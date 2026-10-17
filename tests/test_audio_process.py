"""gst-launch process wrapper: error reporting (needs GStreamer installed)."""

import shutil
import time
import unittest

from audio.backend import GstProcess, summarize_gst_error


class SummarizeTest(unittest.TestCase):
    def test_first_line_with_element_path_shortened(self) -> None:
        lines = [
            "ERROR: from element /GstPipeline:pipeline0/GstUDPSrc:udpsrc0: Could not get/set settings from/on resource.",
            "Additional debug info:",
            "bind failed: Address already in use",
        ]
        self.assertEqual(
            summarize_gst_error(lines, 1),
            "ERROR: from element udpsrc0: Could not get/set settings from/on resource.",
        )

    def test_no_output(self) -> None:
        self.assertEqual(summarize_gst_error([], 255), "gst-launch-1.0 exited with code 255")


@unittest.skipUnless(shutil.which("gst-launch-1.0"), "GStreamer not installed")
class GstProcessTest(unittest.TestCase):
    def _wait_failure(self, gst: GstProcess, timeout: float = 5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            error = gst.failure()
            if error:
                return error
            time.sleep(0.05)
        return None

    def test_running_pipeline_has_no_failure_and_stops(self) -> None:
        gst = GstProcess(["audiotestsrc", "is-live=true", "!", "fakesink"])
        time.sleep(0.5)
        self.assertIsNone(gst.failure())
        gst.stop()
        self.assertIsNotNone(gst.proc.poll())

    def test_missing_element_is_reported(self) -> None:
        gst = GstProcess(["audiotestsrc", "!", "nosuchelement_caesar", "!", "fakesink"])
        error = self._wait_failure(gst)
        self.assertIsNotNone(error)
        self.assertIn("nosuchelement_caesar", error)

    def test_busy_port_is_reported(self) -> None:
        import socket

        taken = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        taken.bind(("0.0.0.0", 0))
        self.addCleanup(taken.close)
        port = taken.getsockname()[1]
        gst = GstProcess(["udpsrc", f"port={port}", "reuse=false", "!", "fakesink"])
        error = self._wait_failure(gst)
        self.assertIsNotNone(error)
        self.assertIn("udpsrc0", error)
        self.assertNotIn("/GstPipeline", error)


if __name__ == "__main__":
    unittest.main()
