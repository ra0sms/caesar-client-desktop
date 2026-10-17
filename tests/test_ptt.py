"""PTT client protocol tests (run: python -m unittest discover -s tests -t .)."""

import socket
import threading
import time
import unittest
from unittest import mock

import network.ptt as ptt_module
from network.ptt import PTTClient

ON, OFF = 0x31, 0x30
KEEPALIVE = 0x01


class PTTClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rx.bind(("127.0.0.1", 0))
        self.rx.settimeout(0.5)
        port = self.rx.getsockname()[1]

        patcher = mock.patch.object(ptt_module, "PTT_PORT", port)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.client = PTTClient()
        self.addCleanup(self.client.sock.close)
        self.addCleanup(self.rx.close)

    def _collect_ext(self, quiet: float = 0.1):
        """Extended packets as (cmd, seq, flags) in arrival order."""
        self.rx.settimeout(quiet)
        packets = []
        while True:
            try:
                data, _ = self.rx.recvfrom(64)
            except socket.timeout:
                return packets
            if len(data) == 6:
                packets.append((data[0], int.from_bytes(data[1:5], "big"), data[5]))

    def test_keepalive_sent_while_on_and_stops_after_off(self) -> None:
        with mock.patch.object(ptt_module, "PTT_KEEPALIVE_INTERVAL", 0.02):
            self.client.on("127.0.0.1")
            time.sleep(0.15)
            self.client.off("127.0.0.1")
            time.sleep(0.1)

        packets = self._collect_ext()
        self.assertEqual(packets[0], (ON, 1, 0x00))
        keepalives = [p for p in packets if p[2] & KEEPALIVE]
        self.assertGreaterEqual(len(keepalives), 3)
        self.assertEqual(packets[-1][0], OFF)
        self.assertEqual([p[1] for p in packets], sorted(p[1] for p in packets))

    def test_no_keepalive_after_off_under_rapid_toggling(self) -> None:
        # A tiny interval makes the keepalive thread race with off();
        # a keepalive '1' after OFF would key the transmitter again.
        # Packets are read while sending: the burst would overflow the
        # socket buffer and the kernel would drop most of them.
        packets = []
        sending = threading.Event()
        sending.set()

        def reader() -> None:
            while sending.is_set():
                packets.extend(self._collect_ext(quiet=0.05))

        thread = threading.Thread(target=reader)
        thread.start()
        sent = 0
        original_send = self.client._send

        def counting_send(packet: bytes) -> None:
            nonlocal sent
            if len(packet) == 6:
                sent += 1
            original_send(packet)

        with mock.patch.object(self.client, "_send", counting_send), \
                mock.patch.object(ptt_module, "PTT_KEEPALIVE_INTERVAL", 0.0005):
            for _ in range(300):
                self.client.on("127.0.0.1")
                time.sleep(0.001)
                self.client.off("127.0.0.1")
                time.sleep(0.002)  # PTT is off here: nothing may be sent
        time.sleep(0.1)
        sending.clear()
        thread.join()
        packets.extend(self._collect_ext())

        self.assertEqual(len(packets), sent, "packets were dropped, the check is incomplete")
        state_off = True
        for cmd, seq, flags in packets:
            if cmd == OFF:
                state_off = True
            elif flags & KEEPALIVE:
                self.assertFalse(state_off, f"keepalive '1' after OFF (seq {seq})")
            else:
                state_off = False
        self.assertEqual(packets[-1][0], OFF)

    def test_off_during_keepalive_send_is_not_followed_by_keepalive(self) -> None:
        # Deterministic version of the race: hold the keepalive thread
        # between its "still on?" check and the send, and release PTT then.
        in_keepalive = threading.Event()
        original_packet = self.client._ext_packet

        def slow_packet(state: bytes, flags: int) -> bytes:
            if flags & KEEPALIVE and not in_keepalive.is_set():
                in_keepalive.set()
                time.sleep(0.1)
            return original_packet(state, flags)

        with mock.patch.object(self.client, "_ext_packet", slow_packet), \
                mock.patch.object(ptt_module, "PTT_KEEPALIVE_INTERVAL", 0.01):
            self.client.on("127.0.0.1")
            self.assertTrue(in_keepalive.wait(1))
            self.client.off("127.0.0.1")
            time.sleep(0.2)

        packets = self._collect_ext()
        self.assertEqual(packets[-1][0], OFF, f"packet after OFF: {packets[-1]}")

    def test_repeated_on_starts_single_keepalive_thread(self) -> None:
        self.client.on("127.0.0.1")
        first = self.client._keepalive
        self.client.on("127.0.0.1")
        self.assertIs(self.client._keepalive, first)
        self.client.off("127.0.0.1")
        first.join(timeout=1)
        self.assertFalse(first.is_alive())


if __name__ == "__main__":
    unittest.main()
