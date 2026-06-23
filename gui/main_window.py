from pathlib import Path

from PyQt5.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from audio.devices import get_input_devices, get_output_devices
from config import load_config
from gui.session_manager import SessionManager
from serial_ports import get_serial_ports

try:
    APP_VERSION = Path(__file__).resolve().parent.parent.joinpath("version.txt").read_text().strip()
except Exception:
    APP_VERSION = "unknown"


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

        self.cat_port_label = QLabel("PTY: -")
        self.cat_port_label.setStyleSheet("color: gray;")

        self.cat_status = QLabel("CAT: disabled")
        self.cat_status.setStyleSheet("color: gray;")

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

        layout.addLayout(foot_layout)

        layout.addWidget(self.ptt_btn)

        # CAT UI
        layout.addWidget(self.cat_port_label)
        layout.addWidget(self.cat_status)

        self.setLayout(layout)

        self.setWindowTitle(f"CAESAR Client v{APP_VERSION}")
        self.resize(600, 300)

        # ---------------- SIGNALS ----------------

        self.session.status_message.connect(self.on_status_message)
        self.session.ptt_state_changed.connect(self.on_ptt_state)
        self.session.cat_state_changed.connect(self.on_cat_state)
        self.session.footswitch.state_changed.connect(self.footswitch_changed)
        self.session.monitor.status_changed.connect(self.on_server_status)

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
        )

        if ok:
            self.ptt_btn.setEnabled(True)

    # =====================================================
    # DISCONNECT
    # =====================================================

    def disconnect_server(self) -> None:

        self.session.disconnect()

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
    # EXIT
    # =====================================================

    def closeEvent(self, a0) -> None:

        self.session.cleanup()

        if a0:
            a0.accept()
