import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile


SPEC = importlib.util.spec_from_file_location(
    "sd_image", Path(__file__).resolve().parents[1] / "scripts/sd_image.py"
)
sd_image = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sd_image)


class SdImageTests(unittest.TestCase):
    def test_mbr_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / "card.img"
            layout = sd_image.write_mbr(image, 40)
            self.assertEqual(image.stat().st_size, 40 * 1024 * 1024)
            self.assertEqual(sd_image.read_mbr(image), {
                "partition_type": 0xEF,
                "start_lba": 2048,
                "sectors": layout["partition_sectors"],
            })

    def test_zip_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("../escape", "bad")
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                sd_image.safe_extract(archive, root / "stage")
            self.assertFalse((root / "escape").exists())


if __name__ == "__main__":
    unittest.main()
