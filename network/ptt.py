import socket

from network.constants import PTT_PORT


class PTTClient:
    def __init__(self) -> None:
        self.sock: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def on(self, ip: str) -> None:
        self.sock.sendto(b"1", (ip, PTT_PORT))

    def off(self, ip: str) -> None:
        self.sock.sendto(b"0", (ip, PTT_PORT))