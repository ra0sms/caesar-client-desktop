import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".caesar-desktop"
CONFIG_DIR.mkdir(exist_ok=True)

CONFIG_FILE = CONFIG_DIR / "config.json"


def load_config():

    if not CONFIG_FILE.exists():

        return {
            "server_ip": "",
            "input_device": "",
            "output_device": "",
            "footswitch_port": ""
        }

    try:

        with open(CONFIG_FILE, "r") as f:
            return json.load(f)

    except Exception:

        return {
            "server_ip": "",
            "input_device": "",
            "output_device": "",
            "footswitch_port": ""
        }


def save_config(data):

    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=4)