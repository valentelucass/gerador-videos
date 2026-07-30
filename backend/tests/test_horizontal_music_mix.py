import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.src.core import horizontal_renderer as renderer


class HorizontalMusicMixTests(unittest.TestCase):
    def test_cta_emoji_sound_starts_when_the_sticker_appears(self) -> None:
        annotation_start = 12.5
        lines = ["DEJA TU ME GUSTA", "Y SUSCRIBETE"]

        expected_sticker_start = (
            annotation_start
            + renderer.ANNOTATION_TYPING_DELAY
            + sum(len(line) for line in lines) * renderer.CTA_TYPING_STEP
            + (len(lines) - 1) * renderer.ANNOTATION_LINE_GAP
        )

        self.assertEqual(
            renderer._native_annotation_emoji_time(annotation_start, lines, "👍"),
            expected_sticker_start,
        )

    def test_segment_cta_sticker_uses_the_same_cursor_as_its_sound(self) -> None:
        lines = ["SUSCRIBETE", "PARA MAS"]
        expected_offset = renderer._native_annotation_emoji_offset(lines, "🔔")

        self.assertEqual(
            expected_offset,
            renderer.ANNOTATION_TYPING_DELAY
            + sum(len(line) for line in lines) * renderer.CTA_TYPING_STEP
            + (len(lines) - 1) * renderer.ANNOTATION_LINE_GAP,
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                patch.object(renderer, "_native_required_stickers", return_value={"🔔": root / "bell.png"}),
                patch.object(renderer, "_run_compositor") as run_compositor,
            ):
                renderer._native_render_annotation_effect(
                    root / "source.mp4", 0, 150, 0, lines, "🔔", root / "effect.mp4", encoder_args=[]
                )

            graph = run_compositor.call_args.args[0]
            filter_graph = graph[graph.index("-filter_complex") + 1]
            self.assertIn(f"enable='gte(t,{expected_offset:.3f})*lt(t,", filter_graph)

    def test_music_cycle_preserves_pauses_in_the_imported_track(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Edge of the Shadow.mp3"

            with (
                patch.object(renderer, "_run_compositor") as run_compositor,
                patch.object(renderer, "_duration", return_value=30.0),
            ):
                cycle = renderer._native_looped_music_bed(source, 12.0, root / "music")

            self.assertEqual(cycle.name, "trilha_ciclo.m4a")
            command = run_compositor.call_args.args[0]
            audio_filter = command[command.index("-af") + 1]
            self.assertEqual(audio_filter, "aresample=48000,asetpts=PTS-STARTPTS")
            self.assertNotIn("silenceremove", audio_filter)

    def test_music_cycle_rejects_any_future_truncation_of_an_imported_track(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            with (
                patch.object(renderer, "_run_compositor"),
                patch.object(renderer, "_duration", side_effect=[173.48, 0.19]),
            ):
                with self.assertRaisesRegex(RuntimeError, "encurtou o áudio importado"):
                    renderer._native_looped_music_bed(root / "with-pauses.mp3", 600.0, root / "music")

    def test_final_mix_keeps_the_selected_music_and_uses_moderate_ducking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            filter_script = root / "mix.ffscript"

            with patch.object(renderer, "_run_compositor") as run_compositor:
                renderer._native_finalize(
                    root / "segments.ffconcat",
                    root / "narration.mp3",
                    root / "selected-song.mp3",
                    [],
                    [],
                    narration_seconds=12.0,
                    visual_duration=12.0,
                    output=root / "final.mp4",
                    filter_script=filter_script,
                )

            graph = filter_script.read_text(encoding="utf-8")
            self.assertIn(f"volume={renderer.MUSIC_BED_VOLUME:.2f}[music]", graph)
            self.assertIn(
                "sidechaincompress="
                f"threshold={renderer.MUSIC_DUCKING_THRESHOLD:.3f}:"
                f"ratio={renderer.MUSIC_DUCKING_RATIO:.1f}:"
                f"attack={renderer.MUSIC_DUCKING_ATTACK_MS}:"
                f"release={renderer.MUSIC_DUCKING_RELEASE_MS}",
                graph,
            )

            command = run_compositor.call_args.args[0]
            self.assertIn(str(root / "selected-song.mp3"), command)


if __name__ == "__main__":
    unittest.main()
