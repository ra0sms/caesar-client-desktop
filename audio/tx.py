import subprocess

from audio.backend import (
    find_gst_launch,
    get_gst_env,
    get_gst_popen_kwargs,
    get_gst_src,
)


class AudioTX:
    def __init__(self):
        self.proc = None

    def start(self, ip, device):
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
            "port=5000",
        ]

        self.proc = subprocess.Popen(cmd, env=get_gst_env(), **get_gst_popen_kwargs())

    def stop(self):
        if self.proc:
            self.proc.kill()
            self.proc.wait()
            self.proc = None
