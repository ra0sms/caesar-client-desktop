import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def _is_windows() -> bool:
    return sys.platform == "win32"


def _is_macos() -> bool:
    return sys.platform == "darwin"


# ─── GStreamer executable ─────────────────────────────────────────────────────

# Standard GStreamer installation paths on Windows
_GST_WINDOWS_PATHS: List[str] = [
    r"C:\gstreamer\1.0\msvc_x86_64\bin",
    r"C:\gstreamer\1.0\x86_64\bin",
    r"C:\Program Files\GStreamer\1.0\msvc_x86_64\bin",
    r"C:\Program Files\GStreamer\1.0\x86_64\bin",
    r"C:\Program Files (x86)\GStreamer\1.0\msvc_x86_64\bin",
]


def find_gst_launch() -> str:
    """Returns full path to gst-launch-1.0, raises FileNotFoundError if not found."""

    # First try PATH as-is
    found = shutil.which("gst-launch-1.0")
    if found:
        return found

    # On Windows also check known GStreamer install locations
    if _is_windows():
        for folder in _GST_WINDOWS_PATHS:
            candidate = Path(folder) / "gst-launch-1.0.exe"
            if candidate.exists():
                return str(candidate)

    raise FileNotFoundError(
        "gst-launch-1.0 not found. "
        "Please install GStreamer and make sure its bin directory is in PATH."
    )


def get_gst_popen_kwargs() -> dict:
    """Extra kwargs for subprocess.Popen to suppress console windows on Windows."""
    if _is_windows():
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def get_gst_env() -> dict:
    """Returns os.environ copy suitable for spawning GStreamer subprocesses.

    On Linux: removes LD_LIBRARY_PATH injected by AppImage.
    On Windows: adds GStreamer bin dir to PATH if found in known locations.
    """
    env = os.environ.copy()

    if _is_linux():
        env.pop("LD_LIBRARY_PATH", None)

    if _is_windows():
        for folder in _GST_WINDOWS_PATHS:
            if Path(folder).exists() and folder not in env.get("PATH", ""):
                env["PATH"] = folder + os.pathsep + env.get("PATH", "")
                break

    return env


# ─── GStreamer pipeline elements ──────────────────────────────────────────────


def get_gst_src(device: str) -> List[str]:
    """GStreamer audio source element + device arg for the current platform."""
    if _is_windows():
        # WASAPI requires a system device GUID — use system default for now
        return ["wasapisrc"]
    elif _is_macos():
        return ["osxaudiosrc", f"device={device}"]
    else:
        return ["pulsesrc", f"device={device}"]


def get_gst_sink(device: str) -> List[str]:
    """GStreamer audio sink element + device arg for the current platform."""
    if _is_windows():
        # WASAPI requires a system device GUID — use system default for now
        return ["wasapisink"]
    elif _is_macos():
        return ["osxaudiosink", f"device={device}"]
    else:
        return ["pulsesink", f"device={device}"]


# ─── Device enumeration ───────────────────────────────────────────────────────


def get_input_devices() -> List[str]:
    """Returns list of input device names for the current platform."""
    if _is_linux():
        return _pactl_list("sources")
    elif _is_windows():
        # Device selection via WASAPI GUIDs is not yet implemented
        return ["Default"]
    else:
        return _sounddevice_list(input_channels=True)


def get_output_devices() -> List[str]:
    """Returns list of output device names for the current platform."""
    if _is_linux():
        return _pactl_list("sinks")
    elif _is_windows():
        # Device selection via WASAPI GUIDs is not yet implemented
        return ["Default"]
    else:
        return _sounddevice_list(input_channels=False)


# ─── Linux (PulseAudio) ───────────────────────────────────────────────────────


def _pactl_list(kind: str) -> List[str]:
    """Run `pactl list short <kind>` and return device names."""
    devices: List[str] = []
    try:
        output = subprocess.check_output(
            ["pactl", "list", "short", kind], text=True
        )
        for line in output.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                devices.append(parts[1])
    except Exception:
        pass
    return devices


# ─── Windows / macOS (sounddevice / PortAudio) ───────────────────────────────


def _sounddevice_list(input_channels: bool = True) -> List[str]:
    """Query sounddevice for devices with input or output channels."""
    devices: List[str] = []
    try:
        import sounddevice as sd  # type: ignore

        channel_key = "max_input_channels" if input_channels else "max_output_channels"
        for d in sd.query_devices():
            if d[channel_key] > 0:
                devices.append(d["name"])
    except Exception:
        pass
    return devices
