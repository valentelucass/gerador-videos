"""Contract tests for the isolated horizontal phase-2 renderer."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
import json
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import Mock, patch

from src.scripts import renderizar_horizontal as renderer
from src.utils import theme_lock


def _cena_auditada(indice: int, texto: str) -> renderer.CenaAuditada:
    return renderer.CenaAuditada(
        indice=indice,
        template_id=4,
        texto=texto,
        textos_tela=(texto,),
        midias=(),
    )


class HITLAuditTests(unittest.TestCase):
    def test_ia_prompt_still_present_blocks_even_with_image(self) -> None:
        metadata = {
            "cenas": [
                {
                    "texto": "Texto oficial da cena.",
                    "template_id": 1,
                    "fonte_midia": "ia",
                    "prompt_ou_busca": "Ancient Rome senate",
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            tema_dir = Path(tmp)
            (tema_dir / "cena_01_PROMPT_IA.txt").write_text("prompt", encoding="utf-8")
            (tema_dir / "cena_01.png").write_bytes(b"image")

            with self.assertRaisesRegex(renderer.AuditoriaHITLError, "prompt IA ainda existe"):
                renderer.auditar_hitl(tema_dir, metadata)

    def test_ia_requires_one_unique_visual_asset(self) -> None:
        metadata = {
            "cenas": [
                {
                    "texto": "Texto oficial da cena.",
                    "template_id": 1,
                    "fonte_midia": "ia",
                    "prompt_ou_busca": "Ancient Rome senate",
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            tema_dir = Path(tmp)
            (tema_dir / "cena_01.png").write_bytes(b"image")
            auditadas = renderer.auditar_hitl(tema_dir, metadata)

            self.assertEqual(auditadas[0].midias[0].path.name, "cena_01.png")
            self.assertEqual(auditadas[0].midias[0].fonte_midia, "ia")

            (tema_dir / "cena_01_final.jpg").write_bytes(b"second image")
            with self.assertRaisesRegex(renderer.AuditoriaHITLError, "midia ambigua"):
                renderer.auditar_hitl(tema_dir, metadata)

    def test_ia_accepts_curated_mp4(self) -> None:
        metadata = {
            "cenas": [
                {
                    "texto": "Texto oficial da cena.",
                    "template_id": 1,
                    "fonte_midia": "ia",
                    "prompt_ou_busca": "Ancient Rome senate",
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            tema_dir = Path(tmp)
            (tema_dir / "cena_01.mp4").write_bytes(b"video")
            auditadas = renderer.auditar_hitl(tema_dir, metadata)

        self.assertEqual(auditadas[0].midias[0].tipo, "video")

    def test_local_accepts_canonical_asset_prepared_by_substring_ingestion(self) -> None:
        metadata = {
            "cenas": [
                {
                    "texto": "Soldados compartilham um cigarro durante a noite.",
                    "template_id": 1,
                    "fonte_midia": "local",
                    "busca_local": "sharing_cigarette",
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            tema_dir = Path(tmp)
            asset = tema_dir / "cena_01_local.jpeg"
            asset.write_bytes(b"image")
            auditadas = renderer.auditar_hitl(tema_dir, metadata)

        self.assertEqual(auditadas[0].midias[0].fonte_midia, "local")
        self.assertEqual(auditadas[0].midias[0].path.name, "cena_01_local.jpeg")

    def test_multislot_template_reports_missing_physical_slot_when_fallback_fails(self) -> None:
        metadata = {
            "cenas": [
                {
                    "texto": "Comparacao entre duas fontes historicas.",
                    "template_id": 3,
                    "midias": [
                        {"fonte_midia": "pexels", "prompt_ou_busca": "roman forum"},
                        {"fonte_midia": "ia", "prompt_ou_busca": "roman emperor portrait"},
                    ],
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            tema_dir = Path(tmp)
            (tema_dir / "cena_01_A_pexels.mp4").write_bytes(b"video")

            class FetcherSemResultado:
                def obter_midia_para_cena(self, **_kwargs):  # type: ignore[no-untyped-def]
                    raise renderer.PexelsFetcherError("sem resultado")

            with self.assertRaisesRegex(renderer.AuditoriaHITLError, "cena 01 slot B"):
                renderer.auditar_hitl(
                    tema_dir,
                    metadata,
                    pexels_fetcher=FetcherSemResultado(),  # type: ignore[arg-type]
                )

    def test_missing_explicit_media_source_is_rejected(self) -> None:
        metadata = {
            "cenas": [
                {
                    "texto": "Cena sem contrato de fonte.",
                    "template_id": 1,
                    "prompt_ou_busca": "roman forum",
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            tema_dir = Path(tmp)
            (tema_dir / "cena_01_pexels.mp4").write_bytes(b"video")
            with self.assertRaisesRegex(renderer.AuditoriaHITLError, "fonte_midia.*obrigatoria"):
                renderer.auditar_hitl(tema_dir, metadata)

    def test_screen_text_string_is_one_item_not_character_sequence(self) -> None:
        metadata = {
            "cenas": [
                {
                    "texto": "Narracao oficial.",
                    "template_id": 1,
                    "fonte_midia": "pexels",
                    "prompt_ou_busca": "roman forum",
                    "textos_tela": "Império Romano",
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            tema_dir = Path(tmp)
            (tema_dir / "cena_01_pexels.mp4").write_bytes(b"video")
            cena = renderer.auditar_hitl(tema_dir, metadata)[0]

        self.assertEqual(cena.textos_tela, ("Império Romano",))

    def test_text_layout_without_explicit_screen_text_is_rejected(self) -> None:
        metadata = {
            "cenas": [
                {
                    "texto": "Narracao oficial.",
                    "template_id": 6,
                    "fonte_midia": "pexels",
                    "prompt_ou_busca": "roman forum",
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            tema_dir = Path(tmp)
            (tema_dir / "cena_01_pexels.mp4").write_bytes(b"video")
            with self.assertRaisesRegex(renderer.AuditoriaHITLError, "exige 'textos_tela'"):
                renderer.auditar_hitl(tema_dir, metadata)

    def test_screen_text_list_rejects_non_string_items(self) -> None:
        metadata = {
            "cenas": [
                {
                    "texto": "Narracao oficial.",
                    "template_id": 6,
                    "fonte_midia": "pexels",
                    "prompt_ou_busca": "roman forum",
                    "textos_tela": ["Roma", 123],
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            tema_dir = Path(tmp)
            (tema_dir / "cena_01_pexels.mp4").write_bytes(b"video")
            with self.assertRaisesRegex(renderer.AuditoriaHITLError, "somente strings"):
                renderer.auditar_hitl(tema_dir, metadata)

    def test_layout_rejects_extra_slots_that_factory_would_ignore(self) -> None:
        metadata = {
            "cenas": [
                {
                    "texto": "Narracao oficial.",
                    "template_id": 1,
                    "midias": [
                        {"fonte_midia": "pexels", "prompt_ou_busca": "roman forum"},
                        {"fonte_midia": "pexels", "prompt_ou_busca": "roman road"},
                    ],
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(renderer.AuditoriaHITLError, "exige exatamente 1 midias"):
                renderer.auditar_hitl(Path(tmp), metadata)

    def test_template_11_requires_four_explicit_single_line_topics(self) -> None:
        metadata = {
            "cenas": [
                {
                    "texto": "A lista precisa manter quatro pontos claros.",
                    "template_id": 11,
                    "fonte_midia": "pexels",
                    "prompt_ou_busca": "roman forum",
                    "textos_tela": ["Primeiro", "Segundo", "Terceiro"],
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            tema_dir = Path(tmp)
            (tema_dir / "cena_01_pexels.mp4").write_bytes(b"video")
            with self.assertRaisesRegex(renderer.AuditoriaHITLError, "exatamente 4 topicos"):
                renderer.auditar_hitl(tema_dir, metadata)

    def test_template_11_accepts_four_explicit_topics(self) -> None:
        metadata = {
            "cenas": [
                {
                    "texto": "A lista apresenta quatro pontos claros e independentes.",
                    "template_id": 11,
                    "fonte_midia": "pexels",
                    "prompt_ou_busca": "roman forum",
                    "textos_tela": ["Primeiro", "Segundo", "Terceiro", "Quarto"],
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            tema_dir = Path(tmp)
            (tema_dir / "cena_01_pexels.mp4").write_bytes(b"video")
            cena = renderer.auditar_hitl(tema_dir, metadata)[0]

        self.assertEqual(cena.textos_tela, ("Primeiro", "Segundo", "Terceiro", "Quarto"))

    def test_template_12_passes_hitl_with_the_future_list_contract(self) -> None:
        metadata = {
            "cenas": [
                {
                    "texto": "A nova lista vai introduzir topicos em etapas controladas.",
                    "template_id": 12,
                    "fonte_midia": "local",
                    "buscas_locais": ["winter scene"],
                    "textos_tela": ["Primeiro", "Segundo", "Terceiro", "Quarto"],
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            tema_dir = Path(tmp)
            (tema_dir / "cena_01_local.jpeg").write_bytes(b"image")
            cena = renderer.auditar_hitl(tema_dir, metadata)[0]

        self.assertEqual(cena.template_id, 12)
        self.assertEqual(cena.midias[0].papel_layout, "esquerda")

    def test_template_12_accepts_accumulated_topics_with_trailing_slots_empty(self) -> None:
        metadata = {
            "cenas": [
                {
                    "texto": "A primeira unidade introduz o topico inicial.",
                    "template_id": 12,
                    "fonte_midia": "local",
                    "busca_local": "winter scene",
                    "textos_tela": ["Primeiro", "", "", ""],
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            tema_dir = Path(tmp)
            (tema_dir / "cena_01_local.jpeg").write_bytes(b"image")
            cena = renderer.auditar_hitl(tema_dir, metadata)[0]

        self.assertEqual(cena.textos_tela, ("Primeiro", "", "", ""))


class WhisperAlignmentTests(unittest.TestCase):
    def test_inserted_whisper_word_does_not_shift_scene_cut(self) -> None:
        cenas = [
            _cena_auditada(1, "Atlântida caiu."),
            _cena_auditada(2, "A cidade sumiu."),
        ]
        timestamps = [
            {"palavra": "uh", "inicio": 0.0, "fim": 0.1},
            {"palavra": "Atlantida", "inicio": 0.2, "fim": 0.4},
            {"palavra": "caiu", "inicio": 0.4, "fim": 0.6},
            {"palavra": "A", "inicio": 0.9, "fim": 1.0},
            {"palavra": "cidade", "inicio": 1.0, "fim": 1.2},
            {"palavra": "sumiu", "inicio": 1.2, "fim": 1.4},
        ]

        temporizadas = renderer.alinhar_cenas_com_whisper(cenas, timestamps, 1.6)

        self.assertEqual(temporizadas[0].inicio, 0.0)
        self.assertEqual(temporizadas[0].fim, 0.9)
        self.assertEqual(temporizadas[1].inicio, 0.9)
        self.assertEqual(temporizadas[1].fim, 1.6)

    def test_omitted_word_is_interpolated_from_official_json(self) -> None:
        cenas = [
            _cena_auditada(1, "Roma cresceu rapidamente."),
            _cena_auditada(2, "O império caiu."),
        ]
        timestamps = [
            {"palavra": "Roma", "inicio": 0.1, "fim": 0.3},
            {"palavra": "rapidamente", "inicio": 0.7, "fim": 0.9},
            {"palavra": "O", "inicio": 1.2, "fim": 1.3},
            {"palavra": "imperio", "inicio": 1.3, "fim": 1.6},
            {"palavra": "caiu", "inicio": 1.6, "fim": 1.9},
        ]

        temporizadas = renderer.alinhar_cenas_com_whisper(cenas, timestamps, 2.0)

        self.assertEqual(temporizadas[0].fim, 1.2)
        self.assertGreater(temporizadas[0].cobertura_whisper, 0.5)
        self.assertEqual(temporizadas[-1].fim, 2.0)

    def test_empty_whisper_result_aborts(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Whisper nao retornou"):
            renderer.alinhar_cenas_com_whisper(
                [_cena_auditada(1, "Texto oficial.")],
                [],
                1.0,
            )

    def test_scene_without_any_lexical_anchor_aborts(self) -> None:
        cenas = [
            _cena_auditada(1, "Roma cresceu."),
            _cena_auditada(2, "Xyzzy plugh."),
        ]
        timestamps = [
            {"palavra": "Roma", "inicio": 0.1, "fim": 0.3},
            {"palavra": "cresceu", "inicio": 0.3, "fim": 0.6},
            {"palavra": "termos", "inicio": 1.0, "fim": 1.2},
            {"palavra": "diferentes", "inicio": 1.2, "fim": 1.5},
        ]

        with self.assertRaisesRegex(RuntimeError, "nenhuma ancora lexical nas cenas: 02"):
            renderer.alinhar_cenas_com_whisper(cenas, timestamps, 1.8)

    def test_non_finite_whisper_timestamp_aborts_early(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "nao monotonicos"):
            renderer.alinhar_cenas_com_whisper(
                [_cena_auditada(1, "Texto oficial.")],
                [{"palavra": "Texto", "inicio": float("nan"), "fim": 0.4}],
                1.0,
            )

    def test_unicode_tokenizer_preserves_polish_words(self) -> None:
        self.assertEqual(
            renderer._tokenizar("Łódź była stolicą. Zażółć gęślą jaźń."),
            ["Łódź", "była", "stolicą", "Zażółć", "gęślą", "jaźń"],
        )

    def test_tts_master_separator_adds_only_missing_terminal_punctuation(self) -> None:
        self.assertEqual(renderer._texto_tts_com_fim_de_frase("Primeira cena"), "Primeira cena.")
        self.assertEqual(renderer._texto_tts_com_fim_de_frase("Segunda cena!"), "Segunda cena!")


class SceneRetentionTests(unittest.TestCase):
    def test_scene_with_exactly_nine_seconds_is_allowed(self) -> None:
        cena = renderer.CenaTemporizada(
            _cena_auditada(7, "Texto oficial curto."),
            inicio=0.0,
            fim_fala=8.9,
            fim=9.0,
            cobertura_whisper=1.0,
        )

        renderer.validar_duracao_maxima_cenas([cena])

    def test_scene_above_nine_seconds_fails_with_retention_context(self) -> None:
        cena = renderer.CenaTemporizada(
            _cena_auditada(7, "Texto oficial curto."),
            inicio=0.0,
            fim_fala=9.0,
            fim=9.001,
            cobertura_whisper=1.0,
        )

        with self.assertRaisesRegex(
            renderer.TempoCenaExcedidoError,
            r"Cena 07.*9\.001s.*retencao do YouTube",
        ):
            renderer.validar_duracao_maxima_cenas([cena])

    def test_retention_failure_happens_before_ffmpeg_preflight_and_scene_render(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tema_dir = root / "lotes" / "nicho" / "tema"
            run_dir = root / "run"
            tema_dir.mkdir(parents=True)
            run_dir.mkdir()
            audio = run_dir / "narracao_mestre.mp3"
            audio.write_bytes(b"audio")
            track = root / "track.mp3"
            arrow = root / "arrow.png"
            track.write_bytes(b"track")
            arrow.write_bytes(b"arrow")
            cena = renderer.CenaAuditada(
                1,
                1,
                "Texto oficial curto.",
                (),
                (),
            )
            temporizada = renderer.CenaTemporizada(
                cena,
                inicio=0.0,
                fim_fala=9.0,
                fim=9.001,
                cobertura_whisper=1.0,
            )
            tts = Mock()
            tts.sintetizar_sync.return_value = str(audio)
            whisper = Mock()
            whisper.extrair_timestamps.return_value = []

            with (
                patch.object(
                    renderer,
                    "_validar_diretorio_tema",
                    return_value=(tema_dir, "nicho", "tema"),
                ),
                patch.object(renderer, "_ler_metadata", return_value={"idioma": "pl-PL"}),
                patch.object(renderer, "auditar_hitl", return_value=[cena]),
                patch.object(
                    renderer,
                    "_sortear_assets_globais",
                    return_value=(
                        renderer.AssetGlobal("trilha", track, "audio"),
                        renderer.AssetGlobal("seta", arrow, "video", False),
                    ),
                ),
                patch.object(
                    renderer,
                    "_resolver_executavel",
                    side_effect=lambda value, _nome: value,
                ),
                patch.object(renderer, "_sondar_midias_cenas", return_value=[cena]),
                patch.object(renderer, "_sondar_assets_globais"),
                patch.object(renderer, "_mapear_pool_transicoes_horizontal", return_value=[]),
                patch.object(renderer, "_resolver_fonte_deterministica", return_value=None),
                patch.object(renderer, "_criar_diretorio_execucao", return_value=run_dir),
                patch.object(renderer, "_duracao_audio", return_value=9.001),
                patch.object(renderer, "alinhar_cenas_com_whisper", return_value=[temporizada]),
                patch.object(renderer, "_validar_recursos_ffmpeg") as preflight,
                patch.object(renderer, "_renderizar_cena") as render_scene,
            ):
                with self.assertRaises(renderer.TempoCenaExcedidoError):
                    renderer.renderizar_horizontal(
                        tema_dir,
                        tts_engine=tts,
                        whisper_sync=whisper,
                        ffmpeg_bin="ffmpeg",
                        ffprobe_bin="ffprobe",
                    )

            preflight.assert_not_called()
            render_scene.assert_not_called()


class FFmpegGraphTests(unittest.TestCase):
    def test_transition_pool_scans_subfolders_and_selection_is_per_cut(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "wipes"
            nested.mkdir()
            video = nested / "wipe.mp4"
            second = root / "glitches" / "glitch.mp4"
            second.parent.mkdir()
            video.write_bytes(b"video")
            second.write_bytes(b"video")
            (root / "seta_apontamento.png").write_bytes(b"arrow")

            def fake_probe(path, _ffprobe):  # type: ignore[no-untyped-def]
                if path == video:
                    return {
                        "streams": [
                            {
                                "codec_type": "video",
                                "duration": "0.8",
                                "width": 1920,
                                "height": 1080,
                            },
                            {
                                "codec_type": "audio",
                                "duration": "0.8",
                                "channels": 1,
                            },
                        ]
                    }
                return {
                    "streams": [
                        {
                            "codec_type": "video",
                            "duration": "0.4",
                            "width": 1920,
                            "height": 1080,
                        },
                        {"codec_type": "audio", "duration": "0.4", "channels": 2},
                    ]
                }

            with (
                patch.object(renderer, "OVERLAYS_DIR", root),
                patch.object(renderer, "_ffprobe_json", side_effect=fake_probe),
                patch.object(renderer.random, "choice", return_value=None) as choice,
            ):
                pool = renderer._mapear_pool_transicoes_horizontal("ffprobe")
                selected_asset = next(item for item in pool if item.path == video.resolve())
                choice.return_value = selected_asset
                selecionadas = renderer._selecionar_transicoes_horizontais(
                    pool,
                    [1.0, 2.0],
                    5.0,
                )

        self.assertEqual(len(pool), 2)
        self.assertEqual(choice.call_count, 2)
        self.assertEqual([item.input_index for item in selecionadas], [3, 4])
        self.assertTrue(all(item.path == video.resolve() for item in selecionadas))
        self.assertEqual([item.inicio_visual for item in selecionadas], [0.6, 1.6])
        self.assertTrue(
            all(item.duracao_video == item.duracao_audio == 0.8 for item in selecionadas)
        )

    def test_transition_pool_ignores_video_without_embedded_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            collection = root / "film_burns"
            collection.mkdir()
            silent = collection / "silent.mov"
            av = collection / "with_sound.mov"
            silent.write_bytes(b"silent")
            av.write_bytes(b"av")

            def fake_probe(path, _ffprobe):  # type: ignore[no-untyped-def]
                streams = [
                    {
                        "codec_type": "video",
                        "duration": "1.0",
                        "width": 1920,
                        "height": 1080,
                    }
                ]
                if path == av:
                    streams.append(
                        {"codec_type": "audio", "duration": "1.0", "channels": 2}
                    )
                return {"streams": streams}

            with (
                patch.object(renderer, "OVERLAYS_DIR", root),
                patch.object(renderer, "_ffprobe_json", side_effect=fake_probe),
            ):
                pool = renderer._mapear_pool_transicoes_horizontal("ffprobe")

        self.assertEqual([item.path for item in pool], [av.resolve()])

    def test_native_transition_duration_keeps_audio_and_video_synchronized(self) -> None:
        asset = renderer.TransicaoHorizontal(
            path=Path("film_burn.mov"),
            duracao_video=2.28,
            duracao_audio=2.28,
            canais_audio=2,
        )
        with patch.object(renderer.random, "choice", return_value=asset):
            selected = renderer._selecionar_transicoes_horizontais(
                [asset],
                [5.0],
                10.0,
            )[0]

        self.assertEqual(selected.inicio_visual, 3.86)
        self.assertEqual(selected.duracao_video, 2.28)
        self.assertEqual(selected.duracao_audio, 2.28)

    def test_scene_video_with_header_but_no_duration_is_rejected_before_tts(self) -> None:
        media = renderer.MidiaAuditada(
            1,
            None,
            "pexels",
            "principal",
            Path("cena_01_pexels.mp4"),
            "video",
        )
        cena = renderer.CenaAuditada(1, 1, "Texto oficial.", (), (media,))
        payload = {
            "streams": [
                {"codec_type": "video", "width": 1920, "height": 1080}
            ],
            "format": {},
        }
        with patch.object(renderer, "_ffprobe_json", return_value=payload):
            with self.assertRaisesRegex(renderer.AuditoriaHITLError, "duracao fisica"):
                renderer._sondar_midias_cenas([cena], "ffprobe")

    def test_global_audio_asset_with_stream_but_no_duration_is_rejected(self) -> None:
        fake_asset = renderer.AssetGlobal("trilha", Path("trilha.mp3"), "audio")
        payload = {"streams": [{"codec_type": "audio"}], "format": {}}
        with patch.object(renderer, "_ffprobe_json", return_value=payload):
            with self.assertRaisesRegex(renderer.AuditoriaHITLError, "duracao fisica"):
                renderer._sondar_assets_globais((fake_asset,), "ffprobe")

    def test_static_background_pool_keeps_only_ffprobe_valid_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid = root / "valid.jpg"
            invalid = root / "invalid.png"
            valid.write_bytes(b"valid")
            invalid.write_bytes(b"invalid")

            def fake_probe(path, _ffprobe):  # type: ignore[no-untyped-def]
                if path == valid.resolve():
                    return {
                        "streams": [
                            {"codec_type": "video", "width": 1920, "height": 1080}
                        ]
                    }
                raise RuntimeError("arquivo corrompido")

            with (
                patch.object(renderer, "FUNDOS_ESTATICOS_DIR", root),
                patch.object(renderer, "_ffprobe_json", side_effect=fake_probe),
            ):
                fundos = renderer._mapear_fundos_estaticos("ffprobe")

        self.assertEqual(fundos, [valid.resolve()])

    def test_static_background_is_assigned_only_to_the_affected_templates(self) -> None:
        background = Path("fundo.jpg").resolve()
        target = _cena_auditada(1, "Texto oficial.")
        regular = renderer.CenaAuditada(2, 1, "Outra cena.", (), ())

        with patch.object(renderer, "_mapear_fundos_estaticos", return_value=[background]):
            cenas = renderer._atribuir_fundos_estaticos([target, regular], "ffprobe")

        self.assertEqual(cenas[0].fundo_estatico, background)
        self.assertIsNone(cenas[1].fundo_estatico)

    def test_global_graph_has_dynamic_visual_and_audio_transitions(self) -> None:
        transicoes = [
            renderer.TransicaoSelecionada(
                path=Path("wipe.mp4"),
                input_index=3,
                corte=1.234,
                inicio_visual=0.734,
                duracao_video=1.0,
                duracao_audio=0.8,
                canais_audio=1,
            ),
            renderer.TransicaoSelecionada(
                path=Path("glitch.wav"),
                input_index=4,
                corte=7.89,
                inicio_visual=7.69,
                duracao_video=0.4,
                duracao_audio=0.4,
                canais_audio=2,
            ),
        ]
        graph = renderer.construir_filtro_global(10.0, transicoes)

        self.assertIn("[trilha][voz_sidechain]sidechaincompress", graph)
        self.assertIn("[3:v]fps=30", graph)
        self.assertIn("[4:v]fps=30", graph)
        self.assertIn("aformat=channel_layouts=mono", graph)
        self.assertIn("aformat=channel_layouts=stereo", graph)
        self.assertIn("adelay=734:all=1", graph)
        self.assertIn("adelay=7690:all=1", graph)
        self.assertIn("asetpts=PTS-STARTPTS,volume=3.0,adelay=734:all=1", graph)
        self.assertNotIn("blend=all_mode=screen", graph)
        self.assertNotIn("[textura]", graph)
        video_base = graph.split("[video_base]", 1)[0]
        self.assertNotIn("tpad=", video_base)
        self.assertNotIn("setpts=", video_base)
        self.assertIn("amix=inputs=4:duration=longest", graph)
        self.assertIn("alimiter=limit=0.95:latency=1", graph)

    def test_global_graph_concatenates_multiple_tracks_before_ducking(self) -> None:
        graph = renderer.construir_filtro_global(
            620.0,
            (),
            indices_trilhas=(2, 3, 4),
        )

        self.assertIn("[2:a]aresample=48000", graph)
        self.assertIn("[3:a]aresample=48000", graph)
        self.assertIn("[4:a]aresample=48000", graph)
        self.assertIn("concat=n=3:v=0:a=1[playlist_bruta]", graph)
        self.assertIn("[playlist_bruta]atrim=duration=620.000", graph)
        self.assertIn("[trilha][voz_sidechain]sidechaincompress", graph)

    def test_dynamic_playlist_covers_master_and_uses_multiple_sources_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            faixas = []
            for nome, duracao in (("a.mp3", 4.0), ("b.mp3", 5.0), ("c.mp3", 6.0)):
                path = root / nome
                path.write_bytes(b"track")
                faixas.append(renderer.TrilhaComDuracao(path, duracao))

            playlist = renderer._montar_playlist_dinamica(faixas, 12.0)

        self.assertGreaterEqual(sum(faixa.duracao for faixa in playlist), 12.0)
        self.assertGreaterEqual(len({faixa.path for faixa in playlist}), 2)
        self.assertTrue(
            all(atual.path != proxima.path for atual, proxima in zip(playlist, playlist[1:]))
        )

    def test_drawtext_gets_explicit_windows_safe_font_path(self) -> None:
        graph = "[base]drawtext=text='Roma':x=0:y=0[vout]"
        decorated = renderer._injetar_fonte_drawtext(
            graph,
            Path("C:/Windows/Fonts/arial.ttf"),
        )

        self.assertIn(
            "drawtext=fontfile='C\\:/Windows/Fonts/arial.ttf':expansion=none:",
            decorated,
        )

    def test_layout_factory_escapes_literal_apostrophe_for_ffmpeg_parser(self) -> None:
        escaped = renderer.LayoutFactory._escape_drawtext("d'Ávila")
        self.assertEqual(escaped, "d'" + ("\\" * 3) + "''Ávila")

    def test_scene_command_uses_layout_mapping_order_and_media_loops(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            left = root / "left.jpg"
            right = root / "right.mp4"
            output = root / "scene.mp4"
            arrow = root / "arrow.png"
            background = root / "background.jpg"
            left.write_bytes(b"image")
            right.write_bytes(b"video")
            arrow.write_bytes(b"arrow")
            background.write_bytes(b"background")
            cena = renderer.CenaAuditada(
                indice=1,
                template_id=3,
                texto="Texto oficial.",
                textos_tela=("Roma",),
                midias=(
                    renderer.MidiaAuditada(1, "A", "ia", "esquerda", left, "imagem"),
                    renderer.MidiaAuditada(2, "B", "pexels", "direita", right, "video"),
                ),
                fundo_estatico=background,
            )
            temporizada = renderer.CenaTemporizada(cena, 0.0, 1.5, 2.0, 1.0)
            captured: list[str] = []

            def fake_execute(args, etapa):  # type: ignore[no-untyped-def]
                captured.extend(str(arg) for arg in args)
                output.write_bytes(b"rendered")
                return subprocess.CompletedProcess(args, 0, "", "")

            with (
                patch.object(renderer.LayoutFactory, "build_filter_complex", return_value="nullsrc[vout]") as factory,
                patch.object(renderer, "_executar", side_effect=fake_execute),
            ):
                renderer._renderizar_cena(
                    temporizada,
                    output,
                    ffmpeg="ffmpeg",
                    opcoes_encoder_video=renderer._opcoes_h264_amf(),
                    fonte=Path("C:/Windows/Fonts/arial.ttf"),
                    seta_path=arrow,
                )

        factory.assert_called_once_with(
            3,
            {
                "esquerda": str(left),
                "direita": str(right),
                "seta": str(arrow),
                "fundo_estatico": str(background),
            },
            ("Roma",),
            indices_imagens=frozenset({0}),
            total_frames=60,
        )
        self.assertLess(captured.index("-loop"), captured.index(str(left)))
        self.assertLess(captured.index("-stream_loop"), captured.index(str(right)))
        self.assertIn(str(arrow), captured)
        self.assertIn(str(background), captured)
        self.assertIn("h264_amf", captured)

    def test_final_command_uses_one_input_per_dynamic_transition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "cenas.ffconcat"
            audio = root / "narracao.mp3"
            track = root / "trilha.mp3"
            output = root / "final.mp4"
            manifest.write_text("ffconcat version 1.0\n", encoding="utf-8")
            audio.write_bytes(b"audio")
            track.write_bytes(b"track")
            captured: list[str] = []
            transicoes = [
                renderer.TransicaoSelecionada(
                    path=root / "wipe.mp4",
                    input_index=3,
                    corte=2.5,
                    inicio_visual=2.0,
                    duracao_video=1.0,
                    duracao_audio=0.5,
                    canais_audio=2,
                )
            ]
            transicoes[0].path.write_bytes(b"transition")

            def fake_execute(args, etapa):  # type: ignore[no-untyped-def]
                captured.extend(str(arg) for arg in args)
                output.write_bytes(b"rendered")
                return subprocess.CompletedProcess(args, 0, "", "")

            with patch.object(renderer, "_executar", side_effect=fake_execute):
                renderer._renderizar_compilacao_final(
                    manifest=manifest,
                    audio_mestre=audio,
                    output_path=output,
                    duracao=10.0,
                    transicoes=transicoes,
                    ffmpeg="ffmpeg",
                    trilha_path=track,
                    opcoes_encoder_video=renderer._opcoes_h264_amf(),
                )

        track_index = captured.index(str(track))
        self.assertEqual(captured[track_index - 1], "-i")
        self.assertNotIn("-stream_loop", captured)
        self.assertEqual(captured.count("-i"), 4)
        self.assertIn(str(transicoes[0].path), captured)
        self.assertIn("h264_amf", captured)
        self.assertIn("-pix_fmt", captured)
        self.assertIn("+faststart", captured)
        self.assertIn("-async", captured)
        self.assertIn("-vsync", captured)
        self.assertIn("[vfinal]", captured)
        self.assertIn("[afinal]", captured)

    def test_final_command_maps_all_playlist_tracks_before_transition_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "cenas.ffconcat"
            audio = root / "narracao.mp3"
            tracks = (root / "trilha_a.mp3", root / "trilha_b.mp3")
            transition = root / "wipe.mp4"
            output = root / "final.mp4"
            manifest.write_text("ffconcat version 1.0\n", encoding="utf-8")
            audio.write_bytes(b"audio")
            for track in tracks:
                track.write_bytes(b"track")
            transition.write_bytes(b"transition")
            captured: list[str] = []

            def fake_execute(args, etapa):  # type: ignore[no-untyped-def]
                captured.extend(str(arg) for arg in args)
                output.write_bytes(b"rendered")
                return subprocess.CompletedProcess(args, 0, "", "")

            transicoes = [
                renderer.TransicaoSelecionada(
                    path=transition,
                    input_index=4,
                    corte=2.0,
                    inicio_visual=1.6,
                    duracao_video=0.8,
                    duracao_audio=0.8,
                    canais_audio=2,
                )
            ]
            with patch.object(renderer, "_executar", side_effect=fake_execute):
                renderer._renderizar_compilacao_final(
                    manifest=manifest,
                    audio_mestre=audio,
                    output_path=output,
                    duracao=10.0,
                    transicoes=transicoes,
                    ffmpeg="ffmpeg",
                    trilhas_paths=tracks,
                    opcoes_encoder_video=renderer._opcoes_libx264(),
                )

        self.assertLess(captured.index(str(tracks[0])), captured.index(str(tracks[1])))
        self.assertLess(captured.index(str(tracks[1])), captured.index(str(transition)))
        graph = captured[captured.index("-filter_complex") + 1]
        self.assertIn("concat=n=2:v=0:a=1[playlist_bruta]", graph)
        self.assertIn("[4:a]", graph)
        self.assertIn("libx264", captured)

    def test_amf_preflight_failure_selects_libx264_fallback(self) -> None:
        def fake_execute(args, _etapa):  # type: ignore[no-untyped-def]
            if "-filters" in args:
                return subprocess.CompletedProcess(args, 0, "zoompan\nsidechaincompress\n", "")
            if "-encoders" in args:
                return subprocess.CompletedProcess(args, 0, "V..... h264_amf\n", "")
            raise RuntimeError("driver AMD indisponivel")

        logger = Mock()
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(renderer, "_executar", side_effect=fake_execute),
                patch.object(renderer, "get_logger", return_value=logger),
            ):
                opcoes = renderer._validar_recursos_ffmpeg("ffmpeg", Path(tmp))

        self.assertEqual(opcoes, renderer._opcoes_libx264())
        logger.warning.assert_called_once()

    def test_amf_disabled_by_flag_selects_libx264_without_preflight(self) -> None:
        def fake_execute(args, _etapa):  # type: ignore[no-untyped-def]
            self.assertIn("-filters", args)
            return subprocess.CompletedProcess(args, 0, "zoompan\nsidechaincompress\n", "")

        with (
            patch.dict(os.environ, {"SYNTHREEL_ENABLE_AMF": "off"}),
            patch.object(renderer, "_executar", side_effect=fake_execute) as executar,
            patch.object(renderer, "get_logger", return_value=Mock()),
        ):
            opcoes = renderer._validar_recursos_ffmpeg("ffmpeg", Path("temp"))

        self.assertEqual(opcoes, renderer._opcoes_libx264())
        executar.assert_called_once()

    def test_concat_manifest_declares_each_measured_clip_duration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            primeiro = root / "cena_01_render.mp4"
            segundo = root / "cena_02_render.mp4"
            manifest = root / "cenas.ffconcat"

            renderer._escrever_manifest_concat(
                manifest,
                (primeiro, segundo),
                (7.54, 5.96),
            )

            self.assertEqual(
                manifest.read_text(encoding="utf-8").splitlines(),
                [
                    "ffconcat version 1.0",
                    f"file '{primeiro.resolve().as_posix()}'",
                    "duration 7.540",
                    f"file '{segundo.resolve().as_posix()}'",
                    "duration 5.960",
                ],
            )


class ResumeModeTests(unittest.TestCase):
    def _cena_temporizada(self, media: Path) -> renderer.CenaTemporizada:
        cena = renderer.CenaAuditada(
            indice=3,
            template_id=1,
            texto="Texto oficial para cache.",
            textos_tela=(),
            midias=(
                renderer.MidiaAuditada(
                    1,
                    None,
                    "local",
                    "principal",
                    media,
                    "imagem",
                ),
            ),
        )
        return renderer.CenaTemporizada(cena, 10.0, 12.0, 13.0, 1.0)

    def test_hash_resume_changes_when_media_or_duration_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "scene.jpg"
            media.write_bytes(b"first-image")
            temporizada = self._cena_temporizada(media)
            metadata = {"fonte_midia": "local", "buscas_locais": ["scene"]}

            primeiro = renderer._hash_entrada_cena(
                temporizada,
                metadata_cena=metadata,
                fonte=None,
                seta_path=root / "unused-arrow.png",
            )
            media.write_bytes(b"changed-image")
            segundo = renderer._hash_entrada_cena(
                temporizada,
                metadata_cena=metadata,
                fonte=None,
                seta_path=root / "unused-arrow.png",
            )
            alterada = renderer.CenaTemporizada(
                temporizada.cena,
                10.0,
                12.0,
                13.1,
                1.0,
            )
            terceiro = renderer._hash_entrada_cena(
                alterada,
                metadata_cena=metadata,
                fonte=None,
                seta_path=root / "unused-arrow.png",
            )

        self.assertNotEqual(primeiro, segundo)
        self.assertNotEqual(segundo, terceiro)

    def test_collision_same_size_and_mtime_different_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "scene.jpg"
            timestamp_ns = 1_700_000_000_000_000_000
            media.write_bytes(b"A" * 4096)
            os.utime(media, ns=(timestamp_ns, timestamp_ns))
            temporizada = self._cena_temporizada(media)
            metadata = {"fonte_midia": "local", "buscas_locais": ["scene"]}
            hash_anterior = renderer._hash_entrada_cena(
                temporizada,
                metadata_cena=metadata,
                fonte=None,
                seta_path=root / "unused-arrow.png",
            )
            assinatura_anterior = renderer._assinatura_arquivo_para_resume(media)

            media.write_bytes(b"B" * 4096)
            os.utime(media, ns=(timestamp_ns, timestamp_ns))
            hash_atual = renderer._hash_entrada_cena(
                temporizada,
                metadata_cena=metadata,
                fonte=None,
                seta_path=root / "unused-arrow.png",
            )
            assinatura_atual = renderer._assinatura_arquivo_para_resume(media)
            clip = root / "cena_03_render.mp4"
            manifest = root / "cena_03.json"
            clip.write_bytes(b"cached-clip")
            manifest.write_text(
                json.dumps(
                    {
                        "versao": renderer.SCENE_RESUME_MANIFEST_VERSION,
                        "hash_entrada": hash_anterior,
                        "duracao_esperada": 3.0,
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(renderer, "_validar_clip_para_resume") as validar_clip:
                reaproveitada = renderer._reaproveitar_cena_integra(
                    clip_path=clip,
                    manifest_path=manifest,
                    hash_entrada=hash_atual,
                    ffprobe="ffprobe",
                )

        self.assertEqual(assinatura_anterior["tamanho"], assinatura_atual["tamanho"])
        self.assertNotIn("mtime_ns", assinatura_atual)
        self.assertNotEqual(assinatura_anterior["hash_hibrido"], assinatura_atual["hash_hibrido"])
        self.assertNotEqual(hash_anterior, hash_atual)
        self.assertIsNone(reaproveitada)
        validar_clip.assert_not_called()

    def test_atomic_lock_collision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "guerra_5min.lock"
            with theme_lock.TravaTema.adquirir(lock_path):
                with patch.object(theme_lock, "pid_esta_ativo", return_value=True):
                    with self.assertRaisesRegex(theme_lock.ThemeLockCollisionError, "PID ativo"):
                        theme_lock.TravaTema.adquirir(lock_path)

            self.assertFalse(lock_path.exists())

    def test_orphan_lock_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "guerra_5min.lock"
            lock_path.write_text(
                json.dumps({"pid": 999_999_999, "timestamp": 1.0, "token": "orphan"}),
                encoding="utf-8",
            )

            with patch.object(theme_lock, "pid_esta_ativo", return_value=False):
                lock = theme_lock.TravaTema.adquirir(lock_path)
                payload = json.loads(lock_path.read_text(encoding="utf-8"))
                lock.liberar()
                self.assertFalse(lock_path.exists())

        self.assertEqual(payload["pid"], os.getpid())
        self.assertNotEqual(payload["token"], "orphan")

    def test_valid_manifest_reuses_scene_without_rendering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clip = root / "cena_03_render.mp4"
            manifest = root / "cena_03.json"
            clip.write_bytes(b"clip")
            manifest.write_text(
                json.dumps(
                    {
                        "versao": renderer.SCENE_RESUME_MANIFEST_VERSION,
                        "hash_entrada": "hash-correto",
                        "duracao_esperada": 3.0,
                    }
                ),
                encoding="utf-8",
            )
            logger = Mock()
            with (
                patch.object(renderer, "_validar_clip_para_resume", return_value=3.0),
                patch.object(renderer, "get_logger", return_value=logger),
            ):
                duracao = renderer._reaproveitar_cena_integra(
                    clip_path=clip,
                    manifest_path=manifest,
                    hash_entrada="hash-correto",
                    ffprobe="ffprobe",
                )

        self.assertEqual(duracao, 3.0)
        logger.info.assert_called_once_with(
            "[RESUME] Cena %02d íntegra detectada. Pulando renderização.",
            3,
        )

    def test_invalid_manifest_forces_scene_artifact_removal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clip = root / "cena_03_render.mp4"
            manifest = root / "cena_03.json"
            clip.write_bytes(b"old-clip")
            manifest.write_text("{invalido", encoding="utf-8")

            resultado = renderer._reaproveitar_cena_integra(
                clip_path=clip,
                manifest_path=manifest,
                hash_entrada="hash-atual",
                ffprobe="ffprobe",
            )
            renderer._invalidar_artefatos_cena(clip, manifest)
            self.assertFalse(clip.exists())
            self.assertFalse(manifest.exists())

        self.assertIsNone(resultado)

    def test_resume_directory_is_stable_for_the_same_theme(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "SynthReel" / "horizontal"
            with patch.object(renderer, "TEMP_HORIZONTAL_DIR", root):
                primeiro = renderer._criar_diretorio_execucao("historia", "roma")
                segundo = renderer._criar_diretorio_execucao("historia", "roma")

        self.assertEqual(primeiro, segundo)
        self.assertTrue(primeiro.name.startswith("historia_roma"))

    def test_resume_clip_validation_requires_cfr_and_expected_geometry(self) -> None:
        payload = {
            "streams": [
                {
                    "codec_type": "video",
                    "width": 1920,
                    "height": 1080,
                    "avg_frame_rate": "30/1",
                    "r_frame_rate": "30/1",
                    "duration": "3.0",
                }
            ],
            "format": {"duration": "3.0"},
        }
        with (
            patch.object(renderer, "_validar_arquivo_nao_vazio"),
            patch.object(renderer, "_ffprobe_json", return_value=payload),
        ):
            duracao = renderer._validar_clip_para_resume(Path("cena_03_render.mp4"), "ffprobe")

        self.assertEqual(duracao, 3.0)


class ConcurrentSceneRenderTests(unittest.TestCase):
    def _trabalho(self, indice: int) -> renderer.TrabalhoRenderizacaoCena:
        cena = renderer.CenaAuditada(
            indice=indice,
            template_id=1,
            texto=f"Texto da cena {indice}.",
            textos_tela=(),
            midias=(),
        )
        temporizada = renderer.CenaTemporizada(cena, 0.0, 3.0, 3.0, 1.0)
        return renderer.TrabalhoRenderizacaoCena(
            cena_temporizada=temporizada,
            metadata_cena={"fonte_midia": "local"},
            clip_path=Path(f"cena_{indice:02d}_render.mp4"),
            manifest_path=Path(f"cena_{indice:02d}.json"),
            ffmpeg="ffmpeg",
            ffprobe="ffprobe",
            opcoes_encoder_video=renderer._opcoes_h264_amf(),
            fonte=None,
            seta_path=Path("seta.png"),
        )

    def test_worker_reuses_valid_resume_cache_without_rendering(self) -> None:
        trabalho = self._trabalho(3)
        logger = Mock()
        with (
            patch.object(renderer, "get_logger", return_value=logger),
            patch.object(renderer, "_hash_entrada_cena", return_value="hash-correto"),
            patch.object(renderer, "_reaproveitar_cena_integra", return_value=3.0) as reaproveitar,
            patch.object(renderer, "_renderizar_cena") as renderizar,
        ):
            resultado = renderer._processar_trabalho_cena(trabalho)

        self.assertTrue(resultado.reaproveitada)
        self.assertEqual(resultado.indice_cena, 3)
        self.assertEqual(resultado.duracao, 3.0)
        reaproveitar.assert_called_once_with(
            clip_path=trabalho.clip_path,
            manifest_path=trabalho.manifest_path,
            hash_entrada="hash-correto",
            ffprobe="ffprobe",
            logger=logger,
            prefixo_log="[CENA 03]",
        )
        renderizar.assert_not_called()

    def test_worker_failure_short_circuits_pending_scene_queue(self) -> None:
        class ExecutorFalso:
            def __init__(self, futures: list[Future[renderer.ResultadoRenderizacaoCena]]) -> None:
                self._futures = iter(futures)
                self.submissoes = 0
                self.shutdown_calls: list[dict[str, bool]] = []

            def submit(self, *_args: object, **_kwargs: object) -> Future[renderer.ResultadoRenderizacaoCena]:
                self.submissoes += 1
                return next(self._futures)

            def shutdown(self, **kwargs: bool) -> None:
                self.shutdown_calls.append(kwargs)

        falha: Future[renderer.ResultadoRenderizacaoCena] = Future()
        falha.set_exception(RuntimeError("ffmpeg falhou"))
        pendente: Future[renderer.ResultadoRenderizacaoCena] = Future()
        executor = ExecutorFalso([falha, pendente])

        with (
            patch.object(renderer, "ProcessPoolExecutor", return_value=executor) as pool,
            patch.object(renderer, "get_logger", return_value=Mock()) as get_logger,
        ):
            with self.assertRaisesRegex(RuntimeError, "ffmpeg falhou"):
                renderer._renderizar_trabalhos_cenas_concorrentes(
                    (self._trabalho(1), self._trabalho(2)),
                )

        pool.assert_called_once_with(max_workers=2)
        self.assertEqual(executor.submissoes, 2)
        self.assertTrue(pendente.cancelled())
        self.assertEqual(
            executor.shutdown_calls,
            [{"wait": False, "cancel_futures": True}],
        )
        get_logger.return_value.critical.assert_called_once()

    def test_completed_results_are_restored_to_scene_order(self) -> None:
        class ExecutorFalso:
            def __init__(self, futures: list[Future[renderer.ResultadoRenderizacaoCena]]) -> None:
                self._futures = iter(futures)
                self.shutdown_calls: list[dict[str, bool]] = []

            def submit(self, *_args: object, **_kwargs: object) -> Future[renderer.ResultadoRenderizacaoCena]:
                return next(self._futures)

            def shutdown(self, **kwargs: bool) -> None:
                self.shutdown_calls.append(kwargs)

        primeiro: Future[renderer.ResultadoRenderizacaoCena] = Future()
        primeiro.set_result(
            renderer.ResultadoRenderizacaoCena(2, Path("cena_02_render.mp4"), 3.0, False)
        )
        segundo: Future[renderer.ResultadoRenderizacaoCena] = Future()
        segundo.set_result(
            renderer.ResultadoRenderizacaoCena(1, Path("cena_01_render.mp4"), 3.0, False)
        )
        executor = ExecutorFalso([primeiro, segundo])

        with (
            patch.object(renderer, "ProcessPoolExecutor", return_value=executor),
            patch.object(renderer, "as_completed", return_value=(primeiro, segundo)),
        ):
            resultados = renderer._renderizar_trabalhos_cenas_concorrentes(
                (self._trabalho(2), self._trabalho(1)),
            )

        self.assertEqual([resultado.indice_cena for resultado in resultados], [1, 2])
        self.assertEqual(
            executor.shutdown_calls,
            [{"wait": True, "cancel_futures": False}],
        )


class CleanupTests(unittest.TestCase):
    def test_completed_theme_is_removed_without_touching_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lotes = root / "lotes_horizontais"
            tema = lotes / "historia" / "roma"
            tema.mkdir(parents=True)
            (tema / "metadata.json").write_text("{}", encoding="utf-8")
            output = root / "output" / "historia_roma.mp4"
            output.parent.mkdir()
            output.write_bytes(b"video-final")

            with patch.object(renderer, "LOTES_HORIZONTAIS_DIR", lotes):
                renderer._limpar_lote_horizontal_concluido(tema, output)

            self.assertFalse(tema.exists())
            self.assertEqual(output.read_bytes(), b"video-final")

    def test_output_inside_theme_blocks_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lotes = root / "lotes_horizontais"
            tema = lotes / "historia" / "roma"
            tema.mkdir(parents=True)
            output = tema / "resultado.mp4"
            output.write_bytes(b"video-final")

            with patch.object(renderer, "LOTES_HORIZONTAIS_DIR", lotes):
                with self.assertRaisesRegex(RuntimeError, "resultado final esta dentro"):
                    renderer._limpar_lote_horizontal_concluido(tema, output)

            self.assertTrue(tema.is_dir())
            self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
