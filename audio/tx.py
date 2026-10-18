import threading
import time
from pathlib import Path
from typing import List, Optional

from audio.backend import (
    GstProcess,
    find_source_output,
    get_gst_src,
    set_source_output_mute,
    supports_mic_gating,
)
from audio.voice_keyer import wav_source_args
from network.constants import AUDIO_PORT

# How long to look for our capture stream in PulseAudio after starting
MIC_STREAM_LOOKUP_TIMEOUT = 3.0
MIC_STREAM_LOOKUP_INTERVAL = 0.05


class AudioTX:
    """Microphone → Opus/RTP → server.

    The server plays whatever it receives into the radio, so on Linux the
    capture stream is muted except while PTT is on (set_mic()); otherwise
    room noise would reach the radio all the time and could key it via
    VOX. Muting is done on our own PulseAudio stream by a worker thread,
    so set_mic() never blocks the caller (unmuting takes ~5 ms).
    """

    def __init__(self) -> None:
        self.gst: Optional[GstProcess] = None
        # Set when the stream could not be gated: the mic then stays open
        self.gating_warning: str = ""
        # True while a voice keyer message is sent instead of the mic
        self.sending_message: bool = False

        self._ip: str = ""
        self._device: str = ""

        self._mic_on: bool = False
        self._mic_cond = threading.Condition()
        self._gate_thread: Optional[threading.Thread] = None
        self._gate_stop: Optional[threading.Event] = None

    @staticmethod
    def _sender_args(ip: str) -> List[str]:
        """Raw audio → Opus/RTP → server (shared by the mic and messages)."""
        return [
            "!",
            "audioconvert",
            "!",
            "audioresample",
            "!",
            "audio/x-raw,rate=48000,channels=1",
            "!",
            "opusenc",
            "bitrate=48000",
            "complexity=2",
            "frame-size=20",
            "!",
            "rtpopuspay",
            "!",
            "udpsink",
            f"host={ip}",
            f"port={AUDIO_PORT}",
        ]

    def start(self, ip: str, device: str) -> None:
        if self.gst:
            return

        self._ip, self._device = ip, device
        args = [*get_gst_src(device), *self._sender_args(ip)]

        self.gating_warning = ""
        with self._mic_cond:
            self._mic_on = False
        self.gst = GstProcess(args)

        if supports_mic_gating():
            stop = threading.Event()
            self._gate_stop = stop
            self._gate_thread = threading.Thread(
                target=self._gate_loop, args=(self.gst, stop), daemon=True, name="mic-gate"
            )
            self._gate_thread.start()

    def set_mic(self, on: bool) -> None:
        """Open the mic for transmitting (PTT on) or mute it. Non-blocking."""
        with self._mic_cond:
            self._mic_on = on
            self._mic_cond.notify_all()

    # ---------------- voice keyer ----------------

    def start_message(self, path: Path) -> None:
        """Send a recorded message instead of the mic until it ends.

        The mic pipeline is replaced by one reading the file (udpsink
        paces it in real time), so the server just sees TX audio.
        """
        self.stop()
        self.sending_message = True
        self.gst = GstProcess([*wav_source_args(path), *self._sender_args(self._ip)])

    def message_finished(self) -> bool:
        """The message pipeline has ended (completely or with an error)."""
        return self.sending_message and (self.gst is None or self.gst.proc.poll() is not None)

    def resume_mic(self) -> None:
        """Back to the (muted) microphone after a message."""
        self.stop()
        self.start(self._ip, self._device)

    def failure(self) -> Optional[str]:
        """None while streaming (or stopped on purpose), else the error."""
        if not self.gst:
            return None
        if self.sending_message and self.gst.finished_ok:
            return None  # a message reaching its end is not an error
        return self.gst.failure()

    def stop(self) -> None:
        self.sending_message = False
        if self._gate_stop:
            self._gate_stop.set()
            with self._mic_cond:
                self._mic_cond.notify_all()
        if self.gst:
            self.gst.stop()
            self.gst = None
        if self._gate_thread:
            self._gate_thread.join(timeout=2)
        self._gate_thread = None
        self._gate_stop = None

    # ---------------- mic gating worker ----------------

    def _gate_loop(self, gst: GstProcess, stop: threading.Event) -> None:
        # The stream appears in PulseAudio shortly after gst-launch starts;
        # it stays open until we find it (at most MIC_STREAM_LOOKUP_TIMEOUT).
        deadline = time.monotonic() + MIC_STREAM_LOOKUP_TIMEOUT
        index = None
        while index is None:
            if stop.is_set() or gst.proc.poll() is not None:
                return
            index = find_source_output(gst.proc.pid)
            if index is None:
                if time.monotonic() > deadline:
                    self.gating_warning = (
                        "Mic stays open while not transmitting "
                        "(capture stream not found in PulseAudio)"
                    )
                    print(f"[Audio] {self.gating_warning}")
                    return
                stop.wait(MIC_STREAM_LOOKUP_INTERVAL)

        applied: Optional[bool] = None
        while True:
            with self._mic_cond:
                while not stop.is_set() and self._mic_on == applied:
                    self._mic_cond.wait()
                if stop.is_set():
                    return
                want = self._mic_on
            if not set_source_output_mute(index, mute=not want):
                print(f"[Audio] Could not {'unmute' if want else 'mute'} capture stream #{index}")
            applied = want
