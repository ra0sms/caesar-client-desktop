import ipaddress
from typing import Optional

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from audio.rx import AudioRX
from audio.tx import AudioTX
from audio.voice_keyer import message_path
from config import load_config, save_config
from footswitch import FootswitchThread
from morse.decoder import MorseDecoder
from network.cat_bridge import CatBridge
from network.constants import CAT_PORT
from network.ptt import PTTClient
from network.server_monitor import ServerMonitor


# Voice keyer timing
VOICE_POLL_MS = 50    # how often the message pipeline is checked for its end
VOICE_TAIL_MS = 300   # PTT stays on this long after the message has been sent


class SessionManager(QObject):
    """Centralized session management for connect/disconnect lifecycle.

    Owns the audio streams, PTT, CAT bridge, footswitch, and server monitor.
    Emits signals so the UI layer can react without being coupled to logic.
    """

    status_message = pyqtSignal(str, str)  # text, stylesheet
    ptt_state_changed = pyqtSignal(bool)
    cat_state_changed = pyqtSignal(bool, str, str)  # ok, msg, port
    audio_error = pyqtSignal(str)  # why an audio pipeline stopped, "" = OK
    voice_state = pyqtSignal(int)  # voice keyer message being sent, 0 = none

    def __init__(
        self,
        rx: AudioRX,
        tx: AudioTX,
        ptt: PTTClient,
        monitor: ServerMonitor,
        footswitch: FootswitchThread,
        morse: MorseDecoder,
    ) -> None:
        super().__init__()
        self.rx = rx
        self.tx = tx
        self.ptt = ptt
        self.monitor = monitor
        self.footswitch = footswitch
        self.morse = morse

        self.connected: bool = False
        self._ip: str = ""
        self._output_device: str = ""
        self._cat: Optional[CatBridge] = None
        self._cat_status_handler = None

        # gst-launch can exit right away (bad device, missing plugin, busy
        # port) or later (device unplugged); check the pipelines every second.
        self._audio_failed: bool = False
        self._gating_reported: bool = False
        self._audio_timer = QTimer(self)
        self._audio_timer.setInterval(1000)
        self._audio_timer.timeout.connect(self._check_audio)

        # Voice keyer: the message pipeline is polled for its end, then PTT
        # is held for VOICE_TAIL_MS so the server's jitter buffer drains
        # (otherwise the last ~100 ms of the message would be cut off).
        self._voice_slot: int = 0
        self._voice_poll = QTimer(self)
        self._voice_poll.setInterval(VOICE_POLL_MS)
        self._voice_poll.timeout.connect(self._check_voice)
        self._voice_tail = QTimer(self)
        self._voice_tail.setSingleShot(True)
        self._voice_tail.timeout.connect(self._end_voice)

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def ip(self) -> str:
        return self._ip

    @property
    def is_connected(self) -> bool:
        return self.connected

    # ── Connect ─────────────────────────────────────────────────────────────

    def connect(
        self,
        ip: str,
        input_device: str,
        output_device: str,
        foot_port: str,
        enable_cat: bool = True,
        morse_enabled: bool = False,
    ) -> bool:
        if not ip:
            self.status_message.emit("Enter server IP", "")
            return False

        try:
            ipaddress.ip_address(ip)
        except ValueError:
            self.status_message.emit("Invalid IP address", "color:#ff4444;font-weight:bold;")
            return False

        # Update only these keys: other settings (e.g. voice keyer labels)
        # live in the same file.
        cfg = load_config()
        cfg.update({
            "server_ip": ip,
            "input_device": input_device,
            "output_device": output_device,
            "footswitch_port": foot_port,
            "cat_enabled": "true" if enable_cat else "false",
            "morse_enabled": "true" if morse_enabled else "false",
        })
        save_config(cfg)

        self._ip = ip
        self._output_device = output_device
        self.monitor.set_ip(ip)
        self.monitor.start()

        try:
            self.tx.start(ip, input_device)
            self.rx.start(ip, output_device, tap=morse_enabled)
        except FileNotFoundError:
            self.status_message.emit("Error: gst-launch-1.0 not found", "color:#ff4444;font-weight:bold;")
            self.monitor.stop()
            return False

        # ── CW decoder ──────────────────────────────────────────────────
        self.rx.pcm_callback = self.morse.feed_pcm
        self.morse.set_enabled(morse_enabled)

        if foot_port and foot_port != "Disabled":
            self.footswitch.start_monitor(foot_port)

        self.connected = True
        self.status_message.emit("Connecting...", "")
        self._reset_audio_watch()
        self._audio_timer.start()

        # ── CAT Bridge ──────────────────────────────────────────────────
        if enable_cat:
            self._start_cat()

        return True

    def set_morse_enabled(self, enabled: bool) -> None:
        """Toggle the CW decoder.

        The RX audio tap branch is only present in the GStreamer pipeline
        when the decoder is enabled, so toggling at runtime restarts RX
        briefly (a few hundred ms of audio gap).
        """
        if not self.connected:
            self.morse.set_enabled(enabled)
            return

        if enabled == self.morse.enabled:
            self.morse.set_enabled(enabled)
            return

        self.status_message.emit("Restarting audio for CW decoder...", "color: orange;")
        self.rx.stop()
        try:
            self.rx.start(self._ip, self._output_device, tap=enabled)
        except FileNotFoundError:
            self.status_message.emit("Error: gst-launch-1.0 not found", "color:#ff4444;font-weight:bold;")
            self.morse.set_enabled(False)
            return
        self._reset_audio_watch()
        self.morse.set_enabled(enabled)

    # ── Audio watchdog ──────────────────────────────────────────────────────

    def _reset_audio_watch(self) -> None:
        self._audio_failed = False
        self._gating_reported = False
        self.audio_error.emit("")

    def _check_audio(self) -> None:
        if self._audio_failed:
            return
        if self.tx.gating_warning and not self._gating_reported:
            self._gating_reported = True
            self.audio_error.emit(self.tx.gating_warning)
        for name, stream in (("TX", self.tx), ("RX", self.rx)):
            error = stream.failure()
            if error:
                self._audio_failed = True
                details = stream.gst.stderr_text() if stream.gst else ""
                print(f"[Audio] {name} pipeline stopped: {error}\n{details}")
                self.audio_error.emit(f"{name} audio stopped: {error}")
                return

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
        self._end_voice(resume_mic=False)
        self.connected = False
        self._audio_timer.stop()
        self._reset_audio_watch()

        try:
            self.ptt.off(self._ip)
        except Exception:
            pass

        self.morse.set_enabled(False)
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
        # Manual PTT takes over from a message without unkeying the radio
        self._end_voice(release_ptt=False)
        self._key(True)
        self.ptt_state_changed.emit(True)

    def ptt_off(self) -> None:
        if not self.connected:
            return
        if self._voice_slot:
            self._end_voice()  # sends PTT off and brings the mic back
            return
        self._key(False)
        self.ptt_state_changed.emit(False)

    def _key(self, on: bool) -> None:
        # The mic is opened/muted on a worker thread (non-blocking), so the
        # PTT packet is not delayed; the mic follows within a few ms, well
        # inside the server's jitter buffer and the radio's TX switching.
        if on:
            self.tx.set_mic(True)
            self.ptt.on(self._ip)
        else:
            self.ptt.off(self._ip)
            self.tx.set_mic(False)

    def ptt_toggle(self, state: bool) -> None:
        if state:
            self.ptt_on()
        else:
            self.ptt_off()

    # ── Footswitch ─────────────────────────────────────────────────────────

    def on_footswitch(self, pressed: bool) -> None:
        if not self.connected:
            return
        if self._voice_slot:
            self._end_voice(release_ptt=not pressed)
        self._key(pressed)
        self.ptt_state_changed.emit(pressed)

    # ── Voice keyer ─────────────────────────────────────────────────────────

    @property
    def voice_slot(self) -> int:
        return self._voice_slot

    def send_voice_message(self, slot: int) -> bool:
        """Key up and send recorded message ``slot``; the same slot again
        cancels it. Returns True if a message started."""
        if not self.connected:
            return False
        if self._voice_slot:
            same = self._voice_slot == slot
            self._end_voice()
            if same:
                return False
        path = message_path(slot)
        if not path.exists():
            self.status_message.emit(f"Message {slot} is not recorded", "color: orange;")
            return False

        self.tx.start_message(path)
        self._voice_slot = slot
        self.ptt.on(self._ip)
        self.ptt_state_changed.emit(True)
        self.voice_state.emit(slot)
        self._voice_poll.start()
        return True

    def cancel_voice(self) -> None:
        self._end_voice()

    def _check_voice(self) -> None:
        if not self._voice_slot or not self.tx.message_finished():
            return
        self._voice_poll.stop()
        error = self.tx.failure()
        if error:
            print(f"[Voice] message {self._voice_slot} failed: {error}")
            self.audio_error.emit(f"Voice message {self._voice_slot} failed: {error}")
            self._end_voice()
        else:
            self._voice_tail.start(VOICE_TAIL_MS)

    def _end_voice(self, resume_mic: bool = True, release_ptt: bool = True) -> None:
        if not self._voice_slot:
            return
        self._voice_poll.stop()
        self._voice_tail.stop()
        self._voice_slot = 0
        if release_ptt:
            self.ptt.off(self._ip)
            self.ptt_state_changed.emit(False)
        if resume_mic:
            try:
                self.tx.resume_mic()
            except FileNotFoundError:
                self.status_message.emit("Error: gst-launch-1.0 not found", "color:#ff4444;font-weight:bold;")
        self.voice_state.emit(0)

    # ── Cleanup ─────────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        self._end_voice(resume_mic=False)
        self._audio_timer.stop()
        try:
            self.ptt.off(self._ip)
        except Exception:
            pass

        self.morse.set_enabled(False)
        self.rx.stop()
        self.tx.stop()
        self.monitor.stop()
        self.footswitch.stop_monitor()

        if self._cat:
            self._cat.stop()
            self._cat.wait()