"""Voice keyer panel: layout, overwrite confirmation, message folder."""

import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from PyQt5.QtWidgets import QMessageBox

import audio.voice_keyer as vk
import gui.main_window as mw
from gui.session_manager import SessionManager


def write_message(slot: int, seconds: float = 1.0) -> None:
    with wave.open(str(vk.message_path(slot)), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(b"\0\0" * int(48000 * seconds))


class VoicePanelTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        for target in (vk, mw):
            patcher = mock.patch.object(target, "VOICE_DIR", self.dir)
            patcher.start()
            self.addCleanup(patcher.stop)
        # don't touch the real config / audio devices
        for name, value in (("load_config", lambda: {}), ("save_config", lambda cfg: None),
                            ("get_input_devices", lambda: ["mic"]), ("get_output_devices", lambda: ["out"]),
                            ("get_serial_ports", lambda: [])):
            patcher = mock.patch.object(mw, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.session = SessionManager(mock.Mock(), mock.Mock(), mock.Mock(), mock.Mock(), mock.Mock(), mock.Mock())
        self.win = mw.MainWindow(self.session)
        self.recorder = mock.Mock(recording=False, slot=0)
        self.win.voice_recorder = self.recorder

    def test_six_slots_two_per_row(self) -> None:
        self.assertEqual(sorted(self.win.voice_rows), [1, 2, 3, 4, 5, 6])
        grid = self.win.voice_grid
        self.assertEqual((grid.rowCount(), grid.columnCount()), (3, 2))

    def test_empty_slot_records_without_asking(self) -> None:
        with mock.patch.object(QMessageBox, "question") as question:
            self.win.voice_record_clicked(4)
        question.assert_not_called()
        self.recorder.start.assert_called_once()

    def test_overwrite_needs_confirmation(self) -> None:
        write_message(2)
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.No) as question:
            self.win.voice_record_clicked(2)
        question.assert_called_once()
        self.recorder.start.assert_not_called()

        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            self.win.voice_record_clicked(2)
        self.recorder.start.assert_called_once()

    def test_stopping_a_recording_does_not_ask(self) -> None:
        write_message(1)
        self.recorder.recording, self.recorder.slot = True, 1
        self.recorder.stop.return_value = None
        with mock.patch.object(QMessageBox, "question") as question:
            self.win.voice_record_clicked(1)
        question.assert_not_called()
        self.recorder.stop.assert_called_once()


class OpenFolderTest(unittest.TestCase):
    def test_opens_folder_without_appimage_libraries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(vk, "VOICE_DIR", Path(tmp) / "voice"), \
                mock.patch.object(vk.sys, "platform", "linux"), \
                mock.patch.dict(vk.os.environ, {"LD_LIBRARY_PATH": "/tmp/_MEI123"}), \
                mock.patch.object(vk.subprocess, "Popen") as popen:
            self.assertTrue(vk.open_voice_dir())
            self.assertTrue((Path(tmp) / "voice").is_dir())
        args, kwargs = popen.call_args
        self.assertEqual(args[0], ["xdg-open", str(Path(tmp) / "voice")])
        self.assertNotIn("LD_LIBRARY_PATH", kwargs["env"])


if __name__ == "__main__":
    unittest.main()
