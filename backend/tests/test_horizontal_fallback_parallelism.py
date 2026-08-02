import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.src.core import horizontal_renderer as renderer


class HorizontalFallbackParallelismTests(unittest.TestCase):
    def test_amf_keeps_card_composition_serial(self) -> None:
        token = renderer._AMF_HEALTHY.set(True)
        try:
            self.assertEqual(renderer._card_render_workers(5), 1)
        finally:
            renderer._AMF_HEALTHY.reset(token)

    def test_software_fallback_uses_at_most_two_card_workers(self) -> None:
        token = renderer._AMF_HEALTHY.set(False)
        try:
            self.assertEqual(renderer._card_render_workers(1), 1)
            self.assertEqual(renderer._card_render_workers(5), 2)
        finally:
            renderer._AMF_HEALTHY.reset(token)

    def test_no_card_task_does_not_create_a_worker(self) -> None:
        self.assertEqual(renderer._card_render_workers(0), 0)

    def test_software_fallback_composes_independent_cards_with_the_parallel_path(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            token = renderer._AMF_HEALTHY.set(False)
            try:
                with patch.object(renderer, "_run_compositor") as run_compositor:
                    outputs = renderer._native_render_scene_canvases(
                        [object(), object()],
                        [root / "base_1.mp4", root / "base_2.mp4"],
                        root / "cards",
                        root / "background.jpg",
                        root / "background_blur.png",
                        root / "shadow.png",
                        root / "mask.png",
                        ["card", "card"],
                        [False, False],
                        ["none", "none"],
                        [0.0, 1.0],
                        [1.0, 1.0],
                        [1.0, 1.0],
                        "none",
                        0.0,
                    )
            finally:
                renderer._AMF_HEALTHY.reset(token)

        self.assertEqual(len(outputs), 2)
        self.assertEqual(run_compositor.call_count, 2)


if __name__ == "__main__":
    unittest.main()
