import sys
from PyQt5.QtWidgets import QApplication

from audio.rx import AudioRX
from audio.tx import AudioTX
from network.ptt import PTTClient
from network.ping_server import PingServer
from network.server_monitor import ServerMonitor

from gui.main_window import MainWindow

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

window = MainWindow(rx, tx, ptt, monitor, footswitch)
window.show()

sys.exit(app.exec_())