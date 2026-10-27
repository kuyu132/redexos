import json
import os
from pathlib import Path
import pty
import select
import signal
import subprocess
import sys
import tempfile
import termios
import unittest


class SerialTests(unittest.TestCase):
    def test_raw_bytes_and_restore_on_interrupt(self):
        master, slave = pty.openpty()
        original = termios.tcgetattr(slave)
        script = Path(__file__).resolve().parents[1] / "scripts/collect-serial.py"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "capture"
            proc = subprocess.Popen(
                [sys.executable, str(script), "pi5", os.ttyname(slave), str(output)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            try:
                self.assertTrue(select.select([proc.stderr], [], [], 5)[0])
                self.assertIn(b"Capturing serial", proc.stderr.readline())
                data = b"fixture: UART10\r\nraw:\x00\xff\n"
                os.write(master, data)
                received = b""
                while len(received) < len(data):
                    self.assertTrue(select.select([proc.stdout], [], [], 5)[0])
                    chunk = os.read(proc.stdout.fileno(), len(data) - len(received))
                    self.assertTrue(chunk)
                    received += chunk
                self.assertEqual(received, data)
                proc.send_signal(signal.SIGINT)
                _, stderr = proc.communicate(timeout=5)
                self.assertEqual(proc.returncode, 0, stderr)
                self.assertEqual((output / "serial.log").read_bytes(), data)
                self.assertFalse(json.loads((output / "capture.json").read_text())["hardware_pass"])
                restored = termios.tcgetattr(slave)
                # PENDIN is dynamic input queue state, not a serial configuration flag.
                # macOS sets it when returning the PTY from raw to canonical mode.
                for attrs in (original, restored):
                    attrs[3] &= ~getattr(termios, "PENDIN", 0)
                self.assertEqual(restored, original)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                proc.stdout.close()
                proc.stderr.close()
                os.close(master)
                os.close(slave)
