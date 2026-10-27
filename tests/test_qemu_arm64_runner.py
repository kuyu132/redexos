import importlib.util
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "run_qemu_arm64", Path(__file__).resolve().parents[1] / "scripts/run-qemu-arm64.py"
)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class EvidenceLineTests(unittest.TestCase):
    def test_accepts_appended_register_diagnostics(self):
        text = "PASS signal-registers x1=1111 x2=2222 x18=1818\n"
        self.assertIsNotNone(
            runner.find_evidence_line(text, "PASS signal-registers x1=1111 x2=2222")
        )

    def test_rejects_partial_marker_names(self):
        self.assertIsNone(runner.find_evidence_line("PASS pipeline\n", "PASS pipe"))


if __name__ == "__main__":
    unittest.main()
