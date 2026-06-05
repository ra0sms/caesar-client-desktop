import socket
import threading

PING_PORT = 5002

MAGIC_REQUEST = b"PING_REQUEST"
MAGIC_RESPONSE = b"PING_RESPONSE"


class PingServer:
    def __init__(self):
        self.running = False
        self.thread = None

    def start(self):

        if self.running:
            return

        self.running = True

        self.thread = threading.Thread(target=self.worker, daemon=True)

        self.thread.start()

    def stop(self):
        self.running = False

    def worker(self):

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        sock.bind(("0.0.0.0", PING_PORT))

        while self.running:
            try:
                data, addr = sock.recvfrom(1024)

                if data == MAGIC_REQUEST:
                    sock.sendto(MAGIC_RESPONSE, addr)

            except Exception:
                pass

        sock.close()
