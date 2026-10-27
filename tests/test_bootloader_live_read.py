"""Exercise the same bounded read loop linked into the EFI application."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BootloaderReadTests(unittest.TestCase):
    def test_real_rust_read_loop(self):
        with tempfile.TemporaryDirectory(prefix="redexos-live-read-") as directory:
            binary = Path(directory) / "read-tests"
            subprocess.run([
                "rustc", "--edition=2024", "--test",
                str(ROOT / "tests/test_bootloader_live_read.rs"), "-o", str(binary),
            ], check=True, capture_output=True, text=True)
            subprocess.run([str(binary)], check=True, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
