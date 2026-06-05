import subprocess


class AudioTX:

    def __init__(self):
        self.proc = None

    def start(self, ip, device):

        if self.proc:
            return

        cmd = f'''
gst-launch-1.0 -q \
pulsesrc device="{device}" ! \
audioconvert ! audioresample ! \
audio/x-raw,rate=48000,channels=1 ! \
opusenc bitrate=48000 complexity=2 frame-size=20 ! \
rtpopuspay ! \
udpsink host={ip} port=5000
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