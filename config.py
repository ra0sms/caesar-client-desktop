import json
import os
import tempfile
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
    "cat_enabled": "true",
    "morse_enabled": "false",
    "voice_label_1": "CQ",
    "voice_label_2": "Call",
    "voice_label_3": "73",
}


def load_config() -> Dict[str, str]:
    """Saved settings on top of the defaults (missing keys get defaults)."""
    config = DEFAULT_CONFIG.copy()

    try:
        with open(CONFIG_FILE, "r") as f:
            data: Dict[str, Any] = json.load(f)
        if isinstance(data, dict):
            config.update({k: str(v) for k, v in data.items()})
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[Config] Could not read {CONFIG_FILE}: {e}")

    return config


def save_config(data: Dict[str, str]) -> None:
    """Write atomically: a crash mid-write must not leave a broken file
    (which would silently reset every setting on the next start)."""
    fd, tmp_path = tempfile.mkstemp(dir=CONFIG_DIR, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=4)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, CONFIG_FILE)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
