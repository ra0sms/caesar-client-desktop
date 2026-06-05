import subprocess


def get_input_devices():

    devices = []

    try:
        output = subprocess.check_output(
            ["pactl", "list", "short", "sources"], text=True
        )

        for line in output.splitlines():
            parts = line.split()

            if len(parts) >= 2:
                devices.append(parts[1])

    except Exception:
        pass

    return devices


def get_output_devices():

    devices = []

    try:
        output = subprocess.check_output(["pactl", "list", "short", "sinks"], text=True)

        for line in output.splitlines():
            parts = line.split()

            if len(parts) >= 2:
                devices.append(parts[1])

    except Exception:
        pass

    return devices
