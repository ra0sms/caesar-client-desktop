import socket
import threading


class PingResponder:

    def __init__(self):

        self.running = False

    def start(self):

        self.running = True

        threading.Thread(
            target=self.worker,
            daemon=True
        ).start()

    def worker(self):

        sock = socket.socket(
            socket.AF_INET,
            socket.SOCK_DGRAM
        )

        sock.bind(("0.0.0.0", 5002))

        while self.running:

            data, addr = sock.recvfrom(1024)

            if data == b"PING_REQUEST":

                sock.sendto(
                    b"PING_RESPONSE",
                    addr
                )