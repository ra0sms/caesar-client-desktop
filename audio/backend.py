import os
import re
import shutil
import signal
import subprocess
import sys
import threading
from collections import deque
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


# ─── GStreamer process ────────────────────────────────────────────────────────

# "/GstPipeline:pipeline0/GstUDPSrc:udpsrc0" -> "udpsrc0"
_GST_ELEMENT_PATH = re.compile(r"/GstPipeline:\w+(?:/Gst\w+:\w+)*/Gst\w+:(\w+)")

STDERR_TAIL_LINES = 20


def summarize_gst_error(lines: List[str], returncode: int) -> str:
    """One-line description of why gst-launch exited, for the UI.

    gst-launch prints the error first and debug details after it (in the
    user's locale), so the first line is the one worth showing.
    """
    for line in lines:
        if line.strip():
            return _GST_ELEMENT_PATH.sub(r"\1", line.strip())
    return f"gst-launch-1.0 exited with code {returncode}"


class GstProcess:
    """A running gst-launch-1.0 pipeline.

    Its stderr is read on a background thread (keeping only the last
    lines), so the pipe can't fill up and an exit can be explained.
    """

    def __init__(
        self, args: List[str], stdout: Optional[int] = None, eos_on_interrupt: bool = False
    ) -> None:
        # -e: on SIGINT send EOS through the pipeline before exiting, so
        # muxers like wavenc can finish the file (see finish()).
        flags = ["-q", "-e"] if eos_on_interrupt else ["-q"]
        self.proc = subprocess.Popen(
            [find_gst_launch(), *flags, *args],
            env=get_gst_env(),
            stdout=stdout,
            stderr=subprocess.PIPE,
            **get_gst_popen_kwargs(),
        )
        self._stderr: deque = deque(maxlen=STDERR_TAIL_LINES)
        self._stderr_reader = threading.Thread(
            target=self._read_stderr, daemon=True, name="gst-stderr"
        )
        self._stderr_reader.start()

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        for raw in self.proc.stderr:
            line = raw.decode(errors="replace").rstrip()
            if line:
                self._stderr.append(line)

    @property
    def stdout(self):
        return self.proc.stdout

    def failure(self) -> Optional[str]:
        """None while running; otherwise why the pipeline stopped."""
        returncode = self.proc.poll()
        if returncode is None:
            return None
        self._stderr_reader.join(timeout=0.5)
        return summarize_gst_error(list(self._stderr), returncode)

    def stderr_text(self) -> str:
        return "\n".join(self._stderr)

    @property
    def finished_ok(self) -> bool:
        """The pipeline ran to its end (EOS) and exited without error."""
        return self.proc.poll() == 0

    def finish(self, timeout: float = 3.0) -> None:
        """Stop a pipeline started with eos_on_interrupt=True cleanly:
        SIGINT makes gst-launch push EOS, so files get finalized."""
        if self.proc.poll() is None and os.name == "posix":
            self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                pass
        self.stop()

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self._stderr_reader.join(timeout=1)


# ─── GStreamer pipeline elements ──────────────────────────────────────────────


def _device_arg(device: str) -> List[str]:
    # An empty "device=" breaks the pipeline; no device means the default one
    return [f"device={device}"] if device else []


def get_gst_src(device: str) -> List[str]:
    """GStreamer audio source element + device arg for the current platform."""
    if _is_windows():
        # WASAPI requires a system device GUID — use system default for now
        return ["wasapisrc"]
    elif _is_macos():
        return ["osxaudiosrc", *_device_arg(device)]
    else:
        return ["pulsesrc", *_device_arg(device)]


def get_gst_sink(device: str) -> List[str]:
    """GStreamer audio sink element + device arg for the current platform."""
    if _is_windows():
        # WASAPI requires a system device GUID — use system default for now
        return ["wasapisink"]
    elif _is_macos():
        return ["osxaudiosink", *_device_arg(device)]
    else:
        return ["pulsesink", *_device_arg(device)]


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


# ─── PulseAudio mic gating (Linux only) ──────────────────────────────────────


def supports_mic_gating() -> bool:
    """Whether the TX capture stream can be muted while not transmitting."""
    return _is_linux()


def _pactl(*args: str) -> subprocess.CompletedProcess:
    env = get_gst_env()
    env["LC_ALL"] = "C"  # parse "Source Output #N", not a translation of it
    return subprocess.run(
        ["pactl", *args], env=env, capture_output=True, text=True, timeout=2
    )


def find_source_output(pid: int) -> Optional[str]:
    """Index of the capture stream (source output) opened by process ``pid``."""
    try:
        output = _pactl("list", "source-outputs").stdout
    except Exception:
        return None

    index: Optional[str] = None
    for line in output.splitlines():
        line = line.strip()
        header = re.match(r"Source Output #(\d+)", line)
        if header:
            index = header.group(1)
            continue
        owner = re.match(r'application\.process\.id = "(\d+)"', line)
        if owner and index is not None and int(owner.group(1)) == pid:
            return index
    return None


def set_source_output_mute(index: str, mute: bool) -> bool:
    """Mute/unmute one capture stream (not the device: other apps keep the mic)."""
    try:
        return _pactl("set-source-output-mute", index, "1" if mute else "0").returncode == 0
    except Exception:
        return False


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
