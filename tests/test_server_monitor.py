"""ServerMonitor against a fake server ping responder."""

import socket
import sys
import threading
import time
import unittest
from unittest import mock

from PyQt5.QtCore import QCoreApplication, Qt

import network.server_monitor as monitor_module
from network.server_monitor import ServerMonitor

app = QCoreApplication.instance() or QCoreApplication(sys.argv)


class FakeServer:
    def __init__(self, reply: bytes) -> None:
        self.reply = reply
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.running = True
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while self.running:
            try:
                data, addr = self.sock.recvfrom(64)
            except socket.timeout:
                continue
            if data == b"PING_REQUEST":
                self.sock.sendto(self.reply, addr)

    def close(self) -> None:
        self.running = False
        self.thread.join()
        self.sock.close()


class ServerMonitorTest(unittest.TestCase):
    def _first_status(self, reply: bytes):
        server = FakeServer(reply)
        self.addCleanup(server.close)
        patcher = mock.patch.object(monitor_module, "PING_PORT", server.port)
        patcher.start()
        self.addCleanup(patcher.stop)

        statuses = []
        monitor = ServerMonitor()
        monitor.status_changed.connect(lambda *a: statuses.append(a), Qt.DirectConnection)
        monitor.set_ip("127.0.0.1")
        monitor.start()
        self.addCleanup(monitor.stop)

        deadline = time.monotonic() + 3
        while not statuses and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(statuses, "no status emitted")
        return statuses[0]

    def test_authorized(self) -> None:
        ok, ping_ms, refused_ip = self._first_status(b"PING_RESPONSE")
        self.assertTrue(ok)
        self.assertGreaterEqual(ping_ms, 0)
        self.assertEqual(refused_ip, "")

    def test_not_allowed_reports_ip_seen_by_server(self) -> None:
        ok, ping_ms, refused_ip = self._first_status(b"PING_UNAUTHORIZED 10.66.66.18")
        self.assertTrue(ok)
        self.assertEqual(refused_ip, "10.66.66.18")

    def test_unknown_reply_is_offline(self) -> None:
        ok, ping_ms, refused_ip = self._first_status(b"SOMETHING_ELSE")
        self.assertFalse(ok)
        self.assertEqual(refused_ip, "")


if __name__ == "__main__":
    unittest.main()
