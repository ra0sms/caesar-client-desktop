import time
from threading import Lock
from typing import Optional

import serial
from PyQt5.QtCore import QThread, pyqtSignal


class FootswitchThread(QThread):
    state_changed = pyqtSignal(bool)

    def __init__(self) -> None:
        super().__init__()

        self.running: bool = False
        self.port: Optional[str] = None
        self._lock = Lock()

        self.last_state: bool = False

    def start_monitor(self, port: str) -> None:

        if not port:
            return

        if port == "Disabled":
            return

        with self._lock:
            self.port = port

        if not self.running:
            self.running = True
            self.start()

    def stop_monitor(self) -> None:

        self.running = False

        self.wait(2000)

    def run(self) -> None:

        ser: Optional[serial.Serial] = None

        while self.running:
            try:
                if ser is None:
                    with self._lock:
                        port = self.port

                    ser = serial.Serial(port, baudrate=9600, timeout=0)

                    # КЛЮЧЕВОЙ МОМЕНТ: опускаем RTS сразу после открытия
                    # Небольшая задержка для стабильности драйвера
                    time.sleep(0.05)
                    ser.rts = False

                    # Сохраняем начальное состояние CTS
                    self.last_state = ser.cts
                    self.state_changed.emit(self.last_state)

                state = ser.cts

                if state != self.last_state:
                    self.last_state = state
                    self.state_changed.emit(state)

                self.msleep(50)

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
