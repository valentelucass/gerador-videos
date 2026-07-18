"""FFmpeg filter_complex templates for horizontal 16:9 videos."""

from __future__ import annotations

import math
import re
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
    # A câmera percorre somente 6% durante TODA a cena. O valor final é
    # atingido no ultimo frame: não há loop, retorno ao enquadramento inicial
    # nem um segundo zoom dentro do mesmo clipe.
    KEN_BURNS_MAX_ZOOM = 1.06
    # zoompan aceita x/y somente em pixels inteiros. A composição em 3x e a
    # redução Lanczos ao final convertem cada degrau interno em cerca de um
    # terço de pixel na saída, removendo o judder residual da câmera.
    KEN_BURNS_SUPERSAMPLE = 3
    FPS = 30
    TYPING_START_SECONDS = 0.10
    TYPING_LINE_STAGGER_SECONDS = 0.24
    TYPING_STEP_SECONDS = 0.038
    TYPING_MAX_STEPS = 28
    TYPING_LINE_PAUSE_SECONDS = 0.12

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
        cor_texto: str | None = None,
        borda_texto: bool | None = None,
        cor_borda_texto: str | None = None,
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

        graph = template(caminhos_midias, textos, imagens, frames)
        if cor_texto is not None:
            color = LayoutFactory._normalizar_cor_texto(cor_texto)
            # Every textual template declares either the legacy black or white
            # color.  Applying the preference here keeps one editor control
            # consistent across pure-text and photo/text layouts.
            graph = re.sub(r"fontcolor=(?:black|white)(?=:)", f"fontcolor={color}", graph)
            graph = graph.replace(
                "bordercolor=black@0.88",
                f"bordercolor={LayoutFactory._cor_contorno_texto(color)}@0.88",
            )
        if borda_texto is not None:
            graph = LayoutFactory._aplicar_contorno_texto(
                graph,
                habilitada=borda_texto,
                cor=LayoutFactory._normalizar_cor_texto(cor_borda_texto or "black"),
            )
        return graph

    @staticmethod
    def criar(
        template_id: int,
        caminhos_midias: Mapping[str, str],
        textos_tela: Sequence[str] | None = None,
        *,
        indices_imagens: frozenset[int] | None = None,
        total_frames: int | None = None,
        cor_texto: str | None = None,
    ) -> str:
        """Portuguese alias for build_filter_complex."""

        return LayoutFactory.build_filter_complex(
            template_id,
            caminhos_midias,
            textos_tela,
            indices_imagens=indices_imagens,
            total_frames=total_frames,
            cor_texto=cor_texto,
        )

    @staticmethod
    def get_filter_complex(
        template_id: int,
        caminhos_midias: Mapping[str, str],
        textos_tela: Sequence[str] | None = None,
        *,
        indices_imagens: frozenset[int] | None = None,
        total_frames: int | None = None,
        cor_texto: str | None = None,
    ) -> str:
        """English alias for build_filter_complex."""

        return LayoutFactory.build_filter_complex(
            template_id,
            caminhos_midias,
            textos_tela,
            indices_imagens=indices_imagens,
            total_frames=total_frames,
            cor_texto=cor_texto,
        )

    @staticmethod
    def gerar(
        template_id: int,
        caminhos_midias: Mapping[str, str],
        textos_tela: Sequence[str] | None = None,
        *,
        indices_imagens: frozenset[int] | None = None,
        total_frames: int | None = None,
        cor_texto: str | None = None,
    ) -> str:
        """Short alias for build_filter_complex."""

        return LayoutFactory.build_filter_complex(
            template_id,
            caminhos_midias,
            textos_tela,
            indices_imagens=indices_imagens,
            total_frames=total_frames,
            cor_texto=cor_texto,
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
        card_w, card_h = 1320, 742
        card_x, card_y = 300, 169
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
            LayoutFactory._scale_crop("fg_src", "fg_sized", card_w, card_h),
            # O Ken Burns ocorre dentro do cartão em alta resolução. Mudar
            # a geometria externa a cada frame força arredondamentos inteiros
            # no overlay e produz o tremor visível em TVs; por isso o cartão
            # permanece estável enquanto a foto faz o zoom contínuo.
            "[fg_sized]null[fg]",
            # A imagem principal entra por baixo e se acomoda no centro. O
            # Ken Burns da fonte continua até o ultimo frame da cena.
            LayoutFactory._overlay_animado(
                "bg",
                "fg",
                "v0",
                str(card_x),
                f"if(lt(t,0.55),1080-{1080 - card_y}*(t/0.55),{card_y})",
            ),
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
                    250,
                    250,
                    indices_imagens,
                    total_frames,
                    animar=False,
                )
            )
        filtros.extend(
            [
                LayoutFactory._overlay_animado(
                    "base", "left", "v0",
                    "if(lt(t,0.45),-580+825*(t/0.45),245)", "255",
                ),
                LayoutFactory._overlay_animado(
                    "v0", "right", "v1",
                    "if(lt(t,0.55),1920-685*(t/0.55),1235)", "420",
                ),
            ]
        )
        if idx_seta is not None:
            filtros.append(LayoutFactory._overlay("v1", "arrow", "v2", 875, 355))
        else:
            # Compatibility for direct factory callers. The horizontal
            # renderer always supplies the persistent PNG arrow.
            filtros.append(
                LayoutFactory._drawtext(
                    "v1",
                    "v2",
                    "↘",
                    "925",
                    "390",
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
        # A chamada pura precisa ter leitura imediata: letras grandes, peso de
        # cartaz e uma linha por bloco, sem competir com elementos visuais.
        quantidade = max(1, len(linhas))
        fontsize = 160 if quantidade == 1 else 138 if quantidade == 2 else 110
        passo_y = fontsize + 30
        altura_total = fontsize + (quantidade - 1) * passo_y
        y_inicial = max(70, (LayoutFactory.CANVAS_H - altura_total) // 2)
        filtros = [LayoutFactory._base_com_fundo_estatico(caminhos_midias, "base")]
        filtros.extend(
            LayoutFactory._drawtext_linhas(
                "base",
                "center_txt",
                linhas,
                x="(w-text_w)/2",
                y_inicial=y_inicial,
                passo_y=passo_y,
                fontsize=fontsize,
                fontcolor="white",
                borderw=8,
                bordercolor="black@0.88",
                font_preset="display",
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
        # Este caption precisa ocupar a imagem como uma chamada editorial,
        # não como uma única faixa curta: divida-o em até duas linhas.
        linhas = LayoutFactory._linhas_texto_em_duas_linhas(textos)
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
            LayoutFactory._overlay_animado(
                "base", "left", "v0", "270",
                "if(lt(t,0.50),1080-875*(t/0.50),205)",
            ),
            LayoutFactory._overlay_animado(
                "v0", "right", "v1",
                "if(lt(t,0.55),1920-750*(t/0.55),1170)", "205",
            ),
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
                560,
                820,
                indices_imagens,
                total_frames,
            ),
            LayoutFactory._overlay_animado(
                "base", "phone", "v0",
                "if(lt(t,0.50),-560+810*(t/0.50),250)", "130",
            ),
        ]
        label_atual = "v0"
        proximo_inicio = LayoutFactory.TYPING_START_SECONDS
        if linhas:
            filtros.extend(
                LayoutFactory._drawtext_digitado(
                    label_atual,
                    "right_title",
                    linhas[0],
                    "1330-(text_w/2)",
                    "335",
                    68,
                    LayoutFactory.TEXT_COLOR,
                    inicio=proximo_inicio,
                )
            )
            label_atual = "right_title"
            proximo_inicio += (
                LayoutFactory._duracao_digitacao(linhas[0])
                + LayoutFactory.TYPING_LINE_PAUSE_SECONDS
            )
        for index, linha in enumerate(linhas[1:]):
            proximo = f"right_body{index}"
            filtros.extend(
                LayoutFactory._drawtext_digitado(
                    label_atual,
                    proximo,
                    linha,
                    "1330-(text_w/2)",
                    str(500 + index * 74),
                    58,
                    LayoutFactory.TEXT_COLOR,
                    inicio=proximo_inicio,
                )
            )
            label_atual = proximo
            proximo_inicio += (
                LayoutFactory._duracao_digitacao(linha)
                + LayoutFactory.TYPING_LINE_PAUSE_SECONDS
            )
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
            # O retângulo abre a cena maior no centro e se acomoda à esquerda.
            # A foto quadrada só entra depois, deixando a leitura sequencial.
            "[left]scale=w='trunc(930*(if(lt(t,0.55),1.18-0.18*t/0.55,1))/2)*2':"
            "h='trunc(520*(if(lt(t,0.55),1.18-0.18*t/0.55,1))/2)*2':eval=frame[left_entry]",
            LayoutFactory._overlay_animado(
                "base", "left_entry", "v0",
                "if(lt(t,0.55),411-183*(t/0.55),228)",
                "if(lt(t,0.55),233-43*(t/0.55),190)",
            ),
            LayoutFactory._overlay_animado(
                "v0", "right", "v1",
                "if(lt(t,0.55),1920,if(lt(t,1.10),1920-708*((t-0.55)/0.55),1212))",
                "190",
            ),
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
                inicio=1.12,
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
            # The first rectangular image is intentionally prepared at full
            # canvas size. It starts as the whole screen, then shrinks into
            # the left card before the square photo is introduced.
            *LayoutFactory._filtros_midia(
                idx_esq,
                "left",
                1920,
                1080,
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
            "[left]scale=w='trunc(if(lt(t,0.50),1920-990*(t/0.50),930)/2)*2':"
            "h='trunc(if(lt(t,0.50),1080-560*(t/0.50),520)/2)*2':eval=frame[left_entry]",
            LayoutFactory._overlay_animado(
                "base", "left_entry", "v0",
                "if(lt(t,0.50),188*(t/0.50),188)",
                "if(lt(t,0.50),295*(t/0.50),295)",
            ),
            LayoutFactory._overlay_animado(
                "v0", "right", "v1",
                "if(lt(t,0.50),1920,if(lt(t,1.05),1920-748*((t-0.50)/0.55),1172))",
                "295",
            ),
            LayoutFactory._format("v1", "vout"),
        ]
        return ";".join(filtros)

    @staticmethod
    def _normalizar_cor_texto(cor: str) -> str:
        """Accept only FFmpeg-safe named colors or hexadecimal colors."""

        value = str(cor).strip().lower()
        if value in {"black", "white", "yellow", "red", "blue", "green"}:
            return value
        if re.fullmatch(r"0x[0-9a-f]{6}", value):
            return value
        if re.fullmatch(r"#[0-9a-f]{6}", value):
            return f"0x{value[1:]}"
        raise ValueError("cor_texto deve ser uma cor nomeada suportada ou hexadecimal (#RRGGBB).")

    @staticmethod
    def _cor_contorno_texto(cor: str) -> str:
        """Choose a contrasting outline for the heavy display text."""

        if cor == "black":
            return "white"
        if cor.startswith("0x"):
            red, green, blue = (int(cor[index : index + 2], 16) for index in (2, 4, 6))
            if (red * 299 + green * 587 + blue * 114) < 128000:
                return "white"
        return "black"

    @staticmethod
    def _aplicar_contorno_texto(graph: str, *, habilitada: bool, cor: str) -> str:
        """Applies one explicit outline policy to every drawtext filter."""

        result: list[str] = []
        for filtro in graph.split(";"):
            if "drawtext=" not in filtro:
                result.append(filtro)
                continue
            if not habilitada:
                filtro = re.sub(r":borderw=\d+", "", filtro)
                filtro = re.sub(r":bordercolor=[^:\[]+", "", filtro)
            elif "borderw=" in filtro:
                filtro = re.sub(r"bordercolor=[^:\[]+", f"bordercolor={cor}", filtro)
            else:
                filtro = re.sub(
                    r"(\[[^\[\]]+\])$",
                    f":borderw=4:bordercolor={cor}\\1",
                    filtro,
                )
            result.append(filtro)
        return ";".join(result)

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
        """Lista acumulativa do T12, na mesma geometria consolidada do T11.

        Cada sub-cena normalizada leva quatro posicoes de topico, mas as
        posicoes ainda nao apresentadas chegam como strings vazias. Elas nao
        geram ``drawtext`` e, portanto, nao deixam filtros inertes na cadeia.
        O indice original e preservado no calculo de ``y`` para que a malha do
        T11 continue sendo a referencia geometrica exata.
        """

        idx = LayoutFactory._input_index(caminhos_midias, LayoutFactory._ALIASES_ESQUERDA, 0)
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

        label_atual = "v0"
        for indice, texto in enumerate(textos):
            topico = str(texto or "").strip()
            if not topico:
                continue
            topico_formatado = (
                topico
                if topico.lstrip().startswith(("•", "-", "*"))
                else f"•  {topico}"
            )
            proximo_label = f"topic{indice}"
            filtros.append(
                LayoutFactory._drawtext(
                    label_atual,
                    proximo_label,
                    topico_formatado,
                    "1160",
                    str(260 + indice * 155),
                    64,
                    LayoutFactory.TEXT_COLOR,
                    enable=f"gte(t,{0.25 + indice * 1.15:.2f})",
                )
            )
            label_atual = proximo_label

        filtros.append(LayoutFactory._format(label_atual, "vout"))
        return ";".join(filtros)

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

        overscan_w = LayoutFactory._ceil_even(
            width
            * LayoutFactory.KEN_BURNS_MAX_ZOOM
            * LayoutFactory.KEN_BURNS_SUPERSAMPLE
        )
        overscan_h = LayoutFactory._ceil_even(
            height
            * LayoutFactory.KEN_BURNS_MAX_ZOOM
            * LayoutFactory.KEN_BURNS_SUPERSAMPLE
        )
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
                LayoutFactory._scale_crop_lanczos(
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
        amplitude = LayoutFactory.KEN_BURNS_MAX_ZOOM - 1.0
        return (
            f"[{input_label}]zoompan="
            # ``on`` é o contador absoluto de frames. A curva é calculada
            # contra a duração inteira da cena, sem pzoom/estado acumulado e
            # sem qualquer reset enquanto a imagem em loop é consumida.
            f"z='1+({amplitude:.6f}*on/{frames_movimento})':"
            # Centro fixo: x/y não alternam de lado conforme o zoom avança.
            "d=1:x='floor((iw-iw/zoom)/2)':y='floor((ih-ih/zoom)/2)':"
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
    def _scale_crop_lanczos(input_label: str, output_label: str, width: int, height: int) -> str:
        return (
            f"[{input_label}]scale={width}:{height}:flags=lanczos:force_original_aspect_ratio=increase,"
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
    def _overlay_animado(
        base_label: str,
        media_label: str,
        output_label: str,
        x: str,
        y: str,
    ) -> str:
        """Sobrepoe uma mídia usando expressoes temporais do FFmpeg."""

        return (
            f"[{base_label}][{media_label}]overlay=x='{x}':y='{y}'"
            f"[{output_label}]"
        )

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
        borderw: int = 0,
        bordercolor: str | None = None,
        enable: str | None = None,
        font_preset: str | None = None,
    ) -> str:
        texto_escape = LayoutFactory._escape_drawtext(texto)
        spacing = f":line_spacing={line_spacing}" if line_spacing else ""
        box = ""
        if boxcolor is not None:
            box = f":box=1:boxcolor={boxcolor}:boxborderw={max(0, boxborderw)}"
        border = (
            f":borderw={max(0, borderw)}:bordercolor={bordercolor}"
            if borderw and bordercolor
            else ""
        )
        timeline = f":enable='{enable}'" if enable else ""
        preset = (
            ":fontfile='__SYNTHREEL_EDITORIAL_FONT__'"
            if font_preset == "editorial"
            else ":fontfile='__SYNTHREEL_DISPLAY_FONT__'" if font_preset == "display" else ""
        )
        return (
            f"[{input_label}]drawtext=text='{texto_escape}':fontcolor={fontcolor}:"
            f"fontsize={fontsize}:x={x}:y={y}{spacing}{box}{border}{preset}{timeline}[{output_label}]"
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
        borderw: int = 0,
        bordercolor: str | None = None,
        font_preset: str | None = None,
        inicio: float | None = None,
    ) -> list[str]:
        filtros: list[str] = []
        label_atual = input_label
        proximo_inicio = LayoutFactory.TYPING_START_SECONDS if inicio is None else inicio
        for index, texto in enumerate(textos):
            proximo_label = f"{prefixo_saida}{index}"
            filtros.extend(
                LayoutFactory._drawtext_digitado(
                    label_atual,
                    proximo_label,
                    texto,
                    x,
                    str(y_inicial + index * passo_y),
                    fontsize or LayoutFactory._fontsize_linha(texto),
                    fontcolor or LayoutFactory.TEXT_COLOR,
                    inicio=proximo_inicio,
                    boxcolor=boxcolor,
                    boxborderw=boxborderw,
                    borderw=borderw,
                    bordercolor=bordercolor,
                    font_preset=font_preset,
                )
            )
            label_atual = proximo_label
            proximo_inicio += (
                LayoutFactory._duracao_digitacao(texto)
                + LayoutFactory.TYPING_LINE_PAUSE_SECONDS
            )
        return filtros

    @staticmethod
    def _duracao_digitacao(texto: str) -> float:
        prefixo = "•  " if texto.startswith("•  ") else ""
        corpo = texto[len(prefixo) :]
        tamanho_passo = max(1, math.ceil(len(corpo) / LayoutFactory.TYPING_MAX_STEPS))
        etapas = max(1, math.ceil(len(corpo) / tamanho_passo))
        return etapas * LayoutFactory.TYPING_STEP_SECONDS

    @staticmethod
    def _drawtext_digitado(
        input_label: str,
        output_label: str,
        texto: str,
        x: str,
        y: str,
        fontsize: int,
        fontcolor: str,
        *,
        inicio: float,
        boxcolor: str | None = None,
        boxborderw: int = 0,
        borderw: int = 0,
        bordercolor: str | None = None,
        font_preset: str | None = None,
    ) -> list[str]:
        """Revela uma linha em etapas, como escrita, sem alterar seu texto."""

        # O marcador de tópicos entra junto do primeiro caractere legível;
        # exibir somente "•" durante a digitação parece um artefato visual.
        prefixo = "•  " if texto.startswith("•  ") else ""
        corpo = texto[len(prefixo) :]
        limite = max(1, LayoutFactory.TYPING_MAX_STEPS)
        tamanho_passo = max(1, math.ceil(len(corpo) / limite))
        partes = [
            prefixo + corpo[:fim]
            for fim in range(tamanho_passo, len(corpo), tamanho_passo)
        ]
        partes.append(texto)
        filtros: list[str] = []
        label_atual = input_label
        for indice, parcial in enumerate(partes):
            saida = output_label if indice == len(partes) - 1 else f"{output_label}_typing_{indice}"
            inicio_etapa = inicio + indice * LayoutFactory.TYPING_STEP_SECONDS
            if indice == len(partes) - 1:
                enable = f"gte(t,{inicio_etapa:.3f})"
            else:
                fim_etapa = inicio + (indice + 1) * LayoutFactory.TYPING_STEP_SECONDS
                enable = f"between(t,{inicio_etapa:.3f},{fim_etapa:.3f})"
            filtros.append(
                LayoutFactory._drawtext(
                    label_atual,
                    saida,
                    parcial,
                    x,
                    y,
                    fontsize,
                    fontcolor,
                    boxcolor=boxcolor,
                    boxborderw=boxborderw,
                    borderw=borderw,
                    bordercolor=bordercolor,
                    enable=enable,
                    font_preset=font_preset,
                )
            )
            label_atual = saida
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
    def _linhas_texto_em_duas_linhas(textos: Sequence[str]) -> list[str]:
        """Quebra um caption em duas linhas equilibradas, sem omitir texto."""

        texto = " ".join(LayoutFactory._linhas_texto(textos)).strip()
        palavras = texto.split()
        if len(palavras) < 3:
            return [texto or " "]
        alvo = len(texto) / 2
        cursor = 0
        corte = 1
        melhor_distancia = float("inf")
        for indice, palavra in enumerate(palavras[:-1], start=1):
            cursor += len(palavra) + (1 if indice > 1 else 0)
            distancia = abs(cursor - alvo)
            if distancia < melhor_distancia:
                corte = indice
                melhor_distancia = distancia
        return [" ".join(palavras[:corte]), " ".join(palavras[corte:])]

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
