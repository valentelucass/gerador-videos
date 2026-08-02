import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.src.core import horizontal_renderer as renderer


class HorizontalBrollDurationTests(unittest.TestCase):
    def test_slows_a_five_second_broll_to_cover_a_nine_second_scene(self) -> None:
        scene = SimpleNamespace(id="scene_09", image="cena_09.mp4", tipo_midia="video_generico")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                patch.object(renderer, "SCENE_RENDER_WORKERS", 1),
                patch.object(renderer, "_duration", return_value=5.21),
                patch.object(renderer, "_run_compositor") as run_compositor,
            ):
                renderer._native_render_scene_clips(
                    [scene], root / "clips", root / "assets", ["fullscreen"], [9 * renderer.FPS]
                )

            run_compositor.assert_called_once()
            filter_graph = run_compositor.call_args.args[0][
                run_compositor.call_args.args[0].index("-filter_complex") + 1
            ]
            self.assertIn("setpts=PTS*1.72744722", filter_graph)

    def test_slows_a_short_broll_when_the_transition_extends_the_scene(self) -> None:
        scene = SimpleNamespace(id="scene_45", image="cena_45.mp4", tipo_midia="video_generico")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                patch.object(renderer, "SCENE_RENDER_WORKERS", 1),
                patch.object(renderer, "_duration", return_value=4.56),
                patch.object(renderer, "_run_compositor") as run_compositor,
            ):
                # Mantém a janela editorial de 9,633 s que antes equivalia a
                # 289 quadros na grade antiga de 30 fps.
                extended_scene_frames = round((289 / 30) * renderer.FPS)
                renderer._native_render_scene_clips(
                    [scene], root / "clips", root / "assets", ["fullscreen"], [extended_scene_frames]
                )

            run_compositor.assert_called_once()
            filter_graph = run_compositor.call_args.args[0][
                run_compositor.call_args.args[0].index("-filter_complex") + 1
            ]
            self.assertIn("setpts=PTS*2.11257310", filter_graph)

    def test_rejects_a_broll_clip_that_would_freeze_before_the_scene_ends(self) -> None:
        scene = SimpleNamespace(id="scene_10", image="cena_10.mp4", tipo_midia="video_generico")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                patch.object(renderer, "SCENE_RENDER_WORKERS", 1),
                patch.object(renderer, "_duration", return_value=3.0),
                patch.object(renderer, "_run_compositor") as run_compositor,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    r"scene_10.*cena_10\.mp4.*tem 3\.00s.*Substitua o arquivo",
                ):
                    renderer._native_render_scene_clips(
                        [scene], root / "clips", root / "assets", ["fullscreen"], [9 * renderer.FPS]
                    )

            run_compositor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
