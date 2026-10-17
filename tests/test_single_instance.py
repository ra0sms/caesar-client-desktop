"""Single-instance guard tests."""

import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from single_instance import acquire_instance_lock, busy_udp_ports


class InstanceLockTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "instance.lock"

    def test_second_instance_is_refused(self) -> None:
        first = acquire_instance_lock(self.path)
        self.assertIsNotNone(first)
        self.assertIsNone(acquire_instance_lock(self.path))
        first.unlock()
        self.assertIsNotNone(acquire_instance_lock(self.path))

    def test_lock_of_crashed_instance_is_taken_over(self) -> None:
        # os._exit skips cleanup: the lock file stays behind, owned by a dead PID
        code = (
            "import os, sys; from pathlib import Path\n"
            "from single_instance import acquire_instance_lock\n"
            f"lock = acquire_instance_lock(Path({str(self.path)!r}))\n"
            "os._exit(0 if lock else 1)\n"
        )
        cwd = Path(__file__).resolve().parent.parent
        subprocess.run([sys.executable, "-c", code], cwd=cwd, check=True)
        self.assertTrue(self.path.exists())
        self.assertIsNotNone(acquire_instance_lock(self.path))


class BusyPortsTest(unittest.TestCase):
    def test_reports_only_taken_ports(self) -> None:
        taken = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        taken.bind(("0.0.0.0", 0))
        self.addCleanup(taken.close)
        taken_port = taken.getsockname()[1]

        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.bind(("0.0.0.0", 0))
        free_port = probe.getsockname()[1]
        probe.close()

        self.assertEqual(busy_udp_ports([taken_port, free_port]), [taken_port])


if __name__ == "__main__":
    unittest.main()
