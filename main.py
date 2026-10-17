import sys
from PyQt5.QtWidgets import QApplication, QMessageBox

from audio.rx import AudioRX
from audio.tx import AudioTX
from gui.main_window import MainWindow
from gui.session_manager import SessionManager
from morse.decoder import MorseDecoder
from network.ptt import PTTClient
from network.ping_server import PingServer
from network.server_monitor import ServerMonitor

from footswitch import FootswitchThread
from single_instance import acquire_instance_lock, busy_udp_ports


app = QApplication(sys.argv)

# Only one client per machine: a second one can't receive audio or answer
# the server's pings (the UDP ports are taken).
instance_lock = acquire_instance_lock()
if instance_lock is None:
    QMessageBox.warning(None, "CAESAR Client", "CAESAR Client is already running.")
    sys.exit(1)

busy = busy_udp_ports()
if busy:
    QMessageBox.warning(
        None,
        "CAESAR Client",
        f"UDP port(s) {', '.join(map(str, busy))} already in use.\n\n"
        "Another CAESAR Client (possibly an older version) or another "
        "program is running. Close it and start the client again.",
    )
    sys.exit(1)

rx = AudioRX()
tx = AudioTX()
ptt = PTTClient()
footswitch = FootswitchThread()

# Ping responder (5002): older servers (CAESAR, nano-server < 1.2.2) ping
# the client and force PTT off when it doesn't answer
ping_server = PingServer()
ping_server.start()

# client monitor
monitor = ServerMonitor()

# CW (Morse) decoder for the RX audio tap
morse = MorseDecoder()

session = SessionManager(rx, tx, ptt, monitor, footswitch, morse)

window = MainWindow(session)
window.show()

sys.exit(app.exec_())