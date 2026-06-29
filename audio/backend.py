import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional


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


# ─── PulseAudio Null-Sink management (Linux only) ────────────────────────────

# Default null-sink name used by CAESAR Desktop
NULL_SINK_NAME = "WSJT_SINK"
NULL_SINK_DESCRIPTION = "WSJT_SINK"


def null_sink_exists(name: str = NULL_SINK_NAME) -> bool:
    """Check if a PulseAudio null-sink with the given name already exists."""
    if not _is_linux():
        return False
    try:
        output = subprocess.check_output(
            ["pactl", "list", "short", "sinks"], text=True
        )
        return any(name in line for line in output.splitlines())
    except Exception:
        return False


def create_null_sink(name: str = NULL_SINK_NAME, description: str = NULL_SINK_DESCRIPTION) -> Optional[str]:
    """Create a PulseAudio null-sink module.

    Returns the module index (as string) on success, or None on failure.
    The sink will appear as both a sink (output) and a monitor source (input).
    """
    if not _is_linux():
        return None

    if null_sink_exists(name):
        return None  # already exists

    try:
        output = subprocess.check_output(
            [
                "pactl", "load-module", "module-null-sink",
                f"sink_name={name}",
                f"sink_properties=device.description={description}",
            ],
            text=True,
        )
        module_index = output.strip()
        print(f"[Audio] Created null-sink '{name}' (module index: {module_index})")
        return module_index
    except Exception as e:
        print(f"[Audio] Failed to create null-sink '{name}': {e}")
        return None


def remove_null_sink(name: str = NULL_SINK_NAME) -> bool:
    """Remove a PulseAudio null-sink by finding and unloading its module.

    Returns True if removed successfully, False otherwise.
    """
    if not _is_linux():
        return False

    if not null_sink_exists(name):
        return False  # nothing to remove

    try:
        # Find the module index that owns this sink name
        output = subprocess.check_output(
            ["pactl", "list", "short", "modules"], text=True
        )
        for line in output.splitlines():
            # Look for a line like:  <index>\tmodule-null-sink\t...sink_name=WSJT_SINK...
            if "module-null-sink" in line and name in line:
                module_index = line.split("\t")[0]
                subprocess.check_call(
                    ["pactl", "unload-module", module_index],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                print(f"[Audio] Removed null-sink '{name}' (module index: {module_index})")
                return True

        print(f"[Audio] Could not find module index for null-sink '{name}'")
        return False
    except Exception as e:
        print(f"[Audio] Failed to remove null-sink '{name}': {e}")
        return False


# ─── PulseAudio Loopback management (Linux only) ────────────────────────────

# Default loopback name for routing speaker audio to WSJT_SINK
LOOPBACK_NAME = "CAESAR_LOOPBACK"


def loopback_exists(name: str = LOOPBACK_NAME) -> bool:
    """Check if a loopback module exists by looking for module-loopback in modules list."""
    if not _is_linux():
        return False
    try:
        output = subprocess.check_output(
            ["pactl", "list", "short", "modules"], text=True
        )
        return any("module-loopback" in line for line in output.splitlines())
    except Exception:
        return False


def create_loopback(source: str, sink: str, name: str = LOOPBACK_NAME) -> Optional[str]:
    """Create a PulseAudio loopback from source monitor to sink.

    This routes audio from the output device (e.g. speakers) to the null-sink,
    allowing JTDX/WSJT-X to decode audio received from the server.

    Returns the module index on success, or None on failure.
    """
    if not _is_linux():
        return None

    if loopback_exists(name):
        return None  # already exists

    try:
        output = subprocess.check_output(
            [
                "pactl", "load-module", "module-loopback",
                f"source={source}",
                f"sink={sink}",
            ],
            text=True,
        )
        module_index = output.strip()
        print(f"[Audio] Created loopback '{name}' ({source} → {sink}, module: {module_index})")
        return module_index
    except Exception as e:
        print(f"[Audio] Failed to create loopback: {e}")
        return None


def remove_loopback(name: str = LOOPBACK_NAME) -> bool:
    """Remove a PulseAudio loopback module.

    Returns True if removed successfully, False otherwise.
    """
    if not _is_linux():
        return False

    if not loopback_exists(name):
        return False

    try:
        output = subprocess.check_output(
            ["pactl", "list", "short", "modules"], text=True
        )
        for line in output.splitlines():
            if "module-loopback" in line:
                module_index = line.split("\t")[0]
                subprocess.check_call(
                    ["pactl", "unload-module", module_index],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                print(f"[Audio] Removed loopback (module: {module_index})")
                return True
        return False
    except Exception as e:
        print(f"[Audio] Failed to remove loopback: {e}")
        return False
