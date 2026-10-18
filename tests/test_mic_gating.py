"""PTT mic gating: pactl parsing, the gating worker, session wiring."""

import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

from PyQt5.QtCore import QCoreApplication

import audio.backend as backend
import audio.tx as tx_module
from audio.tx import AudioTX
from gui.session_manager import SessionManager

app = QCoreApplication.instance() or QCoreApplication(sys.argv)

PACTL_SOURCE_OUTPUTS = """Source Output #41
\tDriver: protocol-native.c
\tMute: no
\tProperties:
\t\tapplication.name = "Firefox"
\t\tapplication.process.id = "1111"
Source Output #42
\tDriver: protocol-native.c
\tMute: no
\tProperties:
\t\tapplication.name = "gst-launch-1.0"
\t\tapplication.process.id = "2222"
"""


class FindSourceOutputTest(unittest.TestCase):
    def test_matches_stream_by_pid(self) -> None:
        result = subprocess.CompletedProcess([], 0, stdout=PACTL_SOURCE_OUTPUTS, stderr="")
        with mock.patch.object(backend, "_pactl", return_value=result):
            self.assertEqual(backend.find_source_output(2222), "42")
            self.assertEqual(backend.find_source_output(1111), "41")
            self.assertIsNone(backend.find_source_output(3333))


class StreamNameTest(unittest.TestCase):
    def test_tx_stream_has_its_own_name_on_linux(self) -> None:
        # The sound server restores mute per application name: the gated TX
        # stream must not share "gst-launch-1.0" with other capture streams.
        with mock.patch.object(backend, "_is_windows", lambda: False), \
                mock.patch.object(backend, "_is_macos", lambda: False):
            self.assertEqual(
                backend.get_gst_src("mic", tx_module.TX_CLIENT_NAME),
                ["pulsesrc", "device=mic", "client-name=caesar-tx"],
            )
            self.assertEqual(backend.get_gst_src(""), ["pulsesrc"])


class FakeGst:
    def __init__(self) -> None:
        self.proc = mock.Mock(pid=2222)
        self.proc.poll.return_value = None

    def stop(self) -> None:
        self.proc.poll.return_value = 0

    def failure(self):
        return None


class GatingWorkerTest(unittest.TestCase):
    def _start(self, stream_index):
        self.calls = []
        self.lock = threading.Lock()

        def fake_mute(index, mute):
            with self.lock:
                self.calls.append((index, mute))
            return True

        patches = [
            mock.patch.object(tx_module, "GstProcess", lambda args: FakeGst()),
            mock.patch.object(tx_module, "supports_mic_gating", lambda: True),
            mock.patch.object(tx_module, "find_source_output", lambda pid: stream_index),
            mock.patch.object(tx_module, "set_source_output_mute", fake_mute),
            mock.patch.object(tx_module, "MIC_STREAM_LOOKUP_TIMEOUT", 0.2),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        tx = AudioTX()
        tx.start("127.0.0.1", "mic")
        self.addCleanup(tx.stop)
        return tx

    def _wait_last(self, expected, timeout=1.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                if self.calls and self.calls[-1] == expected:
                    return True
            time.sleep(0.005)
        return False

    def test_muted_after_start_and_follows_ptt(self) -> None:
        tx = self._start("42")
        self.assertTrue(self._wait_last(("42", True)), self.calls)
        tx.set_mic(True)
        self.assertTrue(self._wait_last(("42", False)), self.calls)
        tx.set_mic(False)
        self.assertTrue(self._wait_last(("42", True)), self.calls)

    def test_final_state_wins_after_rapid_toggling(self) -> None:
        tx = self._start("42")
        self.assertTrue(self._wait_last(("42", True)))
        for _ in range(50):
            tx.set_mic(True)
            tx.set_mic(False)
        tx.set_mic(True)
        self.assertTrue(self._wait_last(("42", False)), self.calls[-3:])

    def test_stream_not_found_leaves_mic_open_with_warning(self) -> None:
        tx = self._start(None)
        deadline = time.monotonic() + 1
        while not tx.gating_warning and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIn("Mic stays open", tx.gating_warning)
        self.assertEqual(self.calls, [])

    def test_stop_ends_worker(self) -> None:
        tx = self._start("42")
        thread = tx._gate_thread
        tx.stop()
        self.assertFalse(thread.is_alive())


class SessionKeyingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.order = []
        self.tx = mock.Mock()
        self.tx.set_mic.side_effect = lambda on: self.order.append(("mic", on))
        self.ptt = mock.Mock()
        self.ptt.on.side_effect = lambda ip: self.order.append(("ptt", True))
        self.ptt.off.side_effect = lambda ip: self.order.append(("ptt", False))
        self.session = SessionManager(mock.Mock(), self.tx, self.ptt, mock.Mock(), mock.Mock(), mock.Mock())
        self.session.connected = True

    def test_button_ptt_opens_mic_and_mutes_after_release(self) -> None:
        self.session.ptt_on()
        self.session.ptt_off()
        self.assertEqual(self.order, [("mic", True), ("ptt", True), ("ptt", False), ("mic", False)])

    def test_footswitch_gates_mic_too(self) -> None:
        self.session.on_footswitch(True)
        self.session.on_footswitch(False)
        self.assertEqual(self.order, [("mic", True), ("ptt", True), ("ptt", False), ("mic", False)])


if __name__ == "__main__":
    unittest.main()
