# CAESAR Desktop Client

Desktop client for the [NANO radio communication system](https://github.com/ra0sms/nano-server).  
Transmits and receives audio over UDP using the Opus codec via GStreamer, with PTT (Push-to-Talk) control via network or foot pedal.

Also it can work with the [CAESAR Server](https://github.com/ra0sms/caesar_project) using the same protocol.

![CAESAR Desktop](screenshots/main_window.png)

---

## Server

This client connects to the **NANO Server** — a hardware/software device based on a single-board computer that handles audio routing and PTT control.

👉 [github.com/ra0sms/nano-server](https://github.com/ra0sms/nano-server)

---

## Features

- Half-duplex audio over UDP (Opus / RTP)
- PTT via on-screen button or foot pedal (CTS signal via COM port)
- Server availability monitoring with ping display
- Configurable audio input/output devices
- Automatic CW (Morse code) decoder — decoded text appears in a live
  one-line scrolling ticker ("бегущая строка"), with automatic beat-tone
  detection (300–1250 Hz) and speed estimation. Decoding uses the
  [DeepCW](https://github.com/e04/web-deep-cw-decoder) neural model
  (`morse/deepcw/`, run with `onnxruntime`), which is licensed under
  AGPL-3.0 — see `morse/deepcw/README.md`
- Voice keyer (Linux): record six messages (CQ, call, …) and send them with
  PTT by F1–F6; Esc or PTT stops a message
- Cross-platform: **Linux** and **Windows**

---

## VPN Setup

If you have only one real IP address, you can use the VPN setup, see the [VPN Setup Guide](docs/wireguard-setup-en.md). 

## Download

Ready-to-use binaries are available on the [Releases page](https://github.com/ra0sms/caesar-client-desktop/releases):

| Platform | File |
|---|---|
| Linux x86_64 | `CAESAR_Desktop_vX.X.X_-x86_64.AppImage` |
| Windows x86_64 | `CAESAR_Desktop_vX.X.X_windows_x86_64.exe` |

Since v1.2.0 the release binaries include the AGPL-3.0 licensed DeepCW
model and are distributed under AGPL-3.0 — see [License](#license).

---

## Requirements

### Linux

| Dependency | Purpose |
|---|---|
| Python 3.10+ | Runtime |
| PyQt5 | GUI |
| GStreamer 1.x + plugins | Audio pipeline |
| PulseAudio (`pactl`) | Audio device enumeration |
| pyserial | Foot pedal (COM port) |

Install GStreamer plugins (Fedora/RHEL):
```bash
sudo dnf install gstreamer1 gstreamer1-plugins-base \
     gstreamer1-plugins-good gstreamer1-plugins-bad-free \
     gstreamer1-plugin-opus
```

Install GStreamer plugins (Ubuntu/Debian):
```bash
sudo apt install gstreamer1.0-tools gstreamer1.0-plugins-base \
     gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
     gstreamer1.0-plugins-ugly
```

### Windows

| Dependency | Purpose |
|---|---|
| Python 3.10+ | Runtime |
| PyQt5 | GUI |
| GStreamer 1.x (MSVC build) | Audio pipeline |
| pyserial | Foot pedal (COM port) |

Install GStreamer from the official site: https://gstreamer.freedesktop.org/download/  
Choose the **MSVC 64-bit** runtime and development installer.  
Make sure `C:\gstreamer\1.0\msvc_x86_64\bin` is added to `PATH`.

> **Note:** On Windows, audio uses the system default input/output devices (WASAPI). Per-device selection is not yet supported.

---

## Installation

```bash
git clone https://github.com/ra0sms/caesar-client-desktop.git
cd caesar-client-desktop

python -m venv venv

# Linux
source venv/bin/activate

# Windows
venv\Scripts\activate

pip install -r requirements.txt
```

---

## Running

```bash
python main.py
```

---

## Building

The build scripts use the `venv` with the build tools installed:

```bash
pip install -r requirements-dev.txt
```

### Linux — AppImage

```bash
bash build_appimage.sh
```

Output: `Releases/CAESAR_Desktop_vX.X.X_-x86_64.AppImage`

`linuxdeploy` is downloaded automatically on first build.

### Windows — EXE

```bat
build_windows.bat
```

Output: `Releases\CAESAR_Desktop_vX.X.X_windows_x86_64.exe`

---

## Configuration

Settings are saved automatically to:

| Platform | Path |
|---|---|
| Linux | `~/.caesar-desktop/config.json` |
| Windows | `C:\Users\<user>\.caesar-desktop\config.json` |


Add virtual Audio device for WSJT integration
```bash
pactl load-module module-null-sink sink_name=WSJT_SINK
```
To use this device for WSJT, set the `audio_output` in your config to `WSJT_SINK`.

---

## Voice Keyer

The six voice keyer messages are stored as WAV files
`~/.caesar-desktop/voice/msg1.wav` … `msg6.wav`; slot N is sent with key FN
(F1–F6). The **Folder** button opens this folder.

Each slot has a name, the send button (FN), ▶ to listen to the message
locally (not on the air) and ● to record it. Recording over an existing
message asks for confirmation.

Recordings made in the client are **WAV, PCM 16-bit, 48 kHz, mono**.

You can also put ready-made files there instead of recording them in the
client. The file must be WAV with integer PCM samples (16- or 24-bit). Any
sample rate works, and stereo is mixed down to mono, but 48 kHz mono 16-bit
needs no conversion. 32-bit float WAV is not supported: such a slot shows as
"not recorded". Files you put there are not limited to 30 s (that limit only
applies to recording in the client).

Convert a file with ffmpeg or sox:

```bash
mkdir -p ~/.caesar-desktop/voice
ffmpeg -i cq.mp3 -ac 1 -ar 48000 -c:a pcm_s16le ~/.caesar-desktop/voice/msg1.wav
# or
sox cq.mp3 -r 48000 -c 1 -b 16 ~/.caesar-desktop/voice/msg1.wav
```

The client checks the folder whenever its window becomes active, so a file
dropped there shows up as soon as you switch back to the client.
Message names are stored in `config.json` as `voice_label_1` …
`voice_label_6`.

---

## Network Ports

| Port | Protocol | Purpose |
|---|---|---|
| 5000 | UDP | Audio stream (RTP/Opus) |
| 5001 | UDP | PTT control |
| 5002 | UDP | Ping / heartbeat (both directions: older servers ping the client) |
| 3001 | TCP | CAT (transceiver control) |

---

## Project Structure

```
├── main.py                 # Entry point
├── config.py               # Load/save settings
├── footswitch.py           # Foot pedal (CTS via serial)
├── serial_ports.py         # Serial port enumeration
├── single_instance.py      # One client per machine (lock file + UDP port check)
├── version.txt             # Current version (source of truth)
├── changelog.txt           # Release history
├── build_appimage.sh       # Linux build script
├── build_windows.bat       # Windows build script
├── audio/
│   ├── backend.py          # Cross-platform GStreamer abstraction
│   ├── rx.py               # Audio receive (UDP → speaker)
│   ├── tx.py               # Audio transmit (mic → UDP)
│   └── devices.py          # Device enumeration (delegates to backend)
├── network/
│   ├── ptt.py              # PTT UDP client
│   ├── cat_bridge.py       # CAT bridge (virtual serial port ↔ TCP)
│   ├── ping_server.py      # Ping responder (for older servers)
│   └── server_monitor.py   # Connection status monitor
├── gui/
│   └── main_window.py      # Main application window
├── screenshots/
│   └── main_window.png     # screenshot for README
└── tools/                  # auto-created on first build (not in git)
    └── linuxdeploy-x86_64.AppImage
```

---

## License

The source code is licensed under the MIT License, except for the CW
decoding model in `morse/deepcw/` and `morse/deepcw_engine.py`, which are
licensed under AGPL-3.0-only (from [deepcw-engine](https://github.com/e04/deepcw-engine)).

Built applications (AppImage / Windows executables) include those files
and are therefore distributed under **AGPL-3.0-only**. Their complete
source code is this repository at the matching release tag (e.g.
`v1.2.0`). See [LICENSE](LICENSE) for details.
