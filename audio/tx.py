import os
import subprocess


class AudioTX:
    def __init__(self):
        self.proc = None

    def start(self, ip, device):
        if self.proc:
            return

        cmd = [
            "gst-launch-1.0",
            "-q",
            "pulsesrc",
            f"device={device}",
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

        env = os.environ.copy()
        env.pop("LD_LIBRARY_PATH", None)

        self.proc = subprocess.Popen(cmd, env=env)

    def stop(self):
        if self.proc:
            self.proc.kill()
            self.proc.wait()
            self.proc = None
