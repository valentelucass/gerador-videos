import unittest

from backend.src.services import image_id_from_filename


class ImageFilenameIdTests(unittest.TestCase):
    def test_accepts_numeric_and_scene_prefixes_with_optional_flow_suffixes(self) -> None:
        cases = {
            "1 - cat-on-sofa.png": 1,
            "1_-_cat-on-sofa_202608061331.png": 1,
            "cena_01.png": 1,
            "cena_01_202608061331.png": 1,
            "scene-107 final.webp": 107,
        }
        for filename, expected_id in cases.items():
            with self.subTest(filename=filename):
                self.assertEqual(image_id_from_filename(filename), expected_id)

    def test_rejects_descriptive_names_without_an_explicit_id(self) -> None:
        self.assertIsNone(image_id_from_filename("orange-tabby-on-sofa.png"))


if __name__ == "__main__":
    unittest.main()
