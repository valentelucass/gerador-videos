import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.src.core import horizontal_renderer as renderer


class HorizontalMusicMixTests(unittest.TestCase):
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
