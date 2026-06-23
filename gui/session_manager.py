import ipaddress
from typing import Optional

from PyQt5.QtCore import QObject, pyqtSignal

from audio.rx import AudioRX
from audio.tx import AudioTX
from config import load_config, save_config
from footswitch import FootswitchThread
from network.cat_bridge import CatBridge
from network.constants import CAT_PORT
from network.ptt import PTTClient
from network.server_monitor import ServerMonitor


class SessionManager(QObject):
    """Centralized session management for connect/disconnect lifecycle.

    Owns the audio streams, PTT, CAT bridge, footswitch, and server monitor.
    Emits signals so the UI layer can react without being coupled to logic.
    """

    status_message = pyqtSignal(str, str)  # text, stylesheet
    ptt_state_changed = pyqtSignal(bool)
    cat_state_changed = pyqtSignal(bool, str, str)  # ok, msg, port

    def __init__(
        self,
        rx: AudioRX,
        tx: AudioTX,
        ptt: PTTClient,
        monitor: ServerMonitor,
        footswitch: FootswitchThread,
    ) -> None:
        super().__init__()
        self.rx = rx
        self.tx = tx
        self.ptt = ptt
        self.monitor = monitor
        self.footswitch = footswitch

        self.connected: bool = False
        self._ip: str = ""
        self._cat: Optional[CatBridge] = None
        self._cat_status_handler = None

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def ip(self) -> str:
        return self._ip

    @property
    def is_connected(self) -> bool:
        return self.connected

    # ── Connect ─────────────────────────────────────────────────────────────

    def connect(self, ip: str, input_device: str, output_device: str, foot_port: str) -> bool:
        if not ip:
            self.status_message.emit("Enter server IP", "")
            return False

        try:
            ipaddress.ip_address(ip)
        except ValueError:
            self.status_message.emit("Invalid IP address", "color:#ff4444;font-weight:bold;")
            return False

        save_config({
            "server_ip": ip,
            "input_device": input_device,
            "output_device": output_device,
            "footswitch_port": foot_port,
        })

        self._ip = ip
        self.monitor.set_ip(ip)
        self.monitor.start()

        try:
            self.tx.start(ip, input_device)
            self.rx.start(ip, output_device)
        except FileNotFoundError:
            self.status_message.emit("Error: gst-launch-1.0 not found", "color:#ff4444;font-weight:bold;")
            self.monitor.stop()
            return False

        if foot_port and foot_port != "Disabled":
            self.footswitch.start_monitor(foot_port)

        self.connected = True
        self.status_message.emit("Connecting...", "")

        # ── CAT Bridge ──────────────────────────────────────────────────
        self._start_cat()

        return True

    def _start_cat(self) -> None:
        if self._cat:
            if self._cat_status_handler:
                try:
                    self._cat.status_changed.disconnect(self._cat_status_handler)
                except Exception:
                    pass
            self._cat.stop()
            self._cat.wait()

        self._cat = CatBridge(self._ip, CAT_PORT)

        def on_cat_status(ok: bool, msg: str, port: str) -> None:
            self.cat_state_changed.emit(ok, msg, port)

        self._cat_status_handler = on_cat_status
        self._cat.status_changed.connect(self._cat_status_handler)
        self._cat.start()

    # ── Disconnect ──────────────────────────────────────────────────────────

    def disconnect(self) -> None:
        self.connected = False

        try:
            self.ptt.off(self._ip)
        except Exception:
            pass

        self.rx.stop()
        self.tx.stop()
        self.monitor.stop()
        self.footswitch.stop_monitor()

        self.ptt_state_changed.emit(False)

        self.status_message.emit("Disconnected", "color: gray;")

        if self._cat:
            self._cat.stop()
            self._cat.wait()
            self._cat = None

    # ── PTT ─────────────────────────────────────────────────────────────────

    def ptt_on(self) -> None:
        if not self.connected:
            return
        self.ptt.on(self._ip)
        self.ptt_state_changed.emit(True)

    def ptt_off(self) -> None:
        if not self.connected:
            return
        self.ptt.off(self._ip)
        self.ptt_state_changed.emit(False)

    def ptt_toggle(self, state: bool) -> None:
        if state:
            self.ptt_on()
        else:
            self.ptt_off()

    # ── Footswitch ─────────────────────────────────────────────────────────

    def on_footswitch(self, pressed: bool) -> None:
        if not self.connected:
            return
        if pressed:
            self.ptt.on(self._ip)
        else:
            self.ptt.off(self._ip)
        self.ptt_state_changed.emit(pressed)

    # ── Cleanup ─────────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        try:
            self.ptt.off(self._ip)
        except Exception:
            pass

        self.rx.stop()
        self.tx.stop()
        self.monitor.stop()
        self.footswitch.stop_monitor()

        if self._cat:
            self._cat.stop()
            self._cat.wait()