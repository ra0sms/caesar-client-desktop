import socket
import threading
import time
from typing import Optional

from PyQt5.QtCore import QObject, pyqtSignal

from network.constants import PING_PORT, MAGIC_PING_REQUEST, MAGIC_PING_RESPONSE


class ServerMonitor(QObject):
    status_changed = pyqtSignal(bool, int)  # ok + ping ms

    def __init__(self) -> None:
        super().__init__()
        self.running: bool = False
        self.server_ip: str = ""
        self._thread: Optional[threading.Thread] = None

    def set_ip(self, ip: str) -> None:
        self.server_ip = ip

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self.worker, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.running = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    def worker(self) -> None:

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(1.0)

        while self.running:
            ok = False
            ping_ms = -1

            try:
                start = time.time()

                sock.sendto(MAGIC_PING_REQUEST, (self.server_ip, PING_PORT))
                data, _ = sock.recvfrom(1024)

                end = time.time()

                if data == MAGIC_PING_RESPONSE:
                    ok = True
                    ping_ms = int((end - start) * 1000)

            except socket.timeout:
                pass
            except Exception:
                pass

            self.status_changed.emit(ok, ping_ms)
            time.sleep(1)

        sock.close()
