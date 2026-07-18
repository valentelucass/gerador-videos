"""Contract tests for the persistent horizontal asset structure."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from backend.src.scripts import setup_assets_horizontal as setup


class HorizontalAssetSetupTests(unittest.TestCase):
    def test_setup_uses_overlay_collections_without_transition_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "horizontal"
            tracks = root / "trilhas"
            overlays = root / "overlays"
            backgrounds = root / "fundos_estaticos"
            collection = overlays / "film_burns"
            tracks.mkdir(parents=True)
            collection.mkdir(parents=True)
            backgrounds.mkdir()
            (tracks / "fundo_documentario.mp3").write_bytes(b"track")
            (overlays / "seta_apontamento.png").write_bytes(b"arrow")
            (collection / "transition.mov").write_bytes(b"av")
            (backgrounds / "grade.jpeg").write_bytes(b"background")

            with (
                patch.object(setup, "ASSETS_HORIZONTAL_DIR", root),
                patch.object(setup, "OVERLAYS_DIR", overlays),
                redirect_stdout(io.StringIO()),
            ):
                pronto = setup.setup_assets_horizontal()

            self.assertTrue(pronto)
            self.assertFalse((root / "transicoes").exists())

    def test_setup_rejects_overlays_without_transition_collections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "horizontal"
            tracks = root / "trilhas"
            overlays = root / "overlays"
            backgrounds = root / "fundos_estaticos"
            tracks.mkdir(parents=True)
            overlays.mkdir(parents=True)
            backgrounds.mkdir()
            (tracks / "fundo_documentario.mp3").write_bytes(b"track")
            (overlays / "seta_apontamento.png").write_bytes(b"arrow")
            (backgrounds / "grade.png").write_bytes(b"background")

            with (
                patch.object(setup, "ASSETS_HORIZONTAL_DIR", root),
                patch.object(setup, "OVERLAYS_DIR", overlays),
                redirect_stdout(io.StringIO()),
            ):
                pronto = setup.setup_assets_horizontal()

            self.assertFalse(pronto)

    def test_setup_rejects_empty_static_background_pool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "horizontal"
            tracks = root / "trilhas"
            overlays = root / "overlays"
            backgrounds = root / "fundos_estaticos"
            collection = overlays / "film_burns"
            tracks.mkdir(parents=True)
            collection.mkdir(parents=True)
            backgrounds.mkdir()
            (tracks / "fundo_documentario.mp3").write_bytes(b"track")
            (overlays / "seta_apontamento.png").write_bytes(b"arrow")
            (collection / "transition.mov").write_bytes(b"av")
            (backgrounds / "leia-me.txt").write_text("nao e imagem", encoding="utf-8")
            (backgrounds / "vazio.jpg").touch()

            saida = io.StringIO()
            with (
                patch.object(setup, "ASSETS_HORIZONTAL_DIR", root),
                patch.object(setup, "OVERLAYS_DIR", overlays),
                redirect_stdout(saida),
            ):
                pronto = setup.setup_assets_horizontal()

            self.assertFalse(pronto)
            self.assertIn("FALTA", saida.getvalue())
            self.assertIn("fundos estaticos JPG/JPEG/PNG", saida.getvalue())


if __name__ == "__main__":
    unittest.main()
