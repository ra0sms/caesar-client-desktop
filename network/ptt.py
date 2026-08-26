import threading
import time
from typing import Optional

import socket

from network.constants import PTT_PORT, PTT_KEEPALIVE_INTERVAL


class PTTClient:
    """PTT UDP client.

    Sends both the legacy 1-byte packet (backward compatibility) and the
    extended 6-byte keepalive packet for the new fail-safe protocol:

      [byte 0]    0x31 ('1') PTT on | 0x30 ('0') PTT off
      [bytes 1-4] sequence number (uint32, big-endian)
      [byte 5]    flags (bit 0 = keepalive packet)

    While PTT is held ON, a periodic keepalive thread keeps sending
    '1'-packets so the server does not force PTT off (fail-safe).
    """

    def __init__(self) -> None:
        self.sock: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self._seq: int = 0
        self._ip: str = ""
        self._keepalive: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    # ---------------- packet helpers ----------------

    def _next_seq(self) -> int:
        with self._lock:
            self._seq = (self._seq + 1) & 0xFFFFFFFF
            return self._seq

    def _ext_packet(self, state: bytes, flags: int) -> bytes:
        return state + self._next_seq().to_bytes(4, "big") + bytes([flags])

    # ---------------- send ----------------

    def on(self, ip: str) -> None:
        self._ip = ip
        self._send(b"1")
        self._send(self._ext_packet(b"\x31", 0x00))
        self._start_keepalive()

    def off(self, ip: str) -> None:
        self._ip = ip
        self._stop_keepalive()
        self._send(b"0")
        self._send(self._ext_packet(b"\x30", 0x00))

    def _send(self, packet: bytes) -> None:
        try:
            self.sock.sendto(packet, (self._ip, PTT_PORT))
        except OSError:
            pass

    # ---------------- keepalive ----------------

    def _start_keepalive(self) -> None:
        self._stop_event.clear()
        if self._keepalive is None or not self._keepalive.is_alive():
            self._keepalive = threading.Thread(target=self._keepalive_loop, daemon=True)
            self._keepalive.start()

    def _stop_keepalive(self) -> None:
        self._stop_event.set()

    def _keepalive_loop(self) -> None:
        while not self._stop_event.is_set():
            self._send(self._ext_packet(b"\x31", 0x01))
            time.sleep(PTT_KEEPALIVE_INTERVAL)
