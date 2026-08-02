import unittest

from backend.src.models import Script
from backend.src.services import google_flow_prompt


class CatIllustrationPresetTests(unittest.TestCase):
    def test_marker_selects_the_consistent_editorial_cat_preset(self) -> None:
        script = Script.model_validate({
            "title": "Por que gatos amassam cobertores?",
            "language": "pt-BR",
            "narrator_gender": "female",
            "voice": "pt-BR-FranciscaNeural",
            "blocks": [{
                "id": "block_01", "text": "O gato aperta o cobertor com as patas.",
                "scenes": [{
                    "id": "scene_01", "image_id": 1, "tipo_midia": "imagem",
                    "asset_key": "orange-cat-kneading-blanket", "image": "cena_01.png",
                    "visual": {
                        "subject": "gato laranja", "action": "amassando um cobertor",
                        "setting": "sofá claro", "framing": "gato no centro",
                        "details": "ilustração felina editorial, gato laranja de olhos verdes",
                    },
                    "transition": {"in": "zoom_in", "out": "none", "speed": "normal"},
                    "sounds": {"transition": [], "context": {"type": "click", "at": "start"}},
                }],
            }],
        })

        prompt = google_flow_prompt(script, "block_01", "scene_01")

        self.assertIn("ilustração felina editorial", prompt)
        self.assertIn("consistent recurring cat and caretaker characters", prompt)
        self.assertIn("Avoid photorealism", prompt)


if __name__ == "__main__":
    unittest.main()
