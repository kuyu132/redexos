"""Exercise the memory-disk read and trace code actually linked into EFI."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BootloaderFsTraceTests(unittest.TestCase):
    def test_real_rust_memory_disk_and_trace(self):
        with tempfile.TemporaryDirectory(prefix="redexos-fs-trace-") as directory:
            binary = Path(directory) / "fs-tests"
            subprocess.run(["rustc", "--edition=2024", "--test",
                            str(ROOT / "tests/test_bootloader_fs_trace.rs"), "-o", str(binary)],
                           check=True, capture_output=True, text=True)
            subprocess.run([str(binary)], check=True, capture_output=True, text=True)
