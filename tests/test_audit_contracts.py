"""Contract tests for the SynthReel audit findings.

These tests intentionally avoid the full pipeline and external services. Tests
marked as expected failures describe business rules that are not enforced yet.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.core.editor_ffmpeg import FFmpegEngine
from src.core.legendas_ass import GeradorLegendasASS
from src.core.llm_roteirista import LLMRoteirista, LLMRoteiristaError, _validar_versoes
from src.core.pexels_fetcher import PexelsFetcher, PexelsNoResultsError
from src.core.pipeline import VideoPipeline


def _scene(text: str = "Palavra segura.", search: str = "ancient ruins") -> dict[str, str]:
    return {"texto": text, "busca": search}


def _word_timestamps(words: list[str], step: float = 0.25) -> list[dict[str, float | str]]:
    return [
        {"palavra": word, "inicio": round(index * step, 3), "fim": round((index + 1) * step, 3)}
        for index, word in enumerate(words)
    ]


class DurationContractAuditTests(unittest.TestCase):
    def test_llm_validator_should_reject_scripts_below_real_audio_duration_budget(self) -> None:
        long_script = [_scene() for _ in range(12)]
        short_script = [_scene() for _ in range(6)]

        with self.assertRaises(LLMRoteiristaError):
            _validar_versoes(
                {
                    LLMRoteirista.VERSAO_LONGA: long_script,
                    LLMRoteirista.VERSAO_CURTA: short_script,
                }
            )

    def test_llm_repair_failure_aborts_without_generic_fallback(self) -> None:
        class StubResponse:
            status_code = 200

            def __init__(self, llm_response: str) -> None:
                self._llm_response = llm_response
                self.text = json.dumps({"response": llm_response})

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, str]:
                return {"response": self._llm_response}

        class StubSession:
            def __init__(self) -> None:
                self.calls: list[str] = []
                self.responses = [
                    "isso nao e json",
                    json.dumps({"versao_longa": [], "versao_curta": []}),
                ]

            def post(self, url: str, json: dict[str, object], timeout: int) -> StubResponse:
                self.calls.append(str(json["prompt"]))
                return StubResponse(self.responses.pop(0))

        session = StubSession()
        roteirista = LLMRoteirista(model="test-model", session=session)

        with self.assertRaisesRegex(LLMRoteiristaError, "sem fallback generico"):
            roteirista.gerar_roteiro("Roma antiga")

        self.assertEqual(len(session.calls), 2)
        self.assertIn("Converta a resposta abaixo", session.calls[1])

    def test_broll_break_rule_says_split_long_scene_in_half(self) -> None:
        cases = [
            (3.0, [3.0]),
            (3.1, [1.55, 1.55]),
            (7.0, [3.5, 3.5]),
        ]

        for duracao, expected in cases:
            with self.subTest(duracao=duracao):
                durations = VideoPipeline._duracoes_segmentos_visuais(
                    cena={"duracao": duracao},
                    indice=3,
                    total_cenas=8,
                    duracao_total_video=30.0,
                )

                self.assertEqual(durations, expected)


class SubtitleIntegrityAuditTests(unittest.TestCase):
    def test_caption_text_uses_official_script_words_not_whisper_words(self) -> None:
        pipeline = VideoPipeline()
        cenas = [{"texto": "Atlântida caiu rápido.", "inicio_audio": 0.0, "fim_audio": 0.9}]
        whisper = _word_timestamps(["Atlantida", "cal", "errado"])

        legendas = pipeline._gerar_timestamps_legenda(cenas, whisper)

        self.assertEqual([item["palavra"] for item in legendas], ["Atlântida", "caiu", "rápido"])

    def test_ass_generator_keeps_accents_and_limits_blocks_to_two_words(self) -> None:
        timestamps = _word_timestamps(["Atlântida", "caiu", "rápido"])

        with tempfile.TemporaryDirectory() as tmp:
            ass_path = Path(tmp) / "legendas.ass"
            GeradorLegendasASS().gerar_arquivo(timestamps, ass_path, max_palavras_linha=2)
            content = ass_path.read_text(encoding="utf-8")

        dialogue_lines = [line for line in content.splitlines() if line.startswith("Dialogue:")]
        rendered_text = [line.rsplit(",", 1)[-1] for line in dialogue_lines]

        self.assertEqual(rendered_text, ["ATLÂNTIDA CAIU", "RÁPIDO"])
        self.assertTrue(all(len(text.split()) <= 2 for text in rendered_text))

    def test_subtitle_alignment_should_not_assign_inserted_whisper_word_time_to_script_word(self) -> None:
        pipeline = VideoPipeline()
        cenas = [{"texto": "Atlântida caiu.", "inicio_audio": 0.2, "fim_audio": 0.8}]
        whisper = [
            {"palavra": "uh", "inicio": 0.0, "fim": 0.1},
            {"palavra": "Atlantida", "inicio": 0.2, "fim": 0.5},
            {"palavra": "caiu", "inicio": 0.5, "fim": 0.8},
        ]

        legendas = pipeline._gerar_timestamps_legenda(cenas, whisper)

        self.assertEqual(legendas[0]["inicio"], 0.2)
        self.assertEqual([item["palavra"] for item in legendas], ["Atlântida", "caiu"])

    def test_subtitle_alignment_interpolates_omitted_script_words_inside_scene(self) -> None:
        pipeline = VideoPipeline()
        cenas = [{"texto": "Atlântida caiu rápido.", "inicio_audio": 0.0, "fim_audio": 1.2}]
        whisper = [
            {"palavra": "Atlantida", "inicio": 0.0, "fim": 0.3},
            {"palavra": "rapido", "inicio": 0.9, "fim": 1.2},
        ]

        legendas = pipeline._gerar_timestamps_legenda(cenas, whisper)

        self.assertEqual([item["palavra"] for item in legendas], ["Atlântida", "caiu", "rápido"])
        self.assertEqual((legendas[0]["inicio"], legendas[0]["fim"]), (0.0, 0.3))
        self.assertEqual((legendas[2]["inicio"], legendas[2]["fim"]), (0.9, 1.2))
        self.assertGreaterEqual(legendas[1]["inicio"], legendas[0]["fim"])
        self.assertLessEqual(legendas[1]["fim"], legendas[2]["inicio"])

    def test_scene_timing_ignores_inserted_whisper_word_when_finding_next_script_word(self) -> None:
        pipeline = VideoPipeline()
        cenas = [
            {"texto": "Atlântida caiu.", "busca": "ancient ruins"},
            {"texto": "A cidade sumiu.", "busca": "sunken city"},
        ]
        whisper = [
            {"palavra": "uh", "inicio": 0.0, "fim": 0.1},
            {"palavra": "Atlantida", "inicio": 0.2, "fim": 0.4},
            {"palavra": "caiu", "inicio": 0.4, "fim": 0.6},
            {"palavra": "A", "inicio": 0.9, "fim": 1.0},
            {"palavra": "cidade", "inicio": 1.0, "fim": 1.2},
            {"palavra": "sumiu", "inicio": 1.2, "fim": 1.4},
        ]

        cenas_temporizadas = pipeline._calcular_tempos_cenas(cenas, whisper)

        self.assertEqual(cenas_temporizadas[0]["inicio_audio"], 0.0)
        self.assertEqual(cenas_temporizadas[0]["fim_visual"], 0.9)
        self.assertEqual(cenas_temporizadas[1]["inicio_audio"], 0.9)


class PexelsFallbackAuditTests(unittest.TestCase):
    def test_pexels_selection_order_is_portrait_video_then_landscape_video_then_photo(self) -> None:
        class StubFetcher(PexelsFetcher):
            def __init__(self) -> None:
                super().__init__(api_key="test")
                self.calls: list[tuple[str, str | None]] = []

            def _buscar_videos(self, query: str, orientacao: str) -> list[dict[str, object]]:
                self.calls.append(("video", orientacao))
                if orientacao == "landscape":
                    return [
                        {
                            "id": "landscape-video",
                            "width": 1920,
                            "height": 1080,
                            "video_files": [
                                {
                                    "file_type": "video/mp4",
                                    "link": "https://example.test/video.mp4",
                                    "width": 1920,
                                    "height": 1080,
                                }
                            ],
                        }
                    ]
                return []

            def _buscar_fotos(self, query: str, orientacao: str | None) -> list[dict[str, object]]:
                self.calls.append(("photo", orientacao))
                return []

            def _download_file(self, download_url: str, output_path: Path) -> None:
                output_path.write_bytes(b"media")

        with tempfile.TemporaryDirectory() as tmp:
            fetcher = StubFetcher()
            with patch("src.core.pexels_fetcher.random.random", return_value=0.99):
                media = fetcher.obter_midia_para_cena("ancient ruins", set(), tmp)

        self.assertEqual(fetcher.calls, [("video", "portrait"), ("video", "landscape")])
        self.assertEqual(media["tipo"], "video")
        self.assertTrue(media["precisa_de_grid"])

    def test_pexels_grid_variation_skips_portrait_and_starts_on_landscape(self) -> None:
        class StubFetcher(PexelsFetcher):
            def __init__(self) -> None:
                super().__init__(api_key="test")
                self.calls: list[tuple[str, str | None]] = []

            def _buscar_videos(self, query: str, orientacao: str) -> list[dict[str, object]]:
                self.calls.append(("video", orientacao))
                if orientacao == "landscape":
                    return [
                        {
                            "id": "landscape-video",
                            "width": 1920,
                            "height": 1080,
                            "video_files": [
                                {
                                    "file_type": "video/mp4",
                                    "link": "https://example.test/video.mp4",
                                    "width": 1920,
                                    "height": 1080,
                                }
                            ],
                        }
                    ]
                return []

            def _buscar_fotos(self, query: str, orientacao: str | None) -> list[dict[str, object]]:
                self.calls.append(("photo", orientacao))
                return []

            def _download_file(self, download_url: str, output_path: Path) -> None:
                output_path.write_bytes(b"media")

        with tempfile.TemporaryDirectory() as tmp:
            fetcher = StubFetcher()
            with patch("src.core.pexels_fetcher.random.random", return_value=0.10):
                media = fetcher.obter_midia_para_cena("ancient ruins", set(), tmp)

        self.assertEqual(fetcher.calls, [("video", "landscape")])
        self.assertEqual(media["tipo"], "video")
        self.assertTrue(media["precisa_de_grid"])
        self.assertEqual(media["orientacao"], "landscape")

    def test_pexels_empty_result_raises_no_results_error_before_pipeline_local_fallback(self) -> None:
        class EmptyFetcher(PexelsFetcher):
            def __init__(self) -> None:
                super().__init__(api_key="test")

            def _buscar_videos(self, query: str, orientacao: str) -> list[dict[str, object]]:
                return []

            def _buscar_fotos(self, query: str, orientacao: str | None) -> list[dict[str, object]]:
                return []

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PexelsNoResultsError):
                with patch("src.core.pexels_fetcher.random.random", return_value=0.99):
                    EmptyFetcher().obter_midia_para_cena("ancient ruins", set(), tmp)

    def test_pipeline_local_fallback_budget_aborts_above_fifteen_percent(self) -> None:
        percentual = VideoPipeline._validar_limite_falhas_midia(
            falhas_midia_local=1,
            total_cenas=10,
        )

        self.assertEqual(percentual, 10.0)
        with self.assertRaisesRegex(RuntimeError, "fallback local em 2/10 cenas"):
            VideoPipeline._validar_limite_falhas_midia(
                falhas_midia_local=2,
                total_cenas=10,
            )

    def test_pipeline_local_fallback_media_is_marked_for_budget_counter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = VideoPipeline()
            pipeline.temp_dir = Path(tmp)
            (pipeline.temp_dir / "fallback_texture.png").write_bytes(b"fallback")

            media = pipeline._midia_fallback(1)

        self.assertTrue(media["fallback_local"])


class AudioMixAuditTests(unittest.TestCase):
    def _capture_mix_filter(self) -> str:
        class CapturingEngine(FFmpegEngine):
            def __init__(self) -> None:
                super().__init__(ffmpeg_bin="ffmpeg")
                self.captured_args: list[str] = []

            def _tem_audio(self, input_path: Path) -> bool:
                return True

            def _run_ffmpeg(self, args, step_name):  # type: ignore[no-untyped-def]
                self.captured_args = list(args)
                return None

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "video.mp4"
            narracao = root / "narracao.mp3"
            music = root / "music.mp3"
            transition = root / "transition.mp4"
            for path in (video, narracao, music, transition):
                path.write_bytes(b"x")

            engine = CapturingEngine()
            engine.mixar_audio_final(
                input_video=video,
                narracao=narracao,
                output_path=root / "out.mp4",
                background_music=music,
                transicoes=[{"path": transition, "inicio": 1.2, "duracao": 0.4}],
                duracao=10.0,
            )

        filter_index = engine.captured_args.index("-filter_complex") + 1
        return engine.captured_args[filter_index]

    def test_audio_mix_uses_normalize_zero_to_keep_voice_stable(self) -> None:
        filter_complex = self._capture_mix_filter()

        self.assertIn("amix=inputs=3:duration=first:dropout_transition=0:normalize=0", filter_complex)

    def test_background_music_and_transition_audio_should_meet_audible_floor(self) -> None:
        filter_complex = self._capture_mix_filter()
        volumes = [float(value) for value in re.findall(r"volume=([0-9.]+)", filter_complex)]

        self.assertGreaterEqual(volumes[1], 0.16)
        self.assertGreaterEqual(volumes[2], 0.30)


if __name__ == "__main__":
    unittest.main()
