import subprocess
from typing import Optional

from audio.backend import (
    find_gst_launch,
    get_gst_env,
    get_gst_popen_kwargs,
    get_gst_sink,
)
from network.constants import AUDIO_PORT


class AudioRX:
    def __init__(self) -> None:
        self.proc: Optional[subprocess.Popen] = None

    def start(self, ip: str, device: str) -> None:
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
            "!",
            *get_gst_sink(device),
        ]

        self.proc = subprocess.Popen(cmd, env=get_gst_env(), **get_gst_popen_kwargs())

    def stop(self) -> None:
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=5)
            self.proc = None
