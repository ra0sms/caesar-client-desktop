import socket


class PTTClient:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def on(self, ip):
        self.sock.sendto(b"1", (ip, 5001))

    def off(self, ip):
        self.sock.sendto(b"0", (ip, 5001))