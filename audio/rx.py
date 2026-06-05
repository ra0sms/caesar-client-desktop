import subprocess


class AudioRX:

    def __init__(self):
        self.proc = None

    def start(self, ip, device):
        if self.proc:
            return

        cmd = f'''
gst-launch-1.0 -q \
udpsrc port=5000 caps="application/x-rtp,payload=96" ! \
rtpjitterbuffer latency=100 ! \
rtpopusdepay ! opusdec ! audioconvert ! pulsesink device="{device}"
'''

        self.proc = subprocess.Popen(
            cmd,
            shell=True
        )

    def stop(self):
        if self.proc:
            self.proc.kill()
            self.proc.wait()
            self.proc = None