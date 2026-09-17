import sys
from pathlib import Path

from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from audio.backend import (
    create_null_sink,
    get_input_devices,
    get_output_devices,
    null_sink_exists,
    remove_null_sink,
    NULL_SINK_NAME,
)
from config import load_config, save_config
from gui.marquee import MarqueeLabel
from gui.session_manager import SessionManager
from serial_ports import get_serial_ports


def _read_version() -> str:
    """Read version from version.txt, works both in source and PyInstaller bundle."""
    try:
        # PyInstaller bundles data files into sys._MEIPASS
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
        return (base / "version.txt").read_text().strip()
    except Exception:
        return "unknown"


APP_VERSION = _read_version()


class MainWindow(QWidget):
    def __init__(self, session: SessionManager):
        super().__init__()

        self.session = session

        cfg = load_config()

        # ---------------- SERVER ----------------

        self.server_ip = QLineEdit(cfg.get("server_ip", ""))

        self.status = QLabel("Disconnected")
        self.status.setStyleSheet("color: gray;")

        # ---------------- AUDIO ----------------

        self.input_combo = QComboBox()
        self.output_combo = QComboBox()

        self.refresh_audio_btn = QPushButton("Refresh")
        self.refresh_audio_btn.clicked.connect(self.refresh_audio_devices)

        self.refresh_audio_devices()

        self.input_combo.setCurrentText(cfg.get("input_device", ""))
        self.output_combo.setCurrentText(cfg.get("output_device", ""))

        # ---------------- WSJT BRIDGE (null-sink) ----------------

        self.wsjt_btn = QPushButton()
        self.wsjt_btn.clicked.connect(self.toggle_null_sink)
        self._update_wsjt_button()

        # ---------------- FOOTSWITCH ----------------

        self.com_combo = QComboBox()

        self.refresh_ports_btn = QPushButton("Refresh")
        self.refresh_ports_btn.clicked.connect(self.refresh_ports)

        self.refresh_ports()

        self.com_combo.setCurrentText(cfg.get("footswitch_port", ""))

        # ---------------- BUTTONS ----------------

        self.connect_btn = QPushButton("Connect")
        self.disconnect_btn = QPushButton("Disconnect")

        self.connect_btn.clicked.connect(self.connect_server)
        self.disconnect_btn.clicked.connect(self.disconnect_server)

        # ---------------- PTT ----------------

        self.ptt_btn = QPushButton("PTT OFF")
        self.ptt_btn.setCheckable(True)
        self.ptt_btn.setEnabled(False)
        self.ptt_btn.toggled.connect(self.ptt_changed)

        self.set_ptt_visual(False)

        # ---------------- CAT STATE ----------------

        self.cat_checkbox = QCheckBox("Enable CAT (transceiver control)")
        self.cat_checkbox.setChecked(cfg.get("cat_enabled", "true") != "false")
        self.cat_checkbox.toggled.connect(self.cat_toggled)

        self.cat_port_label = QLabel("PTY: -")
        self.cat_port_label.setStyleSheet("color: gray;")

        self.cat_status = QLabel("CAT: disabled")
        self.cat_status.setStyleSheet("color: gray;")

        # ---------------- CW DECODER ----------------

        self.morse_check = QCheckBox("CW Decoder")
        self.morse_tone_label = QLabel("Tone: auto")
        self.morse_tone_label.setStyleSheet("color: gray;")
        self.morse_wpm_label = QLabel("WPM: --")
        self.morse_wpm_label.setStyleSheet("color: gray;")
        self.morse_clear_btn = QPushButton("Clear")

        self.morse_marquee = MarqueeLabel()

        self.morse_check.setChecked(cfg.get("morse_enabled", "false") == "true")

        # keep the decoder state in sync with the UI at startup
        self.session.morse.set_enabled(self.morse_check.isChecked())

        # ---------------- LAYOUT ----------------

        top = QHBoxLayout()
        top.addWidget(QLabel("Server IP:"))
        top.addWidget(self.server_ip)

        buttons = QHBoxLayout()
        buttons.addWidget(self.connect_btn)
        buttons.addWidget(self.disconnect_btn)

        foot_layout = QHBoxLayout()
        foot_layout.addWidget(QLabel("PTT Pedal (CTS):"))
        foot_layout.addWidget(self.com_combo)
        foot_layout.addWidget(self.refresh_ports_btn)

        layout = QVBoxLayout()

        layout.addLayout(top)
        layout.addLayout(buttons)

        layout.addWidget(self.status)

        audio_header = QHBoxLayout()
        audio_header.addWidget(QLabel("Audio Devices"))
        audio_header.addStretch()
        audio_header.addWidget(self.refresh_audio_btn)

        layout.addLayout(audio_header)

        layout.addWidget(QLabel("Input"))
        layout.addWidget(self.input_combo)

        layout.addWidget(QLabel("Output"))
        layout.addWidget(self.output_combo)

        # WSJT Bridge
        layout.addWidget(self.wsjt_btn)

        # CW Decoder
        morse_header = QHBoxLayout()
        morse_header.addWidget(self.morse_check)
        morse_header.addWidget(self.morse_tone_label)
        morse_header.addWidget(self.morse_wpm_label)
        morse_header.addStretch()
        morse_header.addWidget(self.morse_clear_btn)

        layout.addLayout(morse_header)
        layout.addWidget(self.morse_marquee)

        layout.addLayout(foot_layout)

        layout.addWidget(self.ptt_btn)

        # CAT UI
        layout.addWidget(self.cat_checkbox)
        layout.addWidget(self.cat_port_label)
        layout.addWidget(self.cat_status)

        self.setLayout(layout)

        self.setWindowTitle(f"CAESAR Client v{APP_VERSION}")
        self.resize(620, 500)

        # ---------------- SIGNALS ----------------

        self.session.status_message.connect(self.on_status_message)
        self.session.ptt_state_changed.connect(self.on_ptt_state)
        self.session.cat_state_changed.connect(self.on_cat_state)
        self.session.footswitch.state_changed.connect(self.footswitch_changed)
        self.session.monitor.status_changed.connect(self.on_server_status)

        self.morse_check.toggled.connect(self.morse_toggled)
        self.morse_clear_btn.clicked.connect(self.morse_clear)
        self.session.morse.text_decoded.connect(self.on_morse_text)
        self.session.morse.status_changed.connect(self.on_morse_status)
        self.session.morse.tone_detected.connect(self.on_morse_tone)

    # =====================================================
    # IP
    # =====================================================

    @property
    def ip(self) -> str:
        return self.server_ip.text().strip()

    # =====================================================
    # PTT VISUAL
    # =====================================================

    def set_ptt_visual(self, active: bool) -> None:

        if active:
            self.ptt_btn.setStyleSheet("""
                QPushButton {
                    background-color: #cc0000;
                    color: white;
                    font-weight: bold;
                    font-size: 16px;
                }
            """)
        else:
            self.ptt_btn.setStyleSheet("""
                QPushButton {
                    background-color: #444444;
                    color: white;
                    font-size: 16px;
                }
            """)

    # =====================================================
    # FOOTSWITCH
    # =====================================================

    def footswitch_changed(self, pressed: bool) -> None:
        self.session.on_footswitch(pressed)

    # =====================================================
    # AUDIO
    # =====================================================

    def refresh_audio_devices(self) -> None:

        input_current = self.input_combo.currentText()
        output_current = self.output_combo.currentText()

        self.input_combo.clear()
        self.output_combo.clear()

        self.input_combo.addItems(get_input_devices())
        self.output_combo.addItems(get_output_devices())

        if self.input_combo.findText(input_current) >= 0:
            self.input_combo.setCurrentText(input_current)

        if self.output_combo.findText(output_current) >= 0:
            self.output_combo.setCurrentText(output_current)

    # =====================================================
    # WSJT BRIDGE (null-sink)
    # =====================================================

    def _update_wsjt_button(self) -> None:
        """Update the WSJT Bridge button text/style based on null-sink state."""
        if null_sink_exists():
            self.wsjt_btn.setText("Remove WSJT Bridge")
            self.wsjt_btn.setStyleSheet("""
                QPushButton {
                    background-color: #cc4400;
                    color: white;
                    font-weight: bold;
                }
            """)
        else:
            self.wsjt_btn.setText("Create WSJT Bridge")
            self.wsjt_btn.setStyleSheet("""
                QPushButton {
                    background-color: #006644;
                    color: white;
                    font-weight: bold;
                }
            """)

    def toggle_null_sink(self) -> None:
        """Create or remove the PulseAudio null-sink for WSJT bridge."""
        if null_sink_exists():
            ok = remove_null_sink()
            if ok:
                self.status.setText("WSJT Bridge removed")
                self.status.setStyleSheet("color: orange;")
        else:
            result = create_null_sink()
            if result is not None:
                self.status.setText(f"WSJT Bridge created (module {result})")
                self.status.setStyleSheet("color: #00ff66; font-weight: bold;")
            else:
                self.status.setText("Failed to create WSJT Bridge (already exists?)")
                self.status.setStyleSheet("color: #ff4444;")

        self._update_wsjt_button()
        self.refresh_audio_devices()

    # =====================================================
    # PORTS
    # =====================================================

    def refresh_ports(self) -> None:

        current = self.com_combo.currentText()

        self.com_combo.clear()
        self.com_combo.addItem("Disabled")

        for port in get_serial_ports():
            self.com_combo.addItem(port)

        idx = self.com_combo.findText(current)
        if idx >= 0:
            self.com_combo.setCurrentIndex(idx)

    # =====================================================
    # CONNECT
    # =====================================================

    def connect_server(self) -> None:

        ok = self.session.connect(
            ip=self.ip,
            input_device=self.input_combo.currentText(),
            output_device=self.output_combo.currentText(),
            foot_port=self.com_combo.currentText(),
            enable_cat=self.cat_checkbox.isChecked(),
            morse_enabled=self.morse_check.isChecked(),
        )

        if ok:
            self.ptt_btn.setEnabled(True)

    # =====================================================
    # DISCONNECT
    # =====================================================

    def disconnect_server(self) -> None:

        self.session.disconnect()

        # Reset CAT labels when disconnected without CAT
        if not self.cat_checkbox.isChecked():
            self.cat_port_label.setText("PTY: -")
            self.cat_port_label.setStyleSheet("color: gray;")
            self.cat_status.setText("CAT: disabled")
            self.cat_status.setStyleSheet("color: gray;")

        self.ptt_btn.blockSignals(True)
        self.ptt_btn.setChecked(False)
        self.ptt_btn.setText("PTT OFF")
        self.ptt_btn.blockSignals(False)

        self.set_ptt_visual(False)

        self.ptt_btn.setEnabled(False)

    # =====================================================
    # PTT BUTTON
    # =====================================================

    def ptt_changed(self, state: bool) -> None:
        self.session.ptt_toggle(state)

    # =====================================================
    # SIGNAL HANDLERS
    # =====================================================

    def on_status_message(self, text: str, stylesheet: str) -> None:
        self.status.setText(text)
        if stylesheet:
            self.status.setStyleSheet(stylesheet)

    def cat_toggled(self, checked: bool) -> None:
        """Update the CAT status label when the checkbox is toggled."""
        if not checked:
            self.cat_port_label.setText("PTY: -")
            self.cat_port_label.setStyleSheet("color: gray;")
            self.cat_status.setText("CAT: disabled")
            self.cat_status.setStyleSheet("color: gray;")

    def on_ptt_state(self, active: bool) -> None:
        self.ptt_btn.blockSignals(True)
        self.ptt_btn.setChecked(active)
        self.ptt_btn.setText("PTT ON" if active else "PTT OFF")
        self.set_ptt_visual(active)
        self.ptt_btn.blockSignals(False)

    def on_cat_state(self, ok: bool, msg: str, port: str) -> None:
        self.cat_port_label.setText(f"PTY: {port if port else '-'}")

        if ok:
            self.cat_status.setText("CAT: ONLINE | " + msg)
            self.cat_status.setStyleSheet("color:#00ff66;font-weight:bold;")
            self.cat_port_label.setStyleSheet("color:#00ff66;")
        else:
            self.cat_status.setText("CAT: " + msg)
            self.cat_status.setStyleSheet("color:#ff4444;font-weight:bold;")
            self.cat_port_label.setStyleSheet("color:#ff4444;")

    def on_server_status(self, ok: bool, ping_ms: int) -> None:

        if not self.session.is_connected:
            return

        if ok:
            self.status.setText(f"ONLINE | {ping_ms} ms")
            self.status.setStyleSheet("color:#00ff66;font-weight:bold;")
        else:
            self.status.setText("OFFLINE")
            self.status.setStyleSheet("color:#ff4444;font-weight:bold;")

    # =====================================================
    # CW DECODER
    # =====================================================

    def morse_toggled(self, checked: bool) -> None:
        self.session.set_morse_enabled(checked)

        cfg = load_config()
        cfg["morse_enabled"] = "true" if checked else "false"
        save_config(cfg)

    def morse_clear(self) -> None:
        self.morse_marquee.clear()

    def on_morse_tone(self, freq_hz: int) -> None:
        self.morse_tone_label.setText(f"Tone: auto ({freq_hz} Hz)")
        self.morse_tone_label.setStyleSheet("color:#00ff66;font-weight:bold;")

    def on_morse_text(self, text: str) -> None:
        self.morse_marquee.append_text(text)

    def on_morse_status(self, active: bool, wpm: int) -> None:
        if wpm > 0:
            self.morse_wpm_label.setText(f"WPM: {wpm}")
        if active:
            self.morse_wpm_label.setStyleSheet("color:#00ff66;font-weight:bold;")
        else:
            self.morse_wpm_label.setStyleSheet("color: gray;")

    # =====================================================
    # EXIT
    # =====================================================

    def closeEvent(self, a0) -> None:

        self.session.cleanup()

        if a0:
            a0.accept()
