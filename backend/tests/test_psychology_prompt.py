import unittest

from backend.src.main import get_script_prompt


class PsychologyPromptTests(unittest.TestCase):
    def test_psychology_prompt_is_static_and_contains_the_specialized_instructions(self) -> None:
        prompt = get_script_prompt("psychology_without_broll")
        normalized = " ".join(prompt.split())

        self.assertIn("ESTILO NARRATIVO PSICOLÓGICO", prompt)
        self.assertIn("3 ou 4 mecanismos psicológicos distintos", normalized)
        self.assertIn("TODAS as cenas usam `\"tipo_midia\": \"imagem\"`", prompt)
        self.assertIn("CONTRATO EXATO", prompt)
        self.assertNotIn("Planeje silenciosamente conflito, pessoa afetada", prompt)


if __name__ == "__main__":
    unittest.main()
