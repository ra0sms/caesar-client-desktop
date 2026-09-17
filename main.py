import sys
from PyQt5.QtWidgets import QApplication

from audio.rx import AudioRX
from audio.tx import AudioTX
from gui.main_window import MainWindow
from gui.session_manager import SessionManager
from morse.decoder import MorseDecoder
from network.ptt import PTTClient
from network.ping_server import PingServer
from network.server_monitor import ServerMonitor

from footswitch import FootswitchThread


app = QApplication(sys.argv)

rx = AudioRX()
tx = AudioTX()
ptt = PTTClient()
footswitch = FootswitchThread()

# server side ping responder (5002)
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