"""FFmpeg filter_complex templates for horizontal 16:9 videos."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from pathlib import Path


class LayoutFactory:
    """Static factory for 1920x1080 layout filter graphs.

    The media paths dictionary is used only to define input order. FFmpeg
    filter_complex graphs reference already-declared inputs as [0:v], [1:v],
    and so on; this class never opens files and never runs subprocesses.

    Templates 3, 4, 7, 8, 9, 10, 11 and 12 optionally accept the persistent
    ``fundo_estatico`` input. When supplied, it replaces the plain-color base
    with a 1920x1080 scale/crop composition layer. This asset is intentionally
    not animated: it is a persistent layout background, not scene media.
    """

    CANVAS_W = 1920
    CANVAS_H = 1080
    BASE_COLOR = "white"
    TEXT_COLOR = "black"
    ACCENT_COLOR = "yellow"
    IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png"})
    KEN_BURNS_MAX_STEP = 0.001
    KEN_BURNS_MAX_ZOOM = 1.15
    FPS = 30

    _ALIASES_PRINCIPAL = ("principal", "media", "video", "imagem", "fundo", "bg")
    _ALIASES_ESQUERDA = ("esquerda", "left", "media_esquerda", "foto_esquerda", "retangulo_esq")
    _ALIASES_DIREITA = ("direita", "right", "media_direita", "foto_direita", "quadrado_dir")
    _ALIASES_CELULAR_1 = ("celular_1", "phone_1", "media_1", "m1", "primeira")
    _ALIASES_CELULAR_2 = ("celular_2", "phone_2", "media_2", "m2", "segunda")
    _ALIASES_CELULAR_3 = ("celular_3", "phone_3", "media_3", "m3", "terceira")
    _ALIASES_SETA = ("seta", "arrow", "seta_apontamento")
    _ALIASES_FUNDO_ESTATICO = ("fundo_estatico",)

    @staticmethod
    def build_filter_complex(
        template_id: int,
        caminhos_midias: Mapping[str, str],
        textos_tela: Sequence[str] | None = None,
        *,
        indices_imagens: frozenset[int] | None = None,
        total_frames: int | None = None,
    ) -> str:
        """Returns the exact FFmpeg -filter_complex string for a layout."""

        if not isinstance(caminhos_midias, Mapping):
            raise TypeError("caminhos_midias deve ser um dicionario.")

        if isinstance(textos_tela, str):
            textos = [textos_tela]
        else:
            textos = [str(texto) for texto in (textos_tela or [])]
        if indices_imagens is None:
            imagens = LayoutFactory._inferir_indices_imagens(caminhos_midias)
        else:
            imagens = frozenset(int(index) for index in indices_imagens)
            invalidos = sorted(
                index for index in imagens if index < 0 or index >= len(caminhos_midias)
            )
            if invalidos:
                raise ValueError(f"indices_imagens fora dos inputs declarados: {invalidos}")
        frames = max(1, int(total_frames or (LayoutFactory.FPS * 9)))
        templates = {
            1: LayoutFactory._template_1_fullscreen,
            2: LayoutFactory._template_2_fundo_borrado,
            3: LayoutFactory._template_3_apontamento,
            4: LayoutFactory._template_4_texto_puro,
            5: LayoutFactory._template_5_celular_triplicado,
            6: LayoutFactory._template_6_descricao_na_imagem,
            7: LayoutFactory._template_7_assimetrico_superior,
            8: LayoutFactory._template_8_celular_lateral,
            9: LayoutFactory._template_9_misto_rodape,
            10: LayoutFactory._template_10_misto_limpo,
            11: LayoutFactory._template_11_lista_topicos,
            12: LayoutFactory._template_12_topicos_escalonados,
        }

        try:
            template = templates[int(template_id)]
        except (KeyError, ValueError) as exc:
            raise ValueError("template_id deve ser um inteiro entre 1 e 12.") from exc

        return template(caminhos_midias, textos, imagens, frames)

    @staticmethod
    def criar(
        template_id: int,
        caminhos_midias: Mapping[str, str],
        textos_tela: Sequence[str] | None = None,
        *,
        indices_imagens: frozenset[int] | None = None,
        total_frames: int | None = None,
    ) -> str:
        """Portuguese alias for build_filter_complex."""

        return LayoutFactory.build_filter_complex(
            template_id,
            caminhos_midias,
            textos_tela,
            indices_imagens=indices_imagens,
            total_frames=total_frames,
        )

    @staticmethod
    def get_filter_complex(
        template_id: int,
        caminhos_midias: Mapping[str, str],
        textos_tela: Sequence[str] | None = None,
        *,
        indices_imagens: frozenset[int] | None = None,
        total_frames: int | None = None,
    ) -> str:
        """English alias for build_filter_complex."""

        return LayoutFactory.build_filter_complex(
            template_id,
            caminhos_midias,
            textos_tela,
            indices_imagens=indices_imagens,
            total_frames=total_frames,
        )

    @staticmethod
    def gerar(
        template_id: int,
        caminhos_midias: Mapping[str, str],
        textos_tela: Sequence[str] | None = None,
        *,
        indices_imagens: frozenset[int] | None = None,
        total_frames: int | None = None,
    ) -> str:
        """Short alias for build_filter_complex."""

        return LayoutFactory.build_filter_complex(
            template_id,
            caminhos_midias,
            textos_tela,
            indices_imagens=indices_imagens,
            total_frames=total_frames,
        )

    @staticmethod
    def _template_1_fullscreen(
        caminhos_midias: Mapping[str, str],
        textos: Sequence[str],
        indices_imagens: frozenset[int],
        total_frames: int,
    ) -> str:
        idx = LayoutFactory._input_index(caminhos_midias, LayoutFactory._ALIASES_PRINCIPAL, 0)
        filtros = [
            LayoutFactory._base("base"),
            *LayoutFactory._filtros_midia(
                idx,
                "m0",
                1920,
                1080,
                indices_imagens,
                total_frames,
            ),
            LayoutFactory._overlay("base", "m0", "v0", 0, 0),
            LayoutFactory._format("v0", "vout"),
        ]
        return ";".join(filtros)

    @staticmethod
    def _template_2_fundo_borrado(
        caminhos_midias: Mapping[str, str],
        textos: Sequence[str],
        indices_imagens: frozenset[int],
        total_frames: int,
    ) -> str:
        idx = LayoutFactory._input_index(caminhos_midias, LayoutFactory._ALIASES_PRINCIPAL, 0)
        source_label = "template2_cfr"
        filtros: list[str] = []
        if idx in indices_imagens:
            filtros.extend(
                LayoutFactory._filtros_midia(
                    idx,
                    "template2_kb",
                    1920,
                    1080,
                    indices_imagens,
                    total_frames,
                )
            )
            source_label = "template2_kb"
        else:
            filtros.append(LayoutFactory._fps_input(idx, source_label))
        filtros.extend(
            [
            f"[{source_label}]split=2[bg_src][fg_src]",
            LayoutFactory._scale_crop("bg_src", "bg_scaled", 1920, 1080),
            "[bg_scaled]boxblur=20:1[bg]",
            LayoutFactory._scale_crop("fg_src", "fg", 1120, 630),
            LayoutFactory._overlay("bg", "fg", "v0", 400, 225),
            LayoutFactory._format("v0", "vout"),
            ]
        )
        return ";".join(filtros)

    @staticmethod
    def _template_3_apontamento(
        caminhos_midias: Mapping[str, str],
        textos: Sequence[str],
        indices_imagens: frozenset[int],
        total_frames: int,
    ) -> str:
        idx_esq = LayoutFactory._input_index(caminhos_midias, LayoutFactory._ALIASES_ESQUERDA, 0)
        idx_dir = LayoutFactory._input_index(caminhos_midias, LayoutFactory._ALIASES_DIREITA, 1)
        filtros = [LayoutFactory._base_com_fundo_estatico(caminhos_midias, "base")]
        filtros.extend(
            LayoutFactory._filtros_midia(
                idx_esq,
                "left",
                580,
                580,
                indices_imagens,
                total_frames,
            )
        )
        filtros.extend(
            LayoutFactory._filtros_midia(
                idx_dir,
                "right",
                415,
                415,
                indices_imagens,
                total_frames,
            )
        )
        indices_por_chave = {
            str(key).casefold(): index for index, key in enumerate(caminhos_midias)
        }
        idx_seta = next(
            (
                indices_por_chave[alias.casefold()]
                for alias in LayoutFactory._ALIASES_SETA
                if alias.casefold() in indices_por_chave
            ),
            None,
        )
        if idx_seta is None:
            # Keep support for older direct callers that supplied an unnamed
            # third scene input as the arrow, while never mistaking the new
            # persistent ``fundo_estatico`` input for that overlay.
            indices_nao_persistentes = [
                index
                for index, papel in enumerate(caminhos_midias)
                if str(papel).casefold()
                not in {alias.casefold() for alias in LayoutFactory._ALIASES_FUNDO_ESTATICO}
            ]
            if len(indices_nao_persistentes) >= 3:
                idx_seta = indices_nao_persistentes[2]
        if idx_seta is not None:
            filtros.extend(
                LayoutFactory._filtros_midia(
                    idx_seta,
                    "arrow",
                    300,
                    300,
                    indices_imagens,
                    total_frames,
                    animar=False,
                )
            )
        filtros.extend(
            [
                LayoutFactory._overlay("base", "left", "v0", 245, 255),
                LayoutFactory._overlay("v0", "right", "v1", 1235, 420),
            ]
        )
        if idx_seta is not None:
            filtros.append(LayoutFactory._overlay("v1", "arrow", "v2", 835, 175))
        else:
            # Compatibility for direct factory callers. The horizontal
            # renderer always supplies the persistent PNG arrow.
            filtros.append(
                LayoutFactory._drawtext(
                    "v1",
                    "v2",
                    "↘",
                    "875",
                    "195",
                    170,
                    LayoutFactory.TEXT_COLOR,
                )
            )
        filtros.append(LayoutFactory._format("v2", "vout"))
        return ";".join(filtros)

    @staticmethod
    def _template_4_texto_puro(
        caminhos_midias: Mapping[str, str],
        textos: Sequence[str],
        indices_imagens: frozenset[int],
        total_frames: int,
    ) -> str:
        linhas = LayoutFactory._linhas_texto(textos)
        y_inicial = max(80, 540 - (len(linhas) * 40))
        filtros = [LayoutFactory._base_com_fundo_estatico(caminhos_midias, "base")]
        filtros.extend(
            LayoutFactory._drawtext_linhas(
                "base",
                "center_txt",
                linhas,
                x="(w-text_w)/2",
                y_inicial=y_inicial,
                passo_y=80,
                fontsize=68,
            )
        )
        filtros.append(
            LayoutFactory._format(
                LayoutFactory._ultimo_label_texto("center_txt", linhas, "base"),
                "vout",
            )
        )
        return ";".join(filtros)

    @staticmethod
    def _template_5_celular_triplicado(
        caminhos_midias: Mapping[str, str],
        textos: Sequence[str],
        indices_imagens: frozenset[int],
        total_frames: int,
    ) -> str:
        idx_1 = LayoutFactory._input_index(caminhos_midias, LayoutFactory._ALIASES_CELULAR_1, 0)
        idx_2 = LayoutFactory._input_index(caminhos_midias, LayoutFactory._ALIASES_CELULAR_2, 1)
        idx_3 = LayoutFactory._input_index(caminhos_midias, LayoutFactory._ALIASES_CELULAR_3, 2)
        filtros = [
            LayoutFactory._base("base"),
            *LayoutFactory._filtros_midia(
                idx_1,
                "m0",
                640,
                1080,
                indices_imagens,
                total_frames,
            ),
            *LayoutFactory._filtros_midia(
                idx_2,
                "m1",
                640,
                1080,
                indices_imagens,
                total_frames,
            ),
            *LayoutFactory._filtros_midia(
                idx_3,
                "m2",
                640,
                1080,
                indices_imagens,
                total_frames,
            ),
            LayoutFactory._overlay("base", "m0", "v0", 0, 0),
            LayoutFactory._overlay("v0", "m1", "v1", 640, 0),
            LayoutFactory._overlay("v1", "m2", "v2", 1280, 0),
            LayoutFactory._format("v2", "vout"),
        ]
        return ";".join(filtros)

    @staticmethod
    def _template_6_descricao_na_imagem(
        caminhos_midias: Mapping[str, str],
        textos: Sequence[str],
        indices_imagens: frozenset[int],
        total_frames: int,
    ) -> str:
        idx = LayoutFactory._input_index(caminhos_midias, LayoutFactory._ALIASES_PRINCIPAL, 0)
        linhas = LayoutFactory._linhas_texto(textos)
        filtros = [
            LayoutFactory._base("base"),
            *LayoutFactory._filtros_midia(
                idx,
                "m0",
                1920,
                1080,
                indices_imagens,
                total_frames,
            ),
            LayoutFactory._overlay("base", "m0", "v0", 0, 0),
        ]
        filtros.extend(
            LayoutFactory._drawtext_linhas(
                "v0",
                "caption",
                linhas,
                x="150",
                y_inicial=490,
                passo_y=80,
                fontsize=64,
                fontcolor="white",
                boxcolor="black@0.62",
                boxborderw=12,
            )
        )
        filtros.append(
            LayoutFactory._format(
                LayoutFactory._ultimo_label_texto("caption", linhas, "v0"),
                "vout",
            )
        )
        return ";".join(filtros)

    @staticmethod
    def _template_7_assimetrico_superior(
        caminhos_midias: Mapping[str, str],
        textos: Sequence[str],
        indices_imagens: frozenset[int],
        total_frames: int,
    ) -> str:
        idx_esq = LayoutFactory._input_index(caminhos_midias, LayoutFactory._ALIASES_ESQUERDA, 0)
        idx_dir = LayoutFactory._input_index(caminhos_midias, LayoutFactory._ALIASES_DIREITA, 1)
        linhas = LayoutFactory._linhas_texto(textos)
        filtros = [
            LayoutFactory._base_com_fundo_estatico(caminhos_midias, "base"),
            *LayoutFactory._filtros_midia(
                idx_esq,
                "left",
                690,
                690,
                indices_imagens,
                total_frames,
            ),
            *LayoutFactory._filtros_midia(
                idx_dir,
                "right",
                525,
                525,
                indices_imagens,
                total_frames,
            ),
            LayoutFactory._overlay("base", "left", "v0", 270, 205),
            LayoutFactory._overlay("v0", "right", "v1", 1170, 205),
        ]
        filtros.extend(
            LayoutFactory._drawtext_linhas(
                "v1",
                "caption",
                linhas,
                x="1432-(text_w/2)",
                y_inicial=780,
                passo_y=58,
                fontsize=46,
            )
        )
        filtros.append(
            LayoutFactory._format(
                LayoutFactory._ultimo_label_texto("caption", linhas, "v1"),
                "vout",
            )
        )
        return ";".join(filtros)

    @staticmethod
    def _template_8_celular_lateral(
        caminhos_midias: Mapping[str, str],
        textos: Sequence[str],
        indices_imagens: frozenset[int],
        total_frames: int,
    ) -> str:
        idx = LayoutFactory._input_index(caminhos_midias, LayoutFactory._ALIASES_PRINCIPAL, 0)
        linhas = LayoutFactory._linhas_texto(textos)
        filtros = [
            LayoutFactory._base_com_fundo_estatico(caminhos_midias, "base"),
            *LayoutFactory._filtros_midia(
                idx,
                "phone",
                440,
                780,
                indices_imagens,
                total_frames,
            ),
            LayoutFactory._overlay("base", "phone", "v0", 185, 160),
        ]
        label_atual = "v0"
        if linhas:
            filtros.append(
                LayoutFactory._drawtext(
                    label_atual,
                    "right_title",
                    linhas[0],
                    "840",
                    "335",
                    68,
                    LayoutFactory.TEXT_COLOR,
                )
            )
            label_atual = "right_title"
        for index, linha in enumerate(linhas[1:]):
            proximo = f"right_body{index}"
            filtros.append(
                LayoutFactory._drawtext(
                    label_atual,
                    proximo,
                    linha,
                    "840",
                    str(500 + index * 74),
                    58,
                    LayoutFactory.TEXT_COLOR,
                )
            )
            label_atual = proximo
        filtros.append(LayoutFactory._format(label_atual, "vout"))
        return ";".join(filtros)

    @staticmethod
    def _template_9_misto_rodape(
        caminhos_midias: Mapping[str, str],
        textos: Sequence[str],
        indices_imagens: frozenset[int],
        total_frames: int,
    ) -> str:
        idx_esq = LayoutFactory._input_index(caminhos_midias, LayoutFactory._ALIASES_ESQUERDA, 0)
        idx_dir = LayoutFactory._input_index(caminhos_midias, LayoutFactory._ALIASES_DIREITA, 1)
        linhas = LayoutFactory._linhas_texto(textos)
        filtros = [
            LayoutFactory._base_com_fundo_estatico(caminhos_midias, "base"),
            *LayoutFactory._filtros_midia(
                idx_esq,
                "left",
                930,
                520,
                indices_imagens,
                total_frames,
            ),
            *LayoutFactory._filtros_midia(
                idx_dir,
                "right",
                520,
                520,
                indices_imagens,
                total_frames,
            ),
            LayoutFactory._overlay("base", "left", "v0", 228, 190),
            LayoutFactory._overlay("v0", "right", "v1", 1212, 190),
        ]
        filtros.extend(
            LayoutFactory._drawtext_linhas(
                "v1",
                "footer",
                linhas,
                x="(w-text_w)/2",
                y_inicial=755,
                passo_y=78,
                fontsize=62,
            )
        )
        filtros.append(
            LayoutFactory._format(
                LayoutFactory._ultimo_label_texto("footer", linhas, "v1"),
                "vout",
            )
        )
        return ";".join(filtros)

    @staticmethod
    def _template_10_misto_limpo(
        caminhos_midias: Mapping[str, str],
        textos: Sequence[str],
        indices_imagens: frozenset[int],
        total_frames: int,
    ) -> str:
        idx_esq = LayoutFactory._input_index(caminhos_midias, LayoutFactory._ALIASES_ESQUERDA, 0)
        idx_dir = LayoutFactory._input_index(caminhos_midias, LayoutFactory._ALIASES_DIREITA, 1)
        filtros = [
            LayoutFactory._base_com_fundo_estatico(caminhos_midias, "base"),
            *LayoutFactory._filtros_midia(
                idx_esq,
                "left",
                930,
                520,
                indices_imagens,
                total_frames,
            ),
            *LayoutFactory._filtros_midia(
                idx_dir,
                "right",
                520,
                520,
                indices_imagens,
                total_frames,
            ),
            LayoutFactory._overlay("base", "left", "v0", 188, 295),
            LayoutFactory._overlay("v0", "right", "v1", 1172, 295),
            LayoutFactory._format("v1", "vout"),
        ]
        return ";".join(filtros)

    @staticmethod
    def _template_11_lista_topicos(
        caminhos_midias: Mapping[str, str],
        textos: Sequence[str],
        indices_imagens: frozenset[int],
        total_frames: int,
    ) -> str:
        idx = LayoutFactory._input_index(caminhos_midias, LayoutFactory._ALIASES_ESQUERDA, 0)
        linhas = LayoutFactory._linhas_texto(textos)
        topicos = [
            linha if linha.lstrip().startswith(("•", "-", "*")) else f"•  {linha}"
            for linha in linhas
        ]
        filtros = [
            LayoutFactory._base_com_fundo_estatico(caminhos_midias, "base"),
            *LayoutFactory._filtros_midia(
                idx,
                "square",
                730,
                730,
                indices_imagens,
                total_frames,
            ),
            LayoutFactory._overlay("base", "square", "v0", 270, 195),
        ]
        filtros.extend(
            LayoutFactory._drawtext_linhas(
                "v0",
                "topic",
                topicos,
                x="1160",
                y_inicial=260,
                passo_y=155,
                fontsize=64,
            )
        )
        filtros.append(
            LayoutFactory._format(
                LayoutFactory._ultimo_label_texto("topic", topicos, "v0"),
                "vout",
            )
        )
        return ";".join(filtros)

    @staticmethod
    def _template_12_topicos_escalonados(
        caminhos_midias: Mapping[str, str],
        textos: Sequence[str],
        indices_imagens: frozenset[int],
        total_frames: int,
    ) -> str:
        """Placeholder contratual para a futura lista com topicos temporizados.

        A infraestrutura HITL ja aceita o ID 12 e o mesmo contrato de uma
        midia/ quatro topicos do template 11. A animacao temporal deliberada
        sera introduzida posteriormente, sem alterar a geometria consolidada
        do T11 neste passo.
        """

        return LayoutFactory._template_11_lista_topicos(
            caminhos_midias,
            textos,
            indices_imagens,
            total_frames,
        )

    @staticmethod
    def _input_index(caminhos_midias: Mapping[str, str], aliases: Sequence[str], fallback_index: int) -> int:
        keys = list(caminhos_midias.keys())
        normalized = {str(key).lower(): index for index, key in enumerate(keys)}

        for alias in aliases:
            if alias.lower() in normalized:
                return normalized[alias.lower()]

        if fallback_index < len(keys):
            return fallback_index

        raise ValueError(
            "Midia insuficiente para o template: esperado input na posicao "
            f"{fallback_index} ou uma das chaves {', '.join(aliases)}."
        )

    @staticmethod
    def _base(label: str) -> str:
        return f"color=c={LayoutFactory.BASE_COLOR}:s={LayoutFactory.CANVAS_W}x{LayoutFactory.CANVAS_H}:r=30[{label}]"

    @staticmethod
    def _base_com_fundo_estatico(caminhos_midias: Mapping[str, str], label: str) -> str:
        """Builds a canvas base from a persistent static-background input.

        ``fundo_estatico`` is deliberately resolved by its semantic key rather
        than by input position: scene media retain their existing ordering and
        direct/legacy callers without this optional asset preserve the white
        base. The persistent background bypasses ``_filtros_midia`` so a JPG
        or PNG never receives Ken Burns.
        """

        aliases = {alias.casefold() for alias in LayoutFactory._ALIASES_FUNDO_ESTATICO}
        for input_index, papel in enumerate(caminhos_midias):
            if str(papel).casefold() in aliases:
                source_label = f"{label}_cfr"
                return ";".join(
                    (
                        LayoutFactory._fps_input(input_index, source_label),
                        LayoutFactory._scale_crop(
                            source_label,
                            label,
                            LayoutFactory.CANVAS_W,
                            LayoutFactory.CANVAS_H,
                        ),
                    )
                )
        return LayoutFactory._base(label)

    @staticmethod
    def _inferir_indices_imagens(caminhos_midias: Mapping[str, str]) -> frozenset[int]:
        indices: set[int] = set()
        aliases_persistentes = {
            *(alias.casefold() for alias in LayoutFactory._ALIASES_SETA),
            *(alias.casefold() for alias in LayoutFactory._ALIASES_FUNDO_ESTATICO),
        }
        for index, (papel, caminho) in enumerate(caminhos_midias.items()):
            path = Path(str(caminho))
            if str(papel).casefold() in aliases_persistentes:
                continue
            if path.name.casefold() == "seta_apontamento.png":
                continue
            if path.suffix.casefold() in LayoutFactory.IMAGE_EXTENSIONS:
                indices.add(index)
        return frozenset(indices)

    @staticmethod
    def _filtros_midia(
        input_index: int,
        output_label: str,
        width: int,
        height: int,
        indices_imagens: frozenset[int],
        total_frames: int,
        *,
        animar: bool = True,
    ) -> list[str]:
        input_label = f"{output_label}_cfr"
        filtros = [LayoutFactory._fps_input(input_index, input_label)]
        if not animar or input_index not in indices_imagens:
            filtros.append(
                LayoutFactory._scale_crop(input_label, output_label, width, height)
            )
            return filtros

        overscan_w = LayoutFactory._ceil_even(width * LayoutFactory.KEN_BURNS_MAX_ZOOM)
        overscan_h = LayoutFactory._ceil_even(height * LayoutFactory.KEN_BURNS_MAX_ZOOM)
        prep_label = f"{output_label}_kb_prep"
        zoom_label = f"{output_label}_kb_zoom"
        filtros.extend(
            [
                LayoutFactory._scale_crop(
                    input_label,
                    prep_label,
                    overscan_w,
                    overscan_h,
                ),
                LayoutFactory._zoompan(
                    prep_label,
                    zoom_label,
                    overscan_w,
                    overscan_h,
                    total_frames,
                ),
                LayoutFactory._scale_crop(
                    zoom_label,
                    output_label,
                    width,
                    height,
                ),
            ]
        )
        return filtros

    @staticmethod
    def _zoompan(
        input_label: str,
        output_label: str,
        width: int,
        height: int,
        total_frames: int,
    ) -> str:
        frames_movimento = max(1, int(total_frames) - 1)
        passo = min(
            LayoutFactory.KEN_BURNS_MAX_STEP,
            (LayoutFactory.KEN_BURNS_MAX_ZOOM - 1.0) / frames_movimento,
        )
        return (
            f"[{input_label}]zoompan="
            f"z='if(eq(on,0),1,min(max(zoom,pzoom)+{passo:.8f},"
            f"{LayoutFactory.KEN_BURNS_MAX_ZOOM:.2f}))':"
            "d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"s={width}x{height}:fps={LayoutFactory.FPS}[{output_label}]"
        )

    @staticmethod
    def _ceil_even(value: float) -> int:
        inteiro = int(math.ceil(value))
        return inteiro if inteiro % 2 == 0 else inteiro + 1

    @staticmethod
    def _scale_crop(input_label: str, output_label: str, width: int, height: int) -> str:
        return (
            f"[{input_label}]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}[{output_label}]"
        )

    @staticmethod
    def _fps_input(input_index: int, output_label: str) -> str:
        """Normaliza cada input fisico para CFR antes de qualquer composicao."""

        return f"[{input_index}:v]fps={LayoutFactory.FPS}[{output_label}]"

    @staticmethod
    def _overlay(base_label: str, media_label: str, output_label: str, x: int, y: int) -> str:
        return f"[{base_label}][{media_label}]overlay=x={x}:y={y}[{output_label}]"

    @staticmethod
    def _drawbox(input_label: str, output_label: str, x: int, y: int, width: int, height: int, color: str) -> str:
        return (
            f"[{input_label}]drawbox=x={x}:y={y}:w={width}:h={height}:"
            f"color={color}:t=fill[{output_label}]"
        )

    @staticmethod
    def _drawtext(
        input_label: str,
        output_label: str,
        texto: str,
        x: str,
        y: str,
        fontsize: int,
        fontcolor: str,
        *,
        line_spacing: int = 0,
        boxcolor: str | None = None,
        boxborderw: int = 0,
    ) -> str:
        texto_escape = LayoutFactory._escape_drawtext(texto)
        spacing = f":line_spacing={line_spacing}" if line_spacing else ""
        box = ""
        if boxcolor is not None:
            box = f":box=1:boxcolor={boxcolor}:boxborderw={max(0, boxborderw)}"
        return (
            f"[{input_label}]drawtext=text='{texto_escape}':fontcolor={fontcolor}:"
            f"fontsize={fontsize}:x={x}:y={y}{spacing}{box}[{output_label}]"
        )

    @staticmethod
    def _drawtext_linhas(
        input_label: str,
        prefixo_saida: str,
        textos: Sequence[str],
        *,
        x: str,
        y_inicial: int,
        passo_y: int,
        fontsize: int | None = None,
        fontcolor: str | None = None,
        boxcolor: str | None = None,
        boxborderw: int = 0,
    ) -> list[str]:
        filtros: list[str] = []
        label_atual = input_label
        for index, texto in enumerate(textos):
            proximo_label = f"{prefixo_saida}{index}"
            filtros.append(
                LayoutFactory._drawtext(
                    label_atual,
                    proximo_label,
                    texto,
                    x,
                    str(y_inicial + index * passo_y),
                    fontsize or LayoutFactory._fontsize_linha(texto),
                    fontcolor or LayoutFactory.TEXT_COLOR,
                    boxcolor=boxcolor,
                    boxborderw=boxborderw,
                )
            )
            label_atual = proximo_label
        return filtros

    @staticmethod
    def _ultimo_label_texto(prefixo_saida: str, textos: Sequence[str], fallback: str) -> str:
        if not textos:
            return fallback
        return f"{prefixo_saida}{len(textos) - 1}"

    @staticmethod
    def _format(input_label: str, output_label: str) -> str:
        return f"[{input_label}]format=yuv420p[{output_label}]"

    @staticmethod
    def _texto_bloco(textos: Sequence[str]) -> str:
        texto = "\n".join(str(item).strip() for item in textos if str(item).strip())
        return texto or " "

    @staticmethod
    def _linhas_texto(textos: Sequence[str]) -> list[str]:
        linhas: list[str] = []
        for item in textos:
            for linha in str(item).splitlines():
                limpa = linha.strip()
                if limpa:
                    linhas.append(limpa)
        return linhas or [" "]

    @staticmethod
    def _fontsize_bloco(texto: str, *, base: int = 72, minimo: int = 44) -> int:
        tamanho = len(texto.replace("\\n", " "))
        if tamanho <= 80:
            return base
        if tamanho <= 150:
            return max(minimo, base - 12)
        return max(minimo, base - 24)

    @staticmethod
    def _fontsize_linha(texto: str) -> int:
        tamanho = len(texto)
        if tamanho <= 32:
            return 54
        if tamanho <= 52:
            return 46
        return 38

    @staticmethod
    def _escape_drawtext(texto: str) -> str:
        return (
            str(texto)
            .replace("\\", "\\\\")
            .replace("\n", "\\n")
            # filter_complex is passed directly as one argv item. At this
            # parser layer FFmpeg needs the quote closed, an escaped literal
            # apostrophe, and the quote reopened (three backslashes).
            .replace("'", "'" + ("\\" * 3) + "''")
            .replace(":", "\\:")
            .replace("%", "\\%")
            .replace(",", "\\,")
            .replace("[", "\\[")
            .replace("]", "\\]")
        )
