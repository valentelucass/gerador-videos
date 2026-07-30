import unittest

from backend.src.models import Script
from backend.src.services import google_flow_prompt


class PsychologyLithographPresetTests(unittest.TestCase):
    def test_marker_selects_the_borderless_cosmic_lithograph_preset(self) -> None:
        script = Script.model_validate({
            "title": "Padrões de proteção",
            "language": "pt-BR",
            "narrator_gender": "male",
            "voice": "pt-BR-AntonioNeural",
            "blocks": [{
                "id": "block_01", "text": "Uma pessoa hesita antes de responder.",
                "scenes": [{
                    "id": "scene_01", "image_id": 1, "tipo_midia": "imagem",
                    "asset_key": "woman-reading-message-alone", "image": "cena_01.png",
                    "visual": {
                        "subject": "mulher com celular", "action": "hesitando", "setting": "vazio escuro",
                        "framing": "mulher no centro", "details": "litografia cósmica vintage sem moldura",
                    },
                    "transition": {"in": "zoom_in", "out": "none", "speed": "normal"},
                    "sounds": {"transition": [], "context": {"type": "click", "at": "start"}},
                }],
            }],
        })

        prompt = google_flow_prompt(script, "block_01", "scene_01")

        self.assertIn("litografia cósmica vintage", prompt)
        self.assertIn("artwork bleeding cleanly to every edge", prompt)
        self.assertIn("Avoid frames, borders", prompt)


if __name__ == "__main__":
    unittest.main()
