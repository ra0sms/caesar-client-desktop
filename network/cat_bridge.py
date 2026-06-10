import os
import pty
import select
import socket
import time

from PyQt5.QtCore import QThread, pyqtSignal


class CatBridge(QThread):
    # ok, message, port_name
    status_changed = pyqtSignal(bool, str, str)

    def __init__(self, host, port):

        super().__init__()

        self.host = host
        self.port = port

        self.master_fd = None
        self.slave_fd = None
        self.slave_name = None

        self.sock = None
        self.running = False

    # ---------------- PTY ----------------

    def create_pty(self):

        self.master_fd, self.slave_fd = pty.openpty()
        self.slave_name = os.ttyname(self.slave_fd)

        print(f"[CAT] Virtual port: {self.slave_name}")

        return self.slave_name

    # ---------------- STATUS ----------------

    def emit_status(self, ok, msg):

        self.status_changed.emit(ok, msg, self.slave_name or "")

    # ---------------- TCP ----------------

    def connect_tcp(self):

        while self.running:
            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.settimeout(2)
                self.sock.connect((self.host, self.port))
                self.sock.setblocking(False)

                self.emit_status(True, f"connected {self.host}:{self.port}")
                print("[CAT] TCP connected")

                return

            except Exception as e:
                self.emit_status(False, f"reconnecting... {e}")
                time.sleep(2)

    # ---------------- MAIN LOOP ----------------

    def run(self):

        self.running = True

        self.create_pty()
        self.connect_tcp()

        while self.running:
            # ---------------- FLRig → Radio ----------------

            try:
                r, _, _ = select.select([self.master_fd], [], [], 0.1)

                if self.master_fd in r:
                    data = os.read(self.master_fd, 1024)

                    if data and self.sock:
                        self.sock.sendall(data)

            except OSError:
                break
            except Exception as e:
                self.emit_status(False, f"PTY error: {e}")

            # ---------------- Radio → FLRig ----------------

            try:
                if self.sock:
                    try:
                        resp = self.sock.recv(1024)

                        if resp:
                            os.write(self.master_fd, resp)

                    except BlockingIOError:
                        pass

            except Exception as e:
                self.emit_status(False, f"TCP error: {e}")
                self.connect_tcp()

        self.cleanup()

    # ---------------- STOP ----------------

    def stop(self):

        self.running = False

        self.emit_status(False, "stopping")

    # ---------------- CLEANUP ----------------

    def cleanup(self):

        try:
            if self.sock:
                self.sock.shutdown(socket.SHUT_RDWR)
                self.sock.close()
        except:
            pass

        self.sock = None

        try:
            if self.master_fd:
                os.close(self.master_fd)
        except:
            pass

        try:
            if self.slave_fd:
                os.close(self.slave_fd)
        except:
            pass

        self.master_fd = None
        self.slave_fd = None

        self.emit_status(False, "stopped")
