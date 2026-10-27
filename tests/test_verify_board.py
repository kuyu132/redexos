import importlib.util
import json
import os
from pathlib import Path
import pty
import re
import select
import subprocess
import sys
import tempfile
import threading
import time
import unittest


SPEC = importlib.util.spec_from_file_location(
    "verify_board", Path(__file__).resolve().parents[1] / "scripts/verify-board.py"
)
verify_board = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify_board)


class BoardVerificationTests(unittest.TestCase):
    def test_functional_and_stability_markers(self):
        smoke = "\n".join([
            "REDOX_BOARD_SMOKE_BEGIN", "PASS port-target=pi5: experimental",
            "PASS file-write-read", "PASS pipe", "PASS child-exec-wait-exit-37",
            "PASS timer-sleep", "REDOX_BOARD_SMOKE_PASS",
        ])
        self.assertTrue(verify_board.smoke_result(smoke, "pi5")["passed"])
        stability = "REDOX_BOARD_HEARTBEAT iteration=10/10\nREDOX_BOARD_STABILITY_PASS rounds=10\n"
        self.assertTrue(verify_board.stability_result(stability, 10)["passed"])

    def test_failures_and_milestone_classification(self):
        self.assertFalse(verify_board.smoke_result("KERNEL PANIC", "pi4b")["passed"])
        self.assertEqual(
            verify_board.last_milestone("Redox OS starting...\ninit: switchroot to /usr /etc\nredox login:"),
            "login",
        )

    def test_end_to_end_serial_capture_and_commands(self):
        master, slave = pty.openpty()
        device = os.ttyname(slave)

        def read_line():
            data = bytearray()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.2)[0]:
                    data.extend(os.read(master, 4096))
                    if b"\r" in data:
                        return bytes(data)
            raise TimeoutError(f"fake board input timed out: {data!r}")

        def respond(command, body):
            token = re.search(rb"BOARD_TEST_[0-9a-f]+", command).group().decode()
            os.write(master, (token + "_BEGIN\r\n" + body + "\r\n" + token + "_END:0\r\nroot:~# ").encode())

        def fake_board():
            time.sleep(0.3)
            os.write(master, b"Redox OS starting...\r\ninit: switchroot to /usr /etc\r\nredox login: ")
            self.assertIn(b"root", read_line())
            os.write(master, b"password: ")
            self.assertIn(b"password", read_line())
            os.write(master, b"root:~# ")
            respond(read_line(), "\n".join([
                "REDOX_BOARD_SMOKE_BEGIN", "PASS port-target=pi4b: experimental",
                "PASS file-write-read", "PASS pipe", "PASS child-exec-wait-exit-37",
                "PASS timer-sleep", "REDOX_BOARD_SMOKE_PASS",
            ]))
            respond(read_line(), "\n".join([
                "REDOX_BOARD_HEARTBEAT iteration=2/2",
                "REDOX_BOARD_STABILITY_PASS rounds=2",
            ]))

        worker = threading.Thread(target=fake_board, daemon=True)
        worker.start()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"board": "pi4b", "image_sha256": "a" * 64}))
            output = root / "evidence"
            process = subprocess.run([
                sys.executable, str(Path(__file__).resolve().parents[1] / "scripts/verify-board.py"),
                "pi4b", device, str(output), "--image-manifest", str(manifest),
                "--no-prompt", "--boots", "1", "--stability-rounds", "2",
                "--boot-timeout", "5", "--command-timeout", "5",
            ], capture_output=True, text=True, timeout=15)
            worker.join(timeout=5)
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads((output / "result.json").read_text())
            self.assertEqual(result["status"], "passed")
            self.assertTrue(result["hardware_validated"])
            self.assertIn("Redox OS starting...", (output / "serial-clean.log").read_text())
        os.close(master)
        os.close(slave)


if __name__ == "__main__":
    unittest.main()
