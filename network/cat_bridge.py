import array
import fcntl
import os
import pty
import select
import socket
import struct
import termios
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
        """Создает PTY и настраивает его"""
        # Создаем обычный PTY
        self.master_fd, self.slave_fd = pty.openpty()
        self.slave_name = os.ttyname(self.slave_fd)

        print(f"[CAT] Virtual port created: {self.slave_name}")

        # Настраиваем порт
        self.setup_pty()

        # Устанавливаем права доступа
        try:
            os.chmod(self.slave_name, 0o666)
            print(f"[CAT] Set permissions 666 on {self.slave_name}")
        except Exception as e:
            print(f"[CAT] Could not set permissions: {e}")

        return self.slave_name

    def setup_pty(self):
        """Настраивает PTY для работы"""
        try:
            attrs = termios.tcgetattr(self.master_fd)

            # Базовые настройки порта
            attrs[2] |= termios.CLOCAL | termios.CREAD
            attrs[2] &= ~termios.CSIZE
            attrs[2] |= termios.CS8
            attrs[2] &= ~termios.PARENB
            attrs[2] &= ~termios.CSTOPB
            attrs[2] &= ~termios.CRTSCTS

            # Отключаем управление потоком
            attrs[0] &= ~(termios.IXON | termios.IXOFF | termios.IXANY)

            # Отключаем эхо и канонический режим
            attrs[3] &= ~(termios.ECHO | termios.ICANON | termios.ISIG)

            # Неблокирующий режим
            attrs[6] = 0  # VMIN = 0
            attrs[5] = 1  # VTIME = 0.1 сек

            termios.tcsetattr(self.master_fd, termios.TCSANOW, attrs)

            # Настройка для slave FD
            attrs_slave = termios.tcgetattr(self.slave_fd)
            attrs_slave[2] |= termios.CLOCAL | termios.CREAD
            attrs_slave[2] &= ~termios.CSIZE
            attrs_slave[2] |= termios.CS8
            attrs_slave[2] &= ~termios.PARENB
            attrs_slave[2] &= ~termios.CSTOPB
            attrs_slave[2] &= ~termios.CRTSCTS
            attrs_slave[0] &= ~(termios.IXON | termios.IXOFF | termios.IXANY)
            attrs_slave[3] &= ~(termios.ECHO | termios.ICANON | termios.ISIG)
            attrs_slave[6] = 0
            attrs_slave[5] = 1
            termios.tcsetattr(self.slave_fd, termios.TCSANOW, attrs_slave)

            print("[CAT] PTY configured")

        except Exception as e:
            print(f"[CAT] Setup error: {e}")

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
        port_name = self.create_pty()
        self.connect_tcp()

        print(f"[CAT] 🚀 CAT Bridge is running.")
        print(f"[CAT] 📡 Use this port in FLRig: {port_name}")

        while self.running:
            # ---------------- FLRig → Radio (PTY → TCP) ----------------
            try:
                r, _, _ = select.select([self.master_fd], [], [], 0.05)

                if self.master_fd in r:
                    data = os.read(self.master_fd, 1024)
                    if data and self.sock:
                        self.sock.sendall(data)

            except OSError:
                break
            except Exception as e:
                pass

            # ---------------- Radio → FLRig (TCP → PTY) ----------------
            try:
                if self.sock:
                    try:
                        resp = self.sock.recv(1024)
                        if resp:
                            os.write(self.master_fd, resp)
                    except BlockingIOError:
                        pass
            except Exception as e:
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
