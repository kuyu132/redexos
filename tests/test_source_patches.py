import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import source_patches


class SourcePatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_root = source_patches.ROOT
        source_patches.ROOT = self.root
        self.tree = self.root / "source"
        self.tree.mkdir()
        self.git("init", "-q")
        self.file = self.tree / "frame.rs"
        self.file.write_text("before\n")
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=test@example.invalid", "commit", "-qm", "base")
        self.file.write_text("after\n")
        patch = source_patches.source_diff(self.tree)
        self.git("restore", "frame.rs")
        (self.root / "fix.patch").write_bytes(patch)
        self.spec = {"patch": {"path": "fix.patch", "sha256": hashlib.sha256(patch).hexdigest()}}

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.tree), *args])

    def tearDown(self):
        source_patches.ROOT = self.old_root
        self.temp.cleanup()

    def test_clean_checkout_applies_exactly_and_repeat_is_idempotent(self):
        source_patches.ensure_patch(self.tree, self.spec)
        source_patches.ensure_patch(self.tree, self.spec)
        self.assertEqual(self.file.read_text(), "after\n")
        self.assertEqual(source_patches.source_diff(self.tree), (self.root / "fix.patch").read_bytes())

    def test_patch_can_add_source_files_and_repeat_is_idempotent(self):
        added = self.tree / "read.rs"
        added.write_text("new helper\n")
        self.git("add", "-N", "read.rs")
        patch = source_patches.source_diff(self.tree)
        self.git("reset", "-q", "--", "read.rs")
        added.unlink()
        (self.root / "fix.patch").write_bytes(patch)
        self.spec["patch"]["sha256"] = hashlib.sha256(patch).hexdigest()
        source_patches.ensure_patch(self.tree, self.spec)
        source_patches.ensure_patch(self.tree, self.spec)
        self.assertEqual(added.read_text(), "new helper\n")
        self.assertEqual(source_patches.source_diff(self.tree), patch)
        self.assertEqual(self.git("diff", "--cached"), b"")

    def test_modified_source_and_untracked_files_are_preserved(self):
        self.file.write_text("user edit\n")
        with self.assertRaisesRegex(ValueError, "Refusing to overwrite"):
            source_patches.ensure_patch(self.tree, self.spec)
        self.assertEqual(self.file.read_text(), "user edit\n")
        self.file.write_text("before\n")
        (self.tree / "new.rs").write_text("user work\n")
        with self.assertRaisesRegex(ValueError, "Unrecorded source"):
            source_patches.ensure_patch(self.tree, self.spec)
        self.assertEqual((self.tree / "new.rs").read_text(), "user work\n")

    def test_patch_tampering_is_rejected_before_source_mutation(self):
        (self.root / "fix.patch").write_text("tampered\n")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            source_patches.ensure_patch(self.tree, self.spec)
        self.assertEqual(self.file.read_text(), "before\n")


if __name__ == "__main__":
    unittest.main()
