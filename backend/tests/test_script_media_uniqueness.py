import copy
import unittest

from pydantic import ValidationError

from backend.src.models import Script


def valid_script_payload() -> dict:
    return {
        "title": "Armadilhas de preço",
        "language": "pt-BR",
        "narrator_gender": "male",
        "voice": "pt-BR-AntonioNeural",
        "blocks": [
            {
                "id": "block_01",
                "text": "A primeira oferta parece irresistível, mas o preço final aparece somente depois.",
                "scenes": [{
                    "id": "scene_01",
                    "image_id": 1,
                    "tipo_midia": "video_generico",
                    "asset_key": "shopper-checking-price-label",
                    "image": "cena_01.mp4",
                    "visual": {
                        "subject": "comprador analisando uma etiqueta",
                        "action": "comparando os valores exibidos",
                        "setting": "corredor de supermercado",
                        "framing": "mão e etiqueta em plano fechado",
                        "details": "produtos desfocados ao fundo",
                    },
                    "transition": {"in": "zoom_in", "out": "none", "speed": "slow"},
                }],
            },
            {
                "id": "block_02",
                "text": "O desconto anunciado muda a percepção, mesmo quando o custo total continua elevado.",
                "scenes": [{
                    "id": "scene_02",
                    "image_id": 2,
                    "tipo_midia": "imagem",
                    "asset_key": "customer-reading-sale-sign",
                    "image": "cena_02.png",
                    "visual": {
                        "subject": "cliente lendo uma placa de promoção",
                        "action": "avaliando o desconto anunciado",
                        "setting": "loja iluminada",
                        "framing": "placa e rosto em plano médio",
                        "details": "prateleiras organizadas ao fundo",
                    },
                    "transition": {"in": "zoom_in", "out": "none", "speed": "slow"},
                }],
            },
        ],
    }


class ScriptMediaUniquenessTests(unittest.TestCase):
    def test_rejects_reused_media_filename_before_curation(self) -> None:
        payload = valid_script_payload()
        payload["blocks"][1]["scenes"][0]["image"] = "cena_01.mp4"
        payload["blocks"][1]["scenes"][0]["tipo_midia"] = "video_generico"

        with self.assertRaisesRegex(ValidationError, "nome de arquivo 'image' exclusivo"):
            Script.model_validate(payload)

    def test_rejects_reused_asset_key_before_curation(self) -> None:
        payload = copy.deepcopy(valid_script_payload())
        payload["blocks"][1]["scenes"][0]["asset_key"] = "shopper-checking-price-label"

        with self.assertRaisesRegex(ValidationError, "asset_key' exclusivo"):
            Script.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
