"""Config load/save."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config


class ConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        for name, value in (("CONFIG_DIR", self.dir), ("CONFIG_FILE", self.dir / "config.json")):
            patcher = mock.patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_missing_file_gives_defaults(self) -> None:
        self.assertEqual(config.load_config(), config.DEFAULT_CONFIG)

    def test_round_trip_and_missing_keys_get_defaults(self) -> None:
        config.save_config({"server_ip": "10.0.1.15"})
        loaded = config.load_config()
        self.assertEqual(loaded["server_ip"], "10.0.1.15")
        self.assertEqual(loaded["cat_enabled"], config.DEFAULT_CONFIG["cat_enabled"])

    def test_corrupt_file_gives_defaults(self) -> None:
        (self.dir / "config.json").write_text('{"server_ip": "10.0')
        self.assertEqual(config.load_config(), config.DEFAULT_CONFIG)

    def test_save_leaves_no_temp_files(self) -> None:
        config.save_config({"server_ip": "1.2.3.4"})
        config.save_config({"server_ip": "5.6.7.8"})
        self.assertEqual([p.name for p in self.dir.iterdir()], ["config.json"])
        self.assertEqual(json.loads((self.dir / "config.json").read_text())["server_ip"], "5.6.7.8")

    def test_failed_write_keeps_old_file(self) -> None:
        config.save_config({"server_ip": "1.2.3.4"})
        with self.assertRaises(TypeError):
            config.save_config({"server_ip": object()})  # not JSON-serializable
        self.assertEqual(config.load_config()["server_ip"], "1.2.3.4")
        self.assertEqual([p.name for p in self.dir.iterdir()], ["config.json"])


if __name__ == "__main__":
    unittest.main()
