import os
import pty
import select
import socket
import termios
import time
import tty
from typing import Optional

from PyQt5.QtCore import QThread, pyqtSignal

from network.constants import CAT_PORT

CONNECT_TIMEOUT = 2.0   # TCP connect attempt
SEND_TIMEOUT = 2.0      # a send stuck this long means the link is dead
RECONNECT_DELAY = 2.0   # pause between connection attempts
POLL_INTERVAL = 0.2     # select() timeout, bounds the reaction to stop()
READ_SIZE = 1024


class CatBridge(QThread):
    # ok, message, port_name
    status_changed = pyqtSignal(bool, str, str)

    def __init__(self, host: str, port: int = CAT_PORT) -> None:
        super().__init__()
        self.host = host
        self.port = port

        self.master_fd: Optional[int] = None
        self.slave_fd: Optional[int] = None
        self.slave_name: Optional[str] = None

        self.sock: Optional[socket.socket] = None
        self.running: bool = False

    # ---------------- PTY ----------------

    def create_pty(self) -> str:
        """Create a PTY pair and configure it."""
        self.master_fd, self.slave_fd = pty.openpty()
        self.slave_name = os.ttyname(self.slave_fd)

        print(f"[CAT] Virtual port created: {self.slave_name}")

        self.setup_pty()

        # Restrict access to the owning user only — the slave otherwise
        # inherits permissions that can allow other local users to read
        # from / write to the radio control port.
        try:
            os.chmod(self.slave_name, 0o600)
            print(f"[CAT] Set permissions 600 on {self.slave_name}")
        except Exception as e:
            print(f"[CAT] Could not set permissions: {e}")

        return self.slave_name

    def setup_pty(self) -> None:
        """Put the PTY in raw 8N1 mode: no echo, no line editing, no
        character translation, no flow control.

        CAT programs usually reconfigure the port when they open it, but
        until then (and after they close it) a cooked PTY would echo the
        radio's replies back to the radio, hold back replies without a
        trailing newline and mangle binary CI-V bytes (0x0D, 0x03, 0x7F).
        """
        try:
            tty.setraw(self.slave_fd, termios.TCSANOW)

            attrs = termios.tcgetattr(self.slave_fd)
            attrs[2] |= termios.CLOCAL | termios.CREAD
            attrs[2] &= ~termios.CRTSCTS
            termios.tcsetattr(self.slave_fd, termios.TCSANOW, attrs)

            print("[CAT] PTY configured")

        except Exception as e:
            print(f"[CAT] Setup error: {e}")

    # ---------------- STATUS ----------------

    def emit_status(self, ok: bool, msg: str) -> None:
        self.status_changed.emit(ok, msg, self.slave_name or "")

    # ---------------- TCP ----------------

    def connect_tcp(self) -> bool:
        """Connect to the server, retrying until connected or stopped.

        Anything the CAT program writes to the PTY while we are offline is
        discarded: replaying stale commands (e.g. a TX command) to the radio
        once the link comes back would be worse than losing them.
        """
        while self.running:
            try:
                sock = socket.create_connection((self.host, self.port), timeout=CONNECT_TIMEOUT)
            except OSError as e:
                self.emit_status(False, f"reconnecting... {e}")
                self._discard_pty_input(RECONNECT_DELAY)
                continue

            # Blocking with a timeout, so sendall() always sends everything
            # (or fails) instead of dropping data on a full socket buffer.
            sock.settimeout(SEND_TIMEOUT)
            self.sock = sock
            self.emit_status(True, f"connected {self.host}:{self.port}")
            print("[CAT] TCP connected")
            return True
        return False

    def disconnect_tcp(self, reason: str) -> None:
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
            print(f"[CAT] TCP disconnected: {reason}")
            self.emit_status(False, f"{reason}, reconnecting...")

    # ---------------- PTY I/O ----------------

    def _discard_pty_input(self, duration: float) -> None:
        """Drain the PTY for ``duration`` seconds (or until stopped)."""
        deadline = time.monotonic() + duration
        while self.running:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            r, _, _ = select.select([self.master_fd], [], [], min(remaining, POLL_INTERVAL))
            if r:
                try:
                    os.read(self.master_fd, READ_SIZE)
                except BlockingIOError:
                    pass

    def _write_pty(self, data: bytes) -> None:
        # The master is non-blocking: with no CAT program reading the PTY
        # its buffer fills up, and a blocking write would hang this thread
        # (and stop()). Excess replies are dropped instead.
        try:
            os.write(self.master_fd, data)
        except BlockingIOError:
            pass

    # ---------------- MAIN LOOP ----------------

    def run(self) -> None:
        self.running = True
        port_name = self.create_pty()
        os.set_blocking(self.master_fd, False)

        print(f"[CAT] 🚀 CAT Bridge is running.")
        print(f"[CAT] 📡 Use this port in FLRig: {port_name}")

        try:
            while self.running:
                if self.sock is None and not self.connect_tcp():
                    break

                r, _, _ = select.select([self.master_fd, self.sock], [], [], POLL_INTERVAL)

                # ---------------- FLRig → Radio (PTY → TCP) ----------------
                if self.master_fd in r:
                    try:
                        data = os.read(self.master_fd, READ_SIZE)
                    except BlockingIOError:
                        data = b""
                    if data:
                        try:
                            self.sock.sendall(data)
                        except OSError as e:
                            self.disconnect_tcp(f"send failed: {e}")
                            continue

                # ---------------- Radio → FLRig (TCP → PTY) ----------------
                if self.sock in r:
                    try:
                        resp = self.sock.recv(READ_SIZE)
                    except OSError as e:
                        self.disconnect_tcp(f"receive failed: {e}")
                        continue
                    if not resp:
                        # The server closes the connection when this IP is not
                        # allowed (client_ip.cfg) or when the web panel restarts.
                        self.disconnect_tcp("server closed connection")
                        self._discard_pty_input(RECONNECT_DELAY)
                        continue
                    self._write_pty(resp)
        except OSError as e:
            print(f"[CAT] PTY error: {e}")
        finally:
            self.cleanup()

    # ---------------- STOP ----------------

    def stop(self) -> None:
        self.running = False
        self.emit_status(False, "stopping")

    # ---------------- CLEANUP ----------------

    def cleanup(self) -> None:
        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.sock.close()
        self.sock = None
        if self.master_fd:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
        if self.slave_fd:
            try:
                os.close(self.slave_fd)
            except OSError:
                pass
        self.master_fd = None
        self.slave_fd = None
        self.emit_status(False, "stopped")
