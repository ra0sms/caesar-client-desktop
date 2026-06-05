from serial.tools import list_ports


def get_serial_ports():

    ports = []

    for p in list_ports.comports():
        ports.append(p.device)

    return ports