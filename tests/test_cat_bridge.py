"""CAT bridge tests: a real PTY on one side, a local TCP server on the other."""

import os
import select
import socket
import sys
import time
import unittest
from unittest import mock

from PyQt5.QtCore import QCoreApplication, Qt

import network.cat_bridge as cat_module
from network.cat_bridge import CatBridge

app = QCoreApplication.instance() or QCoreApplication(sys.argv)


def wait_for(cond, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return False


@unittest.skipUnless(sys.platform.startswith("linux"), "PTY bridge is Linux-only")
class CatBridgeTest(unittest.TestCase):
    def setUp(self) -> None:
        for name, value in (("RECONNECT_DELAY", 0.3), ("POLL_INTERVAL", 0.05)):
            patcher = mock.patch.object(cat_module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.server = socket.socket()
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(4)
        self.server.settimeout(3)
        self.addCleanup(self.server.close)

        self.statuses = []
        self.bridge = CatBridge("127.0.0.1", self.server.getsockname()[1])
        self.bridge.status_changed.connect(
            lambda ok, msg, port: self.statuses.append((ok, msg)), Qt.DirectConnection
        )
        self.bridge.start()
        self.addCleanup(self._stop_bridge)

        self.assertTrue(wait_for(lambda: self.bridge.slave_name))
        self.slave = os.open(self.bridge.slave_name, os.O_RDWR | os.O_NOCTTY)
        self.addCleanup(os.close, self.slave)

    def _stop_bridge(self) -> None:
        self.bridge.stop()
        self.assertTrue(self.bridge.wait(2000), "bridge thread did not stop")

    def _accept(self) -> socket.socket:
        conn, _ = self.server.accept()
        conn.settimeout(2)
        self.addCleanup(conn.close)
        return conn

    def _read_slave(self, n: int, timeout: float = 2.0) -> bytes:
        out = b""
        deadline = time.monotonic() + timeout
        while len(out) < n and time.monotonic() < deadline:
            r, _, _ = select.select([self.slave], [], [], 0.05)
            if r:
                out += os.read(self.slave, n - len(out))
        return out

    def test_bidirectional_and_reconnect_after_server_close(self) -> None:
        conn = self._accept()
        os.write(self.slave, b"FA;")
        self.assertEqual(conn.recv(64), b"FA;")
        conn.sendall(b"FA00014074000;")
        self.assertEqual(self._read_slave(14), b"FA00014074000;")

        # The server drops us (web panel restart / IP not allowed)
        conn.close()
        self.assertTrue(wait_for(lambda: any("server closed" in m for _, m in self.statuses)))
        os.write(self.slave, b"TX1;")  # sent while offline: must not reach the radio

        conn2 = self._accept()
        self.assertTrue(wait_for(lambda: self.statuses[-1][0]))
        os.write(self.slave, b"IF;")
        self.assertEqual(conn2.recv(64), b"IF;")
        conn2.sendall(b"ID019;")
        self.assertEqual(self._read_slave(6), b"ID019;")

    def test_binary_civ_frames_pass_unchanged(self) -> None:
        conn = self._accept()
        # CI-V frame with bytes a cooked TTY would translate or act on
        frame = bytes([0xFE, 0xFE, 0x94, 0xE0, 0x03, 0x0D, 0x7F, 0x0A, 0x11, 0x13, 0xFD])
        os.write(self.slave, frame)
        got = b""
        while len(got) < len(frame):
            got += conn.recv(64)
        self.assertEqual(got, frame)
        conn.sendall(frame)
        self.assertEqual(self._read_slave(len(frame)), frame)
        # nothing echoed back towards the radio
        conn.settimeout(0.3)
        with self.assertRaises(socket.timeout):
            conn.recv(64)

    def test_unread_pty_does_not_block_bridge(self) -> None:
        conn = self._accept()
        # Nobody reads the PTY: the replies must be dropped, not hang the thread
        conn.sendall(b"X" * 200_000)
        time.sleep(0.3)
        os.write(self.slave, b"FA;")
        self.assertEqual(conn.recv(64), b"FA;")

    def test_stops_while_server_unreachable(self) -> None:
        self._accept().close()
        self.server.close()
        self.assertTrue(wait_for(lambda: any("reconnecting" in m for _, m in self.statuses)))
        started = time.monotonic()
        self.bridge.stop()
        self.assertTrue(self.bridge.wait(2000))
        self.assertLess(time.monotonic() - started, 1.0)


if __name__ == "__main__":
    unittest.main()
