"""Voice keyer: record up to MESSAGE_COUNT short messages and play them back.

Messages are WAV files (48 kHz mono S16LE) in ~/.caesar-desktop/voice/.
Sending one on the air is done by AudioTX.start_message(); this module
records them from the microphone and plays them to the local output
("Listen") so the operator can check a recording.
"""

import os
import signal
import threading
import time
import wave
from pathlib import Path
from typing import List, Optional

from audio.backend import (
    GstProcess,
    find_source_output,
    get_gst_sink,
    get_gst_src,
    set_source_output_mute,
    supports_mic_gating,
)
from config import CONFIG_DIR

VOICE_DIR: Path = CONFIG_DIR / "voice"
MESSAGE_COUNT = 3
MAX_RECORD_SECONDS = 30
MIN_RECORD_SECONDS = 0.3

WAV_CAPS = "audio/x-raw,format=S16LE,rate=48000,channels=1"

# Name of the recording stream in PulseAudio/PipeWire (see get_gst_src)
RECORDER_CLIENT_NAME = "caesar-voice-keyer"
STREAM_LOOKUP_TIMEOUT = 3.0
STREAM_LOOKUP_INTERVAL = 0.05


def _unmute_own_stream(gst: GstProcess) -> None:
    """Unmute the recording stream once it shows up in the sound server.

    PipeWire/PulseAudio re-apply a mute they remembered for the stream
    name after the stream is created (pulsesrc's mute=false doesn't
    override it), so the stream is unmuted explicitly, like TX gating does.
    """
    deadline = time.monotonic() + STREAM_LOOKUP_TIMEOUT
    while time.monotonic() < deadline and gst.proc.poll() is None:
        index = find_source_output(gst.proc.pid)
        if index is not None:
            set_source_output_mute(index, False)
            return
        time.sleep(STREAM_LOOKUP_INTERVAL)


def message_path(slot: int) -> Path:
    """WAV file of message ``slot`` (1-based)."""
    return VOICE_DIR / f"msg{slot}.wav"


def message_duration(slot: int) -> Optional[float]:
    """Length of a recorded message in seconds, None if there is none."""
    try:
        with wave.open(str(message_path(slot)), "rb") as w:
            frames, rate = w.getnframes(), w.getframerate()
    except (OSError, EOFError, wave.Error):
        return None
    return frames / rate if rate and frames else None


def gst_location(path: Path) -> str:
    """filesrc/filesink location= argument; quoted, since gst-launch joins
    its arguments into one string (a home path may contain spaces)."""
    escaped = path.as_posix().replace("\\", "\\\\").replace('"', '\\"')
    return f'location="{escaped}"'


def wav_source_args(path: Path) -> List[str]:
    """Pipeline head that decodes a message file to raw audio."""
    return ["filesrc", gst_location(path), "!", "wavparse", "!", "audioconvert", "!", "audioresample"]


class VoiceRecorder:
    """Records one message from the microphone into its WAV file.

    Recording goes to a temporary file that replaces the message only
    once it is complete, so a failed recording keeps the old message.
    """

    def __init__(self) -> None:
        self.gst: Optional[GstProcess] = None
        self.slot: int = 0
        self._tmp: Optional[Path] = None

    @property
    def recording(self) -> bool:
        return self.gst is not None

    def start(self, slot: int, device: str, source: Optional[List[str]] = None) -> None:
        """Start recording message ``slot`` from ``device`` (``source``
        replaces the microphone element, for tests)."""
        if self.gst:
            return
        VOICE_DIR.mkdir(parents=True, exist_ok=True)
        self.slot = slot
        self._tmp = VOICE_DIR / f".msg{slot}.recording.wav"
        args = [
            *(source or get_gst_src(device, RECORDER_CLIENT_NAME)),
            "!", "audioconvert", "!", "audioresample", "!", WAV_CAPS,
            "!", "wavenc", "!", "filesink", gst_location(self._tmp),
        ]
        self.gst = GstProcess(args, eos_on_interrupt=True)
        if source is None and supports_mic_gating():
            threading.Thread(
                target=_unmute_own_stream, args=(self.gst,), daemon=True, name="rec-unmute"
            ).start()

    def failure(self) -> Optional[str]:
        """Why recording stopped on its own (e.g. the device failed)."""
        return self.gst.failure() if self.gst else None

    def stop(self) -> Optional[str]:
        """Finish recording; None on success, otherwise an error message."""
        if not self.gst or self._tmp is None:
            return "Not recording"
        gst, tmp, slot = self.gst, self._tmp, self.slot
        self.gst, self._tmp = None, None

        error = gst.failure()  # stopped by itself before we asked: an error
        gst.finish()
        if error is None and not gst.finished_ok:
            if gst.proc.returncode == -signal.SIGINT:
                # Stopped right after starting, before gst-launch could
                # handle SIGINT (no EOS, no file): just too short
                error = "recording is too short"
            else:
                error = gst.failure()

        try:
            with wave.open(str(tmp), "rb") as w:
                seconds = w.getnframes() / w.getframerate()
        except (OSError, EOFError, wave.Error) as e:
            seconds = 0.0
            error = error or f"recording is unreadable: {e}"

        if error is None and seconds < MIN_RECORD_SECONDS:
            error = "recording is too short"
        if error is not None:
            try:
                tmp.unlink()
            except OSError:
                pass
            return error

        os.replace(tmp, message_path(slot))
        return None


class VoicePlayer:
    """Plays a message to the local output device ("Listen")."""

    def __init__(self) -> None:
        self.gst: Optional[GstProcess] = None
        self.slot: int = 0

    @property
    def playing(self) -> bool:
        return self.gst is not None and self.gst.proc.poll() is None

    def play(self, slot: int, device: str) -> None:
        self.stop()
        self.slot = slot
        self.gst = GstProcess([*wav_source_args(message_path(slot)), "!", *get_gst_sink(device)])

    def stop(self) -> None:
        if self.gst:
            self.gst.stop()
            self.gst = None
        self.slot = 0
