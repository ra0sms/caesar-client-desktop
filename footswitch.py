import time

import serial
from PyQt5.QtCore import QThread, pyqtSignal


class FootswitchThread(QThread):
    state_changed = pyqtSignal(bool)

    def __init__(self):
        super().__init__()

        self.running = False
        self.port = None

        self.last_state = False

    def start_monitor(self, port):

        if not port:
            return

        if port == "Disabled":
            return

        self.port = port

        if not self.running:
            self.running = True
            self.start()

    def stop_monitor(self):

        self.running = False

        self.wait(2000)

    def run(self):

        ser = None

        while self.running:
            try:
                if ser is None:
                    ser = serial.Serial(self.port, baudrate=9600, timeout=0)

                    self.last_state = ser.cts

                    self.state_changed.emit(self.last_state)

                state = ser.cts

                if state != self.last_state:
                    self.last_state = state

                    self.state_changed.emit(state)

                self.msleep(20)

            except Exception:
                try:
                    if ser:
                        ser.close()
                except Exception:
                    pass

                ser = None

                time.sleep(1)

        try:
            if ser:
                ser.close()
        except Exception:
            pass
