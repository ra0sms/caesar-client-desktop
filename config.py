import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".caesar-desktop"
CONFIG_DIR.mkdir(exist_ok=True)

CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "server_ip": "",
    "input_device": "",
    "output_device": "",
    "footswitch_port": "",
}


def load_config():

    if not CONFIG_FILE.exists():
        return DEFAULT_CONFIG.copy()

    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)

    except Exception:
        return DEFAULT_CONFIG.copy()


def save_config(data):

    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=4)
