from pathlib import Path
import subprocess
import tempfile
import unittest
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
collector = runpy.run_path(str(ROOT / "scripts/collect-boot-isolation.py"))


class BootIsolationTests(unittest.TestCase):
    def test_offline_reader_preserves_source_and_detects_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            volume = tmp / "fat"
            directory = volume / "redexos/isolation"
            directory.mkdir(parents=True)
            file = directory / "trial.BIN"
            data = collector["expected_bytes"]("trial", 16 * 2048)
            file.write_bytes(data)
            self.assertEqual(collector["collect"](volume, tmp / "good", "trial", 16, volume=True)["status"], "passed")
            self.assertEqual(file.read_bytes(), data)
            damaged = bytearray(data)
            damaged[-1] ^= 1
            file.write_bytes(damaged)
            self.assertEqual(collector["collect"](volume, tmp / "bad", "trial", 16, volume=True)["status"], "failed")
            self.assertEqual(file.read_bytes(), damaged)
            with self.assertRaises(ValueError):
                collector["collect"](volume, volume / "evidence", "trial", 16, volume=True)
            with self.assertRaises(ValueError):
                collector["collect"](volume, tmp / "escape", "../trial", 16, volume=True)

    def test_production_config_and_pattern(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "test"
            subprocess.run(["rustc", "--edition=2024", "--test",
                            str(ROOT / "tests/test_boot_isolation.rs"), "-o", str(binary)],
                           check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True)
