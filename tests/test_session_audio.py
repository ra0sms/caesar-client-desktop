"""SessionManager reports a failed audio pipeline once, and clears it."""

import sys
import unittest
from unittest import mock

from PyQt5.QtCore import QCoreApplication

from gui.session_manager import SessionManager

app = QCoreApplication.instance() or QCoreApplication(sys.argv)


class FakeStream:
    def __init__(self) -> None:
        self.error = None
        self.gst = None

    def failure(self):
        return self.error

    def stop(self) -> None:
        pass


class SessionAudioWatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tx, self.rx = FakeStream(), FakeStream()
        self.session = SessionManager(
            self.rx, self.tx, mock.Mock(), mock.Mock(), mock.Mock(), mock.Mock()
        )
        self.errors = []
        self.session.audio_error.connect(self.errors.append)

    def test_failure_reported_once_then_cleared_on_disconnect(self) -> None:
        self.session._check_audio()
        self.assertEqual(self.errors, [])

        self.rx.error = "ERROR: from element udpsrc0: bind failed"
        self.session._check_audio()
        self.session._check_audio()
        self.assertEqual(self.errors, ["RX audio stopped: ERROR: from element udpsrc0: bind failed"])

        with mock.patch("builtins.print"):
            self.session.disconnect()
        self.assertEqual(self.errors[-1], "")


if __name__ == "__main__":
    unittest.main()
