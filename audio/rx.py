import os
import subprocess


class AudioRX:
    def __init__(self):
        self.proc = None

    def start(self, ip, device):
        if self.proc:
            return

        cmd = [
            "gst-launch-1.0",
            "-q",
            "udpsrc",
            "port=5000",
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
            "pulsesink",
            f"device={device}",
        ]

        env = os.environ.copy()
        env.pop("LD_LIBRARY_PATH", None)

        self.proc = subprocess.Popen(cmd, env=env)

    def stop(self):
        if self.proc:
            self.proc.kill()
            self.proc.wait()
            self.proc = None
