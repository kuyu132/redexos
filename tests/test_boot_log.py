from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
collect = runpy.run_path(str(ROOT / "scripts/collect-boot-logs.py"))["collect"]


class BootLogTests(unittest.TestCase):
    def test_real_rust_record_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "tests"
            subprocess.run(["rustc", "--edition=2024", "--test", str(ROOT / "tests/test_boot_log.rs"), "-o", str(binary)], check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True)

    def test_mounted_collection_preserves_interrupted_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "sd"
            logs = source / "redexos/logs"
            logs.mkdir(parents=True)
            failed = b"[SD-LOG] BEGIN v3\n\r[FS-READ] #259 returned=0x1000 processing"
            (logs / "BOOT0001.LOG").write_bytes(failed)
            (logs / "BOOT0002.LOG").write_text("[SD-LOG] END UEFI; kernel result unknown\n")
            result = collect(source, Path(tmp) / "evidence", volume=True)
            self.assertEqual(result["status"], "collected")
            self.assertFalse(result["logs"][0]["uefi_handoff_recorded"])
            self.assertTrue(result["logs"][1]["uefi_handoff_recorded"])
            self.assertIn("#259", result["logs"][0]["last_checkpoint"])
            self.assertEqual((logs / "BOOT0001.LOG").read_bytes(), failed)
            with self.assertRaises(ValueError):
                collect(source, source / "output", volume=True)

    def test_legacy_volume_is_no_logs_not_a_successful_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "sd"
            source.mkdir()
            self.assertEqual(collect(source, Path(tmp) / "evidence", volume=True)["status"], "no_logs")
