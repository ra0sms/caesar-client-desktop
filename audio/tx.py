from typing import Optional

from audio.backend import GstProcess, get_gst_src
from network.constants import AUDIO_PORT


class AudioTX:
    def __init__(self) -> None:
        self.gst: Optional[GstProcess] = None

    def start(self, ip: str, device: str) -> None:
        if self.gst:
            return

        args = [
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

        self.gst = GstProcess(args)

    def failure(self) -> Optional[str]:
        """None while streaming (or stopped on purpose), else the error."""
        return self.gst.failure() if self.gst else None

    def stop(self) -> None:
        if self.gst:
            self.gst.stop()
            self.gst = None
