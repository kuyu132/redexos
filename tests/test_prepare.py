import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("prepare", Path(__file__).resolve().parents[1] / "scripts/prepare.py")
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.original_root = prepare.ROOT
        prepare.ROOT = self.root
        self.source = self.root / "source"
        self.source.mkdir()
        subprocess.run(["git", "init", "-q", str(self.source)], check=True)
        recipe = self.source / "recipes/core/kernel/recipe.toml"
        recipe.parent.mkdir(parents=True)
        recipe.write_text('[source]\ngit = "https://example.invalid/kernel.git"\n\n[build]\ntemplate = "custom"\n')
        subprocess.run(["git", "-C", str(self.source), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.source), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture"], check=True)
        rev = prepare.git(self.source, "rev-parse", "HEAD")
        (self.root / "sources.lock.json").write_text(json.dumps({"repositories": {
            "redox": {"url": str(self.source), "rev": rev},
            "kernel": {"url": "https://example.invalid/kernel.git", "rev": "a" * 40},
        }}))
        (self.root / "board-firmware.lock.json").write_text(json.dumps({
            "schema": 1,
            "boards": {"pi4b": {"version": "fixture"}},
        }))
        for name in ("common", *prepare.BOARDS):
            path = self.root / "config" / name / "minimal.toml"
            path.parent.mkdir(parents=True)
            path.write_text(f'# {name}\ninclude = []\n')
        board_config = self.root / "board-config/pi4b/config.txt"
        board_config.parent.mkdir(parents=True)
        board_config.write_text("arm_64bit=1\n")

    def tearDown(self):
        prepare.ROOT = self.original_root
        self.temp.cleanup()

    def test_isolated_boards_and_idempotence(self):
        for board in prepare.BOARDS:
            prepare.prepare(board)
            prepare.prepare(board)
        first = self.root / "work/pi4b/redox/config/aarch64/pi4b/minimal.toml"
        first.write_text("local edit\n")
        prepare.verify("pi5")
        with self.assertRaisesRegex(ValueError, "configuration drift"):
            prepare.verify("pi4b")

    def test_preserves_local_edits(self):
        prepare.prepare("pi4b")
        path = self.root / "work/pi4b/redox/recipes/core/kernel/recipe.toml"
        path.write_text("my local implementation\n")
        with self.assertRaisesRegex(ValueError, "Refusing to overwrite"):
            prepare.prepare("pi4b")
        self.assertEqual(path.read_text(), "my local implementation\n")

    def test_rejects_revision_drift(self):
        prepare.prepare("pi4b")
        manifest = json.loads((self.root / "sources.lock.json").read_text())
        manifest["repositories"]["redox"]["rev"] = "b" * 40
        (self.root / "sources.lock.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "Revision differs"):
            prepare.prepare("pi4b")

    def test_pin_preserves_build_script(self):
        original = '[source]\ngit = "old"\nbranch = "main"\nrev = "old"\n\n[build]\nscript = "git = untouched"\n'
        result = prepare.recipe_with_pin(original, {"url": "new", "rev": "a" * 40})
        self.assertIn('script = "git = untouched"', result)
        self.assertNotIn('branch = "main"', result)
        self.assertEqual(result.count('rev = '), 1)

    def test_locked_patch_is_copied_and_wired_into_recipe(self):
        patch = "diff --git a/frame.rs b/frame.rs\n"
        patch_path = self.root / "patches/kernel.patch"
        patch_path.parent.mkdir(parents=True)
        patch_path.write_text(patch)
        manifest = json.loads((self.root / "sources.lock.json").read_text())
        import hashlib
        manifest["repositories"]["kernel"]["patch"] = {
            "path": "patches/kernel.patch",
            "sha256": hashlib.sha256(patch.encode()).hexdigest(),
        }
        (self.root / "sources.lock.json").write_text(json.dumps(manifest))

        prepare.prepare("pi4b")
        recipe = self.root / "work/pi4b/redox/recipes/core/kernel/recipe.toml"
        copied = recipe.with_name("redexos.patch")
        self.assertIn('patches = ["redexos.patch"]', recipe.read_text())
        self.assertEqual(copied.read_text(), patch)
        prepare.verify("pi4b")

        updated = patch + "updated\n"
        patch_path.write_text(updated)
        manifest["repositories"]["kernel"]["patch"]["sha256"] = hashlib.sha256(updated.encode()).hexdigest()
        (self.root / "sources.lock.json").write_text(json.dumps(manifest))
        prepare.prepare("pi4b")
        self.assertEqual(copied.read_text(), updated)

    def test_patch_tampering_is_rejected_before_prepare(self):
        patch_path = self.root / "patches/kernel.patch"
        patch_path.parent.mkdir(parents=True)
        patch_path.write_text("tampered\n")
        manifest = json.loads((self.root / "sources.lock.json").read_text())
        manifest["repositories"]["kernel"]["patch"] = {
            "path": "patches/kernel.patch", "sha256": "0" * 64,
        }
        (self.root / "sources.lock.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            prepare.prepare("pi4b")

    def test_snapshot_records_actual_lock_and_generated_config(self):
        prepare.prepare("pi4b")
        tree = self.root / "work/pi4b/redox"
        (tree / "cookbook.lock").write_text('# resolved sources\n')
        output = self.root / "snapshot.json"
        prepare.snapshot("pi4b", output)
        result = json.loads(output.read_text())
        self.assertEqual(result["lock_files"]["cookbook.lock"], '# resolved sources\n')
        self.assertIn("config/aarch64/pi4b/minimal.toml", result["generated_configuration"])
        self.assertIn("board-config/pi4b/config.txt", result["project_input_sha256"])
        self.assertIn("sources.lock.json", result["project_input_sha256"])
        self.assertIn("board-firmware.lock.json", result["project_input_sha256"])
        self.assertFalse(result["hardware_validated"])


if __name__ == "__main__":
    unittest.main()
