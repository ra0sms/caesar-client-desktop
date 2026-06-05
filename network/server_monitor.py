import socket
import time
import threading
from PyQt5.QtCore import QObject, pyqtSignal


class ServerMonitor(QObject):

    status_changed = pyqtSignal(bool, int)  # ok + ping ms

    def __init__(self):
        super().__init__()
        self.running = False
        self.server_ip = ""
        self.thread = None

    def set_ip(self, ip):
        self.server_ip = ip

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self.worker, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False

    def worker(self):

        while self.running:

            ok = False
            ping_ms = -1

            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.settimeout(1.0)

                start = time.time()

                sock.sendto(b"PING_REQUEST", (self.server_ip, 5002))
                data, _ = sock.recvfrom(1024)

                end = time.time()

                if data == b"PING_RESPONSE":
                    ok = True
                    ping_ms = int((end - start) * 1000)

                sock.close()

            except:
                pass

            self.status_changed.emit(ok, ping_ms)
            time.sleep(1)