import unittest
from unittest.mock import patch

from backend.src import services
from backend.src.models import Script


class StaticImageFormatTests(unittest.TestCase):
    def test_static_image_script_accepts_fullscreen_annotation_without_broll(self) -> None:
        script = Script.model_validate({
            "title": "Armadilhas de preço",
            "language": "pt-BR",
            "narrator_gender": "male",
            "voice": "pt-BR-AntonioNeural",
            "blocks": [{
                "id": "block_01",
                "text": "Comente qual preço confuso você já encontrou e inscreva-se para mais.",
                "scenes": [{
                    "id": "scene_01",
                    "image_id": 1,
                    "tipo_midia": "imagem",
                    "asset_key": "shopper-checking-price-label",
                    "image": "cena_01.png",
                    "visual": {
                        "subject": "comprador diante de uma etiqueta de preço",
                        "action": "comparando dois valores",
                        "setting": "corredor de supermercado",
                        "framing": "etiqueta e mão no centro",
                        "details": "produtos desfocados ao fundo",
                    },
                    "annotation": {"lines": ["SE INSCREVA", "PARA MAIS"], "emoji": "🔔"},
                    "transition": {"in": "zoom_in", "out": "none", "speed": "slow"},
                    "sounds": {"transition": [], "context": {"type": "click", "at": "start"}},
                }],
            }],
        })

        with (
            patch.object(services, "resolve_scene_image_sources", return_value={"cena_01.png": "cena_01.png"}),
            patch.object(services, "missing_scene_images", return_value=[]),
        ):
            report = services.validate_script(script)

        self.assertTrue(report["valid"])
        self.assertEqual(report["media_mode"], "without_broll")


if __name__ == "__main__":
    unittest.main()
