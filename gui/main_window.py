import ipaddress

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
from config import load_config, save_config
from serial_ports import get_serial_ports

try:
    from version import VERSION as APP_VERSION
except ImportError:
    APP_VERSION = "unknown"


class MainWindow(QWidget):
    def __init__(self, rx, tx, ptt, monitor, footswitch):
        super().__init__()

        self.rx = rx
        self.tx = tx
        self.ptt = ptt
        self.monitor = monitor
        self.footswitch = footswitch

        self.connected = False

        cfg = load_config()

        self.server_ip = QLineEdit(cfg.get("server_ip", ""))
        self.status = QLabel("Disconnected")
        self.status.setStyleSheet("color: gray;")
        self.input_combo = QComboBox()
        self.output_combo = QComboBox()

        self.refresh_audio_btn = QPushButton("Refresh")
        self.refresh_audio_btn.clicked.connect(self.refresh_audio_devices)

        self.refresh_audio_devices()

        self.input_combo.setCurrentText(cfg.get("input_device", ""))

        self.output_combo.setCurrentText(cfg.get("output_device", ""))

        # -------------------------------------------------
        # FOOTSWITCH
        # -------------------------------------------------

        self.com_combo = QComboBox()

        self.refresh_ports_btn = QPushButton("Refresh")

        self.refresh_ports_btn.clicked.connect(self.refresh_ports)

        self.refresh_ports()

        self.com_combo.setCurrentText(cfg.get("footswitch_port", ""))

        self.footswitch.state_changed.connect(self.footswitch_changed)

        # -------------------------------------------------
        # CONNECT BUTTONS
        # -------------------------------------------------

        self.connect_btn = QPushButton("Connect")

        self.disconnect_btn = QPushButton("Disconnect")

        self.connect_btn.clicked.connect(self.connect_server)

        self.disconnect_btn.clicked.connect(self.disconnect_server)

        # -------------------------------------------------
        # PTT BUTTON
        # -------------------------------------------------

        self.ptt_btn = QPushButton("PTT OFF")

        self.ptt_btn.setCheckable(True)
        self.ptt_btn.setEnabled(False)

        self.ptt_btn.toggled.connect(self.ptt_changed)

        self.set_ptt_visual(False)

        # -------------------------------------------------
        # LAYOUT
        # -------------------------------------------------

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

        self.setLayout(layout)

        self.setWindowTitle(f"CAESAR Client v{APP_VERSION}")

        self.resize(600, 300)

        self.monitor.status_changed.connect(self.on_status)

    # =====================================================
    # IP HELPER
    # =====================================================

    @property
    def ip(self):
        return self.server_ip.text().strip()

    # =====================================================
    # PTT VISUAL
    # =====================================================

    def set_ptt_visual(self, active):

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

    def footswitch_changed(self, pressed):

        if not self.connected:
            return

        self.ptt_btn.blockSignals(True)
        self.ptt_btn.setChecked(pressed)

        if pressed:
            self.ptt.on(self.ip)

            self.ptt_btn.setText("PTT ON")

            self.set_ptt_visual(True)

        else:
            self.ptt.off(self.ip)

            self.ptt_btn.setText("PTT OFF")

            self.set_ptt_visual(False)

        self.ptt_btn.blockSignals(False)

    # =====================================================
    # AUDIO DEVICES
    # =====================================================

    def refresh_audio_devices(self):

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

    def refresh_ports(self):

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

    def connect_server(self):

        if not self.ip:
            self.status.setText("Enter server IP")
            return

        try:
            ipaddress.ip_address(self.ip)
        except ValueError:
            self.status.setText("Invalid IP address")
            self.status.setStyleSheet("color:#ff4444;font-weight:bold;")
            return

        input_dev = self.input_combo.currentText()

        output_dev = self.output_combo.currentText()

        foot_port = self.com_combo.currentText()

        save_config(
            {
                "server_ip": self.ip,
                "input_device": input_dev,
                "output_device": output_dev,
                "footswitch_port": foot_port,
            }
        )

        self.monitor.set_ip(self.ip)
        self.monitor.start()

        try:
            self.tx.start(self.ip, input_dev)
            self.rx.start(self.ip, output_dev)
        except FileNotFoundError:
            self.status.setText("Error: gst-launch-1.0 not found")
            self.status.setStyleSheet("color:#ff4444;font-weight:bold;")
            self.monitor.stop()
            self.connected = False
            self.ptt_btn.setEnabled(False)
            return

        if foot_port != "Disabled":
            self.footswitch.start_monitor(foot_port)

        self.connected = True

        self.ptt_btn.setEnabled(True)

        self.status.setText("Connecting...")

    # =====================================================
    # DISCONNECT
    # =====================================================

    def disconnect_server(self):

        self.connected = False

        try:
            self.ptt.off(self.ip)
        except Exception:
            pass

        self.rx.stop()
        self.tx.stop()

        self.monitor.stop()

        self.footswitch.stop_monitor()

        self.ptt_btn.blockSignals(True)

        self.ptt_btn.setChecked(False)
        self.ptt_btn.setText("PTT OFF")

        self.ptt_btn.blockSignals(False)

        self.set_ptt_visual(False)

        self.ptt_btn.setEnabled(False)

        self.status.setText("Disconnected")

        self.status.setStyleSheet("color: gray;")

    # =====================================================
    # PTT BUTTON
    # =====================================================

    def ptt_changed(self, state):

        if not self.connected:
            self.ptt_btn.blockSignals(True)
            self.ptt_btn.setChecked(False)
            self.ptt_btn.blockSignals(False)

            return

        if state:
            self.ptt.on(self.ip)

            self.ptt_btn.setText("PTT ON")

            self.set_ptt_visual(True)

        else:
            self.ptt.off(self.ip)

            self.ptt_btn.setText("PTT OFF")

            self.set_ptt_visual(False)

    # =====================================================
    # SERVER STATUS
    # =====================================================

    def on_status(self, ok, ping_ms):

        if not self.connected:
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

    def closeEvent(self, a0):

        try:
            self.ptt.off(self.ip)
        except Exception:
            pass

        self.rx.stop()
        self.tx.stop()

        self.monitor.stop()
        self.footswitch.stop_monitor()

        if a0 is not None:
            a0.accept()
