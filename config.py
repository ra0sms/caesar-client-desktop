import json
from pathlib import Path
from typing import Any, Dict

CONFIG_DIR: Path = Path.home() / ".caesar-desktop"
CONFIG_DIR.mkdir(exist_ok=True)

CONFIG_FILE: Path = CONFIG_DIR / "config.json"

DEFAULT_CONFIG: Dict[str, str] = {
    "server_ip": "",
    "input_device": "",
    "output_device": "",
    "footswitch_port": "",
}


def load_config() -> Dict[str, str]:

    if not CONFIG_FILE.exists():
        return DEFAULT_CONFIG.copy()

    try:
        with open(CONFIG_FILE, "r") as f:
            data: Dict[str, Any] = json.load(f)
            return {k: str(v) for k, v in data.items()}

    except Exception:
        return DEFAULT_CONFIG.copy()


def save_config(data: Dict[str, str]) -> None:

    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=4)
