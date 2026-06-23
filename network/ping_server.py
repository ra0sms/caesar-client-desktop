import socket
import threading
from typing import Optional

from network.constants import PING_PORT, MAGIC_PING_REQUEST, MAGIC_PING_RESPONSE


class PingServer:
    def __init__(self) -> None:
        self.running: bool = False
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:

        if self.running:
            return

        self.running = True

        self.thread = threading.Thread(target=self.worker, daemon=True)

        self.thread.start()

    def stop(self) -> None:
        self.running = False

    def worker(self) -> None:

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(1.0)

        sock.bind(("0.0.0.0", PING_PORT))

        while self.running:
            try:
                data, addr = sock.recvfrom(1024)

                if data == MAGIC_PING_REQUEST:
                    sock.sendto(MAGIC_PING_RESPONSE, addr)

            except socket.timeout:
                continue
            except Exception:
                pass

        sock.close()
