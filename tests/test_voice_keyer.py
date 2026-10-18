"""Voice keyer: recording, sending a message, session keying."""

import math
import shutil
import socket
import struct
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

from PyQt5.QtCore import QCoreApplication

import audio.tx as tx_module
import audio.voice_keyer as vk
import config
import gui.session_manager as session_module
from audio.tx import AudioTX
from gui.session_manager import SessionManager

app = QCoreApplication.instance() or QCoreApplication(sys.argv)
HAVE_GST = shutil.which("gst-launch-1.0") is not None


def write_tone(path: Path, seconds: float) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        n = int(48000 * seconds)
        w.writeframes(b"".join(
            struct.pack("<h", int(8000 * math.sin(2 * math.pi * 700 * i / 48000))) for i in range(n)
        ))


def process_events_until(cond, timeout=3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    return False


class VoiceDirTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="voice dir ")  # space on purpose
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        patcher = mock.patch.object(vk, "VOICE_DIR", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)


class LocationTest(unittest.TestCase):
    def test_quoted_for_gst_launch(self) -> None:
        self.assertEqual(vk.gst_location(Path('/a b/c"d.wav')), 'location="/a b/c\\"d.wav"')


class RecorderUnmuteTest(unittest.TestCase):
    def test_recording_stream_is_unmuted_once_it_appears(self) -> None:
        # The sound server re-applies a remembered mute after the stream is
        # created, so the recorder must unmute its own stream explicitly.
        gst = mock.Mock()
        gst.proc.pid = 4242
        gst.proc.poll.return_value = None
        lookups = iter([None, None, "77"])
        with mock.patch.object(vk, "find_source_output", lambda pid: next(lookups)) as _, \
                mock.patch.object(vk, "set_source_output_mute") as set_mute, \
                mock.patch.object(vk, "STREAM_LOOKUP_INTERVAL", 0.001):
            vk._unmute_own_stream(gst)
        set_mute.assert_called_once_with("77", False)

    def test_recorder_names_its_stream(self) -> None:
        with mock.patch.object(vk, "GstProcess") as gst_cls, \
                mock.patch.object(vk, "supports_mic_gating", lambda: False), \
                mock.patch.object(vk, "get_gst_src", lambda device, name: ["pulsesrc", f"client-name={name}"]), \
                tempfile.TemporaryDirectory() as tmp, mock.patch.object(vk, "VOICE_DIR", Path(tmp)):
            vk.VoiceRecorder().start(1, "mic")
        self.assertIn("client-name=caesar-voice-keyer", gst_cls.call_args[0][0])


@unittest.skipUnless(HAVE_GST, "GStreamer not installed")
class RecorderTest(VoiceDirTest):
    TEST_SOURCE = ["audiotestsrc", "is-live=true", "freq=700"]

    def test_records_and_replaces_message(self) -> None:
        write_tone(vk.message_path(1), 0.5)  # an older recording
        rec = vk.VoiceRecorder()
        rec.start(1, "", source=self.TEST_SOURCE)
        time.sleep(1.2)
        self.assertIsNone(rec.stop())
        self.assertAlmostEqual(vk.message_duration(1), 1.2, delta=0.3)
        self.assertEqual([p.name for p in self.dir.iterdir()], ["msg1.wav"])

    def test_too_short_keeps_old_message(self) -> None:
        write_tone(vk.message_path(2), 0.5)
        rec = vk.VoiceRecorder()
        rec.start(2, "", source=self.TEST_SOURCE)
        self.assertEqual(rec.stop(), "recording is too short")
        self.assertAlmostEqual(vk.message_duration(2), 0.5, delta=0.01)

    def test_broken_source_reports_error_and_leaves_nothing(self) -> None:
        rec = vk.VoiceRecorder()
        rec.start(3, "", source=["nosuch_source_caesar"])
        time.sleep(0.3)
        error = rec.stop()
        self.assertIn("nosuch_source_caesar", error)
        self.assertFalse(vk.message_path(3).exists())
        self.assertEqual(list(self.dir.iterdir()), [])


@unittest.skipUnless(HAVE_GST, "GStreamer not installed")
class SendMessageTest(VoiceDirTest):
    def test_message_streams_in_real_time_and_mic_comes_back(self) -> None:
        write_tone(vk.message_path(1), 1.0)
        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rx.bind(("127.0.0.1", 0))
        rx.settimeout(0.5)
        self.addCleanup(rx.close)
        for name, value in (
            ("AUDIO_PORT", rx.getsockname()[1]),
            ("supports_mic_gating", lambda: False),
            ("get_gst_src", lambda device, client_name="": ["audiotestsrc", "is-live=true"]),
        ):
            patcher = mock.patch.object(tx_module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        tx = AudioTX()
        tx.start("127.0.0.1", "mic")
        self.addCleanup(tx.stop)
        rx.settimeout(0.05)
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:  # drop the (continuous) mic packets
            try:
                rx.recvfrom(2000)
            except socket.timeout:
                pass
        rx.settimeout(0.5)

        started = time.monotonic()
        tx.start_message(vk.message_path(1))
        packets = []
        while not tx.message_finished():
            try:
                rx.recvfrom(2000)
                packets.append(time.monotonic() - started)
            except socket.timeout:
                pass
        self.assertIsNone(tx.failure())
        self.assertGreaterEqual(len(packets), 45)       # 20 ms Opus frames
        self.assertGreater(packets[-1], 0.8)             # paced, not a burst

        tx.resume_mic()
        self.assertFalse(tx.sending_message)
        time.sleep(0.3)
        self.assertIsNone(tx.failure())
        self.assertIsNotNone(rx.recvfrom(2000))          # mic streams again


class FakeTX:
    def __init__(self) -> None:
        self.calls = []
        self.finished = False
        self.error = None
        self.gating_warning = ""
        self.gst = None

    def start_message(self, path) -> None:
        self.calls.append(("message", Path(path).name))
        self.finished = False

    def message_finished(self) -> bool:
        return self.finished

    def failure(self):
        return self.error

    def resume_mic(self) -> None:
        self.calls.append(("mic",))

    def set_mic(self, on: bool) -> None:
        self.calls.append(("set_mic", on))

    def stop(self) -> None:
        self.calls.append(("stop",))


class SessionVoiceTest(VoiceDirTest):
    def setUp(self) -> None:
        super().setUp()
        write_tone(vk.message_path(1), 0.1)
        write_tone(vk.message_path(2), 0.1)
        self.tx = FakeTX()
        self.ptt = mock.Mock()
        self.session = SessionManager(mock.Mock(), self.tx, self.ptt, mock.Mock(), mock.Mock(), mock.Mock())
        self.session.connected = True
        self.ptt_states, self.voice_states, self.errors = [], [], []
        self.session.ptt_state_changed.connect(self.ptt_states.append)
        self.session.voice_state.connect(self.voice_states.append)
        self.session.audio_error.connect(self.errors.append)

    def ptt_calls(self):
        return [c[0] for c in self.ptt.method_calls]

    def test_message_keys_up_and_releases_after_tail(self) -> None:
        self.assertTrue(self.session.send_voice_message(1))
        self.assertEqual(self.ptt_calls(), ["on"])
        self.assertEqual(self.tx.calls, [("message", "msg1.wav")])
        self.assertEqual(self.voice_states, [1])

        self.tx.finished = True
        finished_at = time.monotonic()
        self.assertTrue(process_events_until(lambda: self.voice_states[-1] == 0))
        held = time.monotonic() - finished_at
        self.assertGreaterEqual(held, session_module.VOICE_TAIL_MS / 1000 - 0.02)
        self.assertEqual(self.ptt_calls(), ["on", "off"])
        self.assertEqual(self.tx.calls[-1], ("mic",))
        self.assertEqual(self.ptt_states, [True, False])

    def test_same_key_again_stops_at_once(self) -> None:
        self.session.send_voice_message(1)
        self.assertFalse(self.session.send_voice_message(1))
        self.assertEqual(self.ptt_calls(), ["on", "off"])
        self.assertEqual(self.session.voice_slot, 0)
        self.assertEqual(self.tx.calls[-1], ("mic",))

    def test_other_key_switches_message(self) -> None:
        self.session.send_voice_message(1)
        self.assertTrue(self.session.send_voice_message(2))
        self.assertEqual(self.session.voice_slot, 2)
        self.assertEqual(self.tx.calls[-1], ("message", "msg2.wav"))

    def test_manual_ptt_takes_over_without_unkeying(self) -> None:
        self.session.send_voice_message(1)
        self.session.ptt_on()
        self.assertEqual(self.ptt_calls(), ["on", "on"])  # no OFF in between
        self.assertEqual(self.tx.calls[-2:], [("mic",), ("set_mic", True)])
        self.assertEqual(self.session.voice_slot, 0)

    def test_ptt_button_release_cancels_message(self) -> None:
        self.session.send_voice_message(1)
        self.session.ptt_off()
        self.assertEqual(self.ptt_calls(), ["on", "off"])
        self.assertEqual(self.session.voice_slot, 0)

    def test_failed_message_unkeys_and_reports(self) -> None:
        self.session.send_voice_message(1)
        self.tx.error = "ERROR: from element wavparse0: not a WAV"
        self.tx.finished = True
        with mock.patch("builtins.print"):
            self.assertTrue(process_events_until(lambda: self.session.voice_slot == 0, timeout=1))
        self.assertEqual(self.ptt_calls(), ["on", "off"])
        self.assertIn("Voice message 1 failed", self.errors[-1])

    def test_missing_message_and_disconnected(self) -> None:
        self.assertFalse(self.session.send_voice_message(3))
        self.session.connected = False
        self.assertFalse(self.session.send_voice_message(1))
        self.assertEqual(self.ptt_calls(), [])

    def test_disconnect_during_message_unkeys_without_mic(self) -> None:
        self.session.send_voice_message(1)
        with mock.patch("builtins.print"):
            self.session.disconnect()
        self.assertEqual(self.ptt_calls()[:2], ["on", "off"])
        self.assertNotIn(("mic",), self.tx.calls)


class ConnectKeepsOtherSettingsTest(unittest.TestCase):
    def test_voice_labels_survive_connect(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        with mock.patch.object(config, "CONFIG_DIR", d), mock.patch.object(config, "CONFIG_FILE", d / "config.json"):
            config.save_config({"voice_label_1": "CQ TEST"})
            session = SessionManager(mock.Mock(), mock.Mock(), mock.Mock(), mock.Mock(), mock.Mock(), mock.Mock())
            with mock.patch.object(session, "_start_cat"):
                self.assertTrue(session.connect("10.0.1.15", "mic", "out", "Disabled", enable_cat=False))
            session._audio_timer.stop()
            saved = config.load_config()
        self.assertEqual(saved["voice_label_1"], "CQ TEST")
        self.assertEqual(saved["server_ip"], "10.0.1.15")


if __name__ == "__main__":
    unittest.main()
