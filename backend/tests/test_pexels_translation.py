import unittest
from unittest.mock import patch

from backend.src.models import TranslationRequest
from backend.src.pexels import TRANSLATION_CHUNK_SIZE, _translation_chunks, translate_to_portuguese


class TranslationChunkTests(unittest.TestCase):
    def test_keeps_short_text_in_one_piece(self) -> None:
        self.assertEqual(_translation_chunks("Primeira frase. Segunda frase."), ["Primeira frase. Segunda frase."])

    def test_splits_long_text_only_on_word_boundaries(self) -> None:
        text = " ".join(f"palavra{i}" for i in range(160))
        chunks = _translation_chunks(text)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= TRANSLATION_CHUNK_SIZE for chunk in chunks))
        self.assertEqual(" ".join(chunks), text)

    def test_long_narration_is_accepted_and_sent_in_safe_chunks(self) -> None:
        text = " ".join(f"narracao{i}" for i in range(240))
        request = TranslationRequest(text=text, source_language="en-US")
        chunks = _translation_chunks(text)

        with patch("backend.src.pexels._translate_chunk_to_portuguese", side_effect=lambda chunk, _: chunk) as translate:
            self.assertEqual(translate_to_portuguese(request.text, request.source_language), text)

        self.assertEqual(translate.call_count, len(chunks))


if __name__ == "__main__":
    unittest.main()
