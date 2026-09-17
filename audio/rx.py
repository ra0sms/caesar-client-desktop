import os
import subprocess
import threading
from typing import Callable, Optional

from audio.backend import (
    find_gst_launch,
    get_gst_env,
    get_gst_popen_kwargs,
    get_gst_sink,
)
from network.constants import AUDIO_PORT

# Tap branch output caps: 8 kHz mono S16LE (fed to the CW decoder)
TAP_CAPS = "audio/x-raw,format=S16LE,channels=1,rate=8000"
TAP_READ_SIZE = 4096


class AudioRX:
    """Receive RTP/Opus audio from the server and play it back.

    When ``tap=True`` the GStreamer pipeline additionally branches the
    decoded audio off via ``tee`` + ``fdsink`` to stdout as 8 kHz mono
    S16LE PCM. A dedicated daemon thread reads that stream and forwards
    every chunk to ``pcm_callback`` (used by the CW decoder). The main
    playback path is untouched.
    """

    def __init__(self) -> None:
        self.proc: Optional[subprocess.Popen] = None
        self.pcm_callback: Optional[Callable[[bytes], None]] = None
        self._tap_thread: Optional[threading.Thread] = None

    def start(self, ip: str, device: str, tap: bool = False) -> None:
        if self.proc:
            return

        cmd = [
            find_gst_launch(),
            "-q",
            "udpsrc",
            f"port={AUDIO_PORT}",
            "caps=application/x-rtp,payload=96",
            "!",
            "rtpjitterbuffer",
            "latency=100",
            "!",
            "rtpopusdepay",
            "!",
            "opusdec",
            "!",
            "audioconvert",
        ]

        if tap:
            # A queue is required on BOTH tee branches: placing a sink
            # directly on a tee pad deadlocks the pipeline (no data flows
            # to either branch). The playback branch keeps its original
            # elements — only the routing changes.
            cmd += [
                "!",
                "tee",
                "name=tap",
                # branch 1: normal playback (unchanged behaviour)
                "tap.",
                "!",
                "queue",
                "!",
                *get_gst_sink(device),
                # branch 2: raw PCM for the CW decoder
                "tap.",
                "!",
                "queue",
                "!",
                "audioconvert",
                "!",
                "audioresample",
                "!",
                TAP_CAPS,
                "!",
                "fdsink",
                "fd=1",
                "sync=false",
            ]
        else:
            cmd += ["!"] + get_gst_sink(device)

        self.proc = subprocess.Popen(
            cmd,
            env=get_gst_env(),
            stdout=subprocess.PIPE if tap else None,
            **get_gst_popen_kwargs(),
        )

        if tap:
            self._tap_thread = threading.Thread(
                target=self._read_pcm, daemon=True, name="rx-pcm-tap"
            )
            self._tap_thread.start()

    def stop(self) -> None:
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                pass
            self.proc = None

        if self._tap_thread:
            self._tap_thread.join(timeout=2)
            self._tap_thread = None

    # ---------------- PCM tap reader ----------------

    def _read_pcm(self) -> None:
        """Read raw PCM from the pipeline stdout until EOF."""
        if not self.proc or not self.proc.stdout:
            return

        try:
            fd = self.proc.stdout.fileno()
        except Exception:
            return

        while True:
            try:
                data = os.read(fd, TAP_READ_SIZE)
            except OSError:
                break
            if not data:
                break
            if self.pcm_callback:
                try:
                    self.pcm_callback(data)
                except Exception:
                    pass
