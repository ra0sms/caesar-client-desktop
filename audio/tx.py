import subprocess
from typing import Optional

from audio.backend import (
    find_gst_launch,
    get_gst_env,
    get_gst_popen_kwargs,
    get_gst_src,
)
from network.constants import AUDIO_PORT


class AudioTX:
    def __init__(self) -> None:
        self.proc: Optional[subprocess.Popen] = None

    def start(self, ip: str, device: str) -> None:
        if self.proc:
            return

        cmd = [
            find_gst_launch(),
            "-q",
            *get_gst_src(device),
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

        self.proc = subprocess.Popen(cmd, env=get_gst_env(), **get_gst_popen_kwargs())

    def stop(self) -> None:
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=5)
            self.proc = None
