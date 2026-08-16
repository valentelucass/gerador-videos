import unittest

from backend.src.main import get_script_prompt


class CatPromptTests(unittest.TestCase):
    def test_cat_prompt_is_available_and_contains_the_channel_contract(self) -> None:
        prompt = get_script_prompt("cats_without_broll")

        self.assertIn("ESTRUTURA OBRIGATÓRIA EM CINCO FASES", prompt)
        self.assertIn("ilustração felina editorial", prompt)
        self.assertIn("Nunca use “porque ele te ama”", prompt)
        self.assertIn("TODAS as cenas usam `\"tipo_midia\": \"imagem\"`", prompt)
        self.assertIn("CONTRATO EXATO", prompt)
        self.assertIn('"voice": "en-US-RogerNeural"', prompt)


if __name__ == "__main__":
    unittest.main()
