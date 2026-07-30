import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.src import main


class ImageDeletionTests(unittest.TestCase):
    def test_deletes_only_explicit_safe_image_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image_dir = Path(temporary)
            target = image_dir / "scene-one.png"
            target.write_bytes(b"image")

            with patch.object(main, "IMAGE_DIR", image_dir):
                result = main.delete_images(["scene-one.png", "missing.jpg"])

            self.assertEqual(result["deleted"], ["scene-one.png"])
            self.assertEqual(result["missing"], ["missing.jpg"])
            self.assertFalse(target.exists())

    def test_rejects_path_traversal_before_deleting_any_image(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image_dir = Path(temporary)
            target = image_dir / "keep.png"
            target.write_bytes(b"image")

            with patch.object(main, "IMAGE_DIR", image_dir):
                with self.assertRaises(Exception):
                    main.delete_images(["keep.png", "..\\outside.png"])

            self.assertTrue(target.exists())


if __name__ == "__main__":
    unittest.main()
