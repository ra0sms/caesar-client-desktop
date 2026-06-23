from typing import List

from serial.tools import list_ports


def get_serial_ports() -> List[str]:

    ports: List[str] = []

    for p in list_ports.comports():
        ports.append(p.device)

    return ports