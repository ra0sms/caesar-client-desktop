import subprocess

from audio.backend import (
    find_gst_launch,
    get_gst_env,
    get_gst_popen_kwargs,
    get_gst_sink,
)


class AudioRX:
    def __init__(self):
        self.proc = None

    def start(self, ip, device):
        if self.proc:
            return

        cmd = [
            find_gst_launch(),
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
            *get_gst_sink(device),
        ]

        self.proc = subprocess.Popen(cmd, env=get_gst_env(), **get_gst_popen_kwargs())

    def stop(self):
        if self.proc:
            self.proc.kill()
            self.proc.wait()
            self.proc = None
