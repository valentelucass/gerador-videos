"""Input-contract tests for the horizontal preparation phase."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from backend.src.scripts import preparar_horizontal as preparar


def _cena_template_11(textos_tela: object) -> dict[str, object]:
    return {
        "texto": "A lista organiza quatro pontos importantes para a narrativa.",
        "template_id": 11,
        "fonte_midia": "pexels",
        "prompt_ou_busca": "roman forum",
        "textos_tela": textos_tela,
    }


class Template11PreparationTests(unittest.TestCase):
    def test_requires_exactly_four_explicit_topics(self) -> None:
        metadata = {"cenas": [_cena_template_11(["Um", "Dois", "Tres"])]}

        with self.assertRaisesRegex(ValueError, "exatamente 4 topicos explicitos"):
            preparar._cenas_da_metadata(metadata)

    def test_requires_a_list_not_a_multiline_text_block(self) -> None:
        metadata = {"cenas": [_cena_template_11("Um\nDois\nTres\nQuatro")]}

        with self.assertRaisesRegex(ValueError, "como lista"):
            preparar._cenas_da_metadata(metadata)

    def test_accepts_four_single_line_topics(self) -> None:
        metadata = {
            "cenas": [
                _cena_template_11(["Um", "Dois", "Tres", "Quatro"]),
            ]
        }

        cenas = preparar._cenas_da_metadata(metadata)

        self.assertEqual(cenas[0]["textos_tela"], ["Um", "Dois", "Tres", "Quatro"])

    def test_template_12_accepts_the_same_four_topic_infrastructure_contract(self) -> None:
        metadata = {
            "cenas": [
                {
                    **_cena_template_11(["Um", "Dois", "Tres", "Quatro"]),
                    "template_id": 12,
                }
            ]
        }

        cenas = preparar._cenas_da_metadata(metadata)

        self.assertEqual(cenas[0]["template_id"], 12)
        self.assertEqual(preparar.TEMPLATE_MEDIA_COUNTS[12], 1)


class Template12SubsceneNormalizationTests(unittest.TestCase):
    def test_template_12_rejects_parent_media_reused_by_a_topic(self) -> None:
        with self.assertRaisesRegex(ValueError, "mídia exclusiva do tópico"):
            preparar._normalizar_contrato_subcenas(
                [
                    {
                        "template_id": 12,
                        "fonte_midia": "pexels",
                        "prompt_ou_busca": "one shared historic image",
                        "sub_cenas": [
                            {
                                "texto": "Cada tópico precisa da própria mídia visual.",
                                "topico": "Primeiro tópico",
                            }
                        ],
                    }
                ]
            )

    def test_flattening_sub_scenes_indices(self) -> None:
        metadata = {
            "cenas": [
                {
                    "texto": "Uma cena comum abre a sequencia.",
                    "template_id": 1,
                    "fonte_midia": "local",
                    "busca_local": "abertura",
                },
                {
                    "template_id": 12,
                    "fonte_midia": "local",
                    "sub_cenas": [
                        {
                            "texto": "A primeira unidade narrativa apresenta o contexto.",
                            "busca_local": "contexto",
                            "topico": "Contexto",
                        },
                        {
                            "texto": "A segunda unidade narrativa mostra a consequencia.",
                            "busca_local": "consequencia",
                            "topico": "Consequencia",
                        },
                    ],
                },
                {
                    "texto": "Uma cena comum conclui a sequencia.",
                    "template_id": 1,
                    "fonte_midia": "local",
                    "busca_local": "conclusao",
                },
            ]
        }

        cenas = preparar._cenas_da_metadata(metadata)

        self.assertEqual([cena["indice"] for cena in cenas], [1, 2, 3, 4])
        self.assertEqual([cena["template_id"] for cena in cenas], [1, 12, 12, 1])
        self.assertEqual(
            [cena["texto"] for cena in cenas],
            [
                "Uma cena comum abre a sequencia.",
                "A primeira unidade narrativa apresenta o contexto.",
                "A segunda unidade narrativa mostra a consequencia.",
                "Uma cena comum conclui a sequencia.",
            ],
        )
        self.assertTrue(all("sub_cenas" not in cena for cena in cenas))
        grupos_t12 = [cena["t12_group_id"] for cena in cenas if cena["template_id"] == 12]
        self.assertEqual(len(set(grupos_t12)), 1)
        self.assertTrue(str(grupos_t12[0]).startswith("t12_"))

    def test_template_12_topic_accumulation(self) -> None:
        cenas = preparar._normalizar_contrato_subcenas(
            [
                {
                    "template_id": 12,
                    "fonte_midia": "local",
                    "sub_cenas": [
                        {
                            "texto": "A primeira sub-cena introduz o primeiro ponto.",
                            "busca_local": "tag_um",
                            "topico": "Topico Um",
                        },
                        {
                            "texto": "A segunda sub-cena acrescenta o segundo ponto.",
                            "busca_local": "tag_dois",
                            "topico": "Acao Dois",
                        },
                        {
                            "texto": "A terceira sub-cena completa o terceiro ponto.",
                            "busca_local": "tag_tres",
                            "topico": "Efeito Tres",
                        },
                    ],
                }
            ]
        )

        self.assertEqual(
            [cena["textos_tela"] for cena in cenas],
            [
                ["Topico Um", "", "", ""],
                ["Topico Um", "Acao Dois", "", ""],
                ["Topico Um", "Acao Dois", "Efeito Tres", ""],
            ],
        )
        self.assertEqual([cena["busca_local"] for cena in cenas], ["tag_um", "tag_dois", "tag_tres"])


class Template12VisualDiversityTests(unittest.TestCase):
    def test_lexical_similarity_ignores_word_order_and_stopwords(self) -> None:
        similaridade = preparar._calcular_similaridade_lexica(
            "soldiers marching on road",
            "marching soldiers on a road",
        )
        diferente = preparar._calcular_similaridade_lexica(
            "soldiers marching on road",
            "field medic treating wounded soldier",
        )

        self.assertGreater(similaridade, 0.60)
        self.assertLessEqual(diferente, 0.60)

    def test_repeated_local_t12_search_uses_distinct_matching_asset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            origem = raiz / "entradas"
            destino = raiz / "preparado"
            origem.mkdir()
            (origem / "battle_01.jpg").write_bytes(b"primeira")
            (origem / "battle_02.jpg").write_bytes(b"segunda")
            logger = Mock()
            cenas = [
                {
                    "template_id": 12,
                    "fonte_midia": "local",
                    "busca_local": "battle",
                    "t12_group_id": "t12_grupo",
                },
                {
                    "template_id": 12,
                    "fonte_midia": "local",
                    "busca_local": "battle",
                    "t12_group_id": "t12_grupo",
                },
            ]

            preparar._preparar_midias_do_tema(
                cenas=cenas,
                destino=destino,
                diretorio_origem=origem,
                fetcher=Mock(),
                logger=logger,
            )

            primeiro = destino / "cena_01_local.jpg"
            segundo = destino / "cena_02_local.jpg"
            self.assertNotEqual(primeiro.read_bytes(), segundo.read_bytes())
            logger.warning.assert_called_once()

    def test_repeated_local_t12_search_without_unused_asset_aborts_theme(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            origem = raiz / "entradas"
            origem.mkdir()
            (origem / "battle_01.jpg").write_bytes(b"unica")
            cenas = [
                {
                    "template_id": 12,
                    "fonte_midia": "local",
                    "busca_local": "battle",
                    "t12_group_id": "t12_grupo",
                },
                {
                    "template_id": 12,
                    "fonte_midia": "local",
                    "busca_local": "battle",
                    "t12_group_id": "t12_grupo",
                },
            ]

            with self.assertRaisesRegex(FileNotFoundError, "asset local alternativo"):
                preparar._preparar_midias_do_tema(
                    cenas=cenas,
                    destino=raiz / "preparado",
                    diretorio_origem=origem,
                    fetcher=Mock(),
                    logger=Mock(),
                )

    def test_redundant_pexels_prompt_uses_diversified_query_and_group_ids(self) -> None:
        class FetcherFalso:
            def __init__(self) -> None:
                self.chamadas: list[tuple[str, set[str]]] = []

            def obter_midia_para_cena(
                self,
                *,
                query: str,
                midias_usadas: set[str],
                storage_path: Path,
                orientacao: str,
            ) -> dict[str, str]:
                self.chamadas.append((query, set(midias_usadas)))
                indice = len(self.chamadas)
                arquivo = storage_path / f"download_{indice}.mp4"
                arquivo.parent.mkdir(parents=True, exist_ok=True)
                arquivo.write_bytes(f"video-{indice}".encode())
                return {
                    "id": f"pexels-{indice}",
                    "path_local": str(arquivo),
                    "tipo": "video",
                }

        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            fetcher = FetcherFalso()
            logger = Mock()
            cenas = [
                {
                    "template_id": 12,
                    "fonte_midia": "pexels",
                    "prompt_ou_busca": "soldiers marching on road",
                    "topico": "Advance",
                    "t12_group_id": "t12_grupo",
                },
                {
                    "template_id": 12,
                    "fonte_midia": "pexels",
                    "prompt_ou_busca": "marching soldiers on a road",
                    "topico": "Trench cover",
                    "t12_group_id": "t12_grupo",
                },
            ]

            preparar._preparar_midias_do_tema(
                cenas=cenas,
                destino=raiz / "preparado",
                diretorio_origem=raiz,
                fetcher=fetcher,  # type: ignore[arg-type]
                logger=logger,
            )

        self.assertIn("cinematic b-roll", fetcher.chamadas[1][0])
        self.assertIn("Trench cover", fetcher.chamadas[1][0])
        self.assertIn("pexels-1", fetcher.chamadas[1][1])
        logger.warning.assert_called_once()


class LocalMediaPreparationTests(unittest.TestCase):
    def test_local_scene_requires_busca_local(self) -> None:
        metadata = {
            "cenas": [
                {
                    "texto": "Uma cena local sem chave de busca nao deve ser aceita.",
                    "template_id": 1,
                    "fonte_midia": "local",
                }
            ]
        }

        with self.assertRaisesRegex(ValueError, "busca_local"):
            preparar._cenas_da_metadata(metadata)

    def test_substring_search_normalizes_case_and_spaces_and_ignores_non_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            origem = Path(tmp)
            (origem / "Sharing Cigarette notes.txt").write_text("ignore", encoding="utf-8")
            esperada = origem / "Soldiers Sharing Cigarette In Dark 202607121944.jpeg"
            esperada.write_bytes(b"image")

            encontrada = preparar._buscar_midia_local_por_substring(
                origem,
                "SHARING cigarette",
            )

        self.assertEqual(encontrada, esperada)

    def test_substring_search_accepts_local_video(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            origem = Path(tmp)
            esperada = origem / "Roman_Battle_Sequence.mp4"
            esperada.write_bytes(b"video")

            encontrada = preparar._buscar_midia_local_por_substring(origem, "battle sequence")

        self.assertEqual(encontrada, esperada)

    def test_substring_search_explains_missing_keyword(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(FileNotFoundError, "naval_battle"):
                preparar._buscar_midia_local_por_substring(Path(tmp), "naval_battle")

    def test_local_scene_uses_busca_local_and_copies_canonical_media_without_pexels(self) -> None:
        class FetcherMustNotRun:
            def obter_midia_para_cena(self, **_kwargs: object) -> object:
                raise AssertionError("Pexels nao deve ser chamado para fonte_midia local.")

        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            entradas = raiz / "entradas" / "horizontal"
            entradas.mkdir(parents=True)
            origem = entradas / "Soldiers Sharing Cigarette In Dark 202607121944.jpg"
            origem.write_bytes(b"local-image")
            lote = entradas / "lote.json"
            lote.write_text(
                json.dumps(
                    {
                        "tema": "Soldados",
                        "cenas": [
                            {
                                "texto": "Os soldados compartilham um cigarro durante a noite.",
                                "template_id": 1,
                                "fonte_midia": "local",
                                "busca_local": "sharing_cigarette",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            resumo = preparar.preparar_horizontal(
                lote,
                "historia",
                fetcher=FetcherMustNotRun(),  # type: ignore[arg-type]
                output_root=raiz / "workspace" / "lotes_horizontais",
            )

            resultado = resumo["resultados"][0]
            destino = Path(str(resultado["diretorio"])) / "cena_01_local.jpg"
            self.assertEqual(resultado["status"], "ok")
            self.assertTrue(origem.is_file())
            self.assertEqual(origem.read_bytes(), b"local-image")
            self.assertEqual(destino.read_bytes(), b"local-image")

    def test_multiple_local_slots_read_busca_local_per_slot(self) -> None:
        cena = {
            "texto": "Duas midias locais formam uma comparacao visual.",
            "template_id": 3,
            "fonte_midia": ["local", "local"],
            "busca_local": ["soldiers", "cigarette"],
        }

        slots = preparar._slots_da_cena(cena)

        self.assertEqual([slot.letra for slot in slots], ["A", "B"])
        self.assertEqual(
            [slot.prompt_ou_busca for slot in slots],
            ["soldiers", "cigarette"],
        )

    def test_buscas_locais_accepts_fewer_tags_without_repeating_the_last_one(self) -> None:
        cena = {
            "texto": "Duas midias locais precisam ocupar slots visuais diferentes.",
            "template_id": 3,
            "fonte_midia": ["local", "local"],
            "buscas_locais": ["soldiers"],
        }

        slots = preparar._slots_da_cena(cena)

        self.assertEqual([slot.prompt_ou_busca for slot in slots], ["soldiers", None])

    def test_missing_local_tags_fill_multislot_scene_with_distinct_unused_assets(self) -> None:
        class FetcherMustNotRun:
            def obter_midia_para_cena(self, **_kwargs: object) -> object:
                raise AssertionError("Pexels nao deve ser chamado para fonte_midia local.")

        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            entradas = raiz / "entradas" / "horizontal"
            entradas.mkdir(parents=True)
            (entradas / "Soldiers at Dawn.jpg").write_bytes(b"soldiers")
            (entradas / "Cigarette by Campfire.jpg").write_bytes(b"campfire")
            lote = entradas / "lote.json"
            lote.write_text(
                json.dumps(
                    {
                        "tema": "Soldados",
                        "cenas": [
                            {
                                "texto": "Dois arquivos locais preenchem uma comparacao visual sem clones.",
                                "template_id": 3,
                                "fonte_midia": "local",
                                "buscas_locais": ["soldiers"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            resumo = preparar.preparar_horizontal(
                lote,
                "historia",
                fetcher=FetcherMustNotRun(),  # type: ignore[arg-type]
                output_root=raiz / "workspace" / "lotes_horizontais",
            )

            tema_dir = Path(str(resumo["resultados"][0]["diretorio"]))
            primeiro = tema_dir / "cena_01_A_local.jpg"
            segundo = tema_dir / "cena_01_B_local.jpg"

            self.assertEqual(resumo["resultados"][0]["status"], "ok")
            self.assertTrue(primeiro.is_file())
            self.assertTrue(segundo.is_file())
            self.assertNotEqual(primeiro.read_bytes(), segundo.read_bytes())


if __name__ == "__main__":
    unittest.main()
