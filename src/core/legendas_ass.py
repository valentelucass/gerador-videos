"""ASS subtitle generator for SynthReel viral captions."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

try:
    from src.utils.logger import get_logger
except ModuleNotFoundError:
    ROOT_DIR = Path(__file__).resolve().parents[2]
    if str(ROOT_DIR) not in sys.path:
        sys.path.append(str(ROOT_DIR))
    from src.utils.logger import get_logger


class GeradorLegendasASS:
    """Converts Whisper word timestamps into centered ASS captions."""

    def __init__(
        self,
        width: int = 1080,
        height: int = 1920,
        font_name: str = "Arial",
        font_size: int = 92,
    ) -> None:
        self.width = width
        self.height = height
        self.font_name = font_name
        self.font_size = font_size
        self.logger = get_logger(__name__)

    def gerar_arquivo(
        self,
        timestamps: list[dict[str, Any]],
        output_path: str | Path,
        max_palavras_linha: int = 2,
    ) -> str:
        """Writes an ASS subtitle file and returns its path."""

        if max_palavras_linha <= 0:
            raise ValueError("max_palavras_linha deve ser maior que zero.")
        if not timestamps:
            raise ValueError("timestamps nao pode ser vazio.")

        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        blocos = self._agrupar_palavras(timestamps, max_palavras_linha)
        output_file.write_text(self._render_ass(blocos), encoding="utf-8")
        self.logger.info("Legendas: arquivo ASS gerado em %s com %s blocos", output_file, len(blocos))
        return str(output_file.resolve())

    def _render_ass(self, blocos: list[dict[str, Any]]) -> str:
        lines = [
            "[Script Info]",
            "ScriptType: v4.00+",
            "WrapStyle: 2",
            "ScaledBorderAndShadow: yes",
            f"PlayResX: {self.width}",
            f"PlayResY: {self.height}",
            "",
            "[V4+ Styles]",
            (
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
                "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
                "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"
            ),
            (
                "Style: Viral,"
                f"{self.font_name},{self.font_size},"
                "&H0000FFFF,&H0000FFFF,&H00000000,&H80000000,"
                "-1,0,0,0,100,100,0,0,1,6,2,5,60,60,0,1"
            ),
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]

        for bloco in blocos:
            texto = self._escape_ass_text(bloco["texto"].upper())
            lines.append(
                "Dialogue: 0,"
                f"{self._formatar_tempo_ass(bloco['inicio'])},"
                f"{self._formatar_tempo_ass(bloco['fim'])},"
                f"Viral,,0,0,0,,{texto}"
            )

        lines.append("")
        return "\n".join(lines)

    def _agrupar_palavras(
        self,
        timestamps: list[dict[str, Any]],
        max_palavras_linha: int,
    ) -> list[dict[str, Any]]:
        palavras = []
        for item in timestamps:
            palavra = self._limpar_palavra(str(item.get("palavra", "")))
            if not palavra:
                continue
            inicio = float(item["inicio"])
            fim = float(item["fim"])
            if fim <= inicio:
                continue
            palavras.append({"palavra": palavra, "inicio": inicio, "fim": fim})

        blocos: list[dict[str, Any]] = []
        for index in range(0, len(palavras), max_palavras_linha):
            grupo = palavras[index : index + max_palavras_linha]
            if not grupo:
                continue
            blocos.append(
                {
                    "inicio": grupo[0]["inicio"],
                    "fim": grupo[-1]["fim"],
                    "texto": " ".join(item["palavra"] for item in grupo),
                }
            )
        return blocos

    @staticmethod
    def _limpar_palavra(palavra: str) -> str:
        palavra = palavra.strip()
        palavra = re.sub(r"^[^\wÀ-ÿ]+|[^\wÀ-ÿ]+$", "", palavra)
        return palavra

    @staticmethod
    def _formatar_tempo_ass(segundos: float) -> str:
        total_cent = max(0, int(round(segundos * 100)))
        cent = total_cent % 100
        total_seg = total_cent // 100
        seg = total_seg % 60
        total_min = total_seg // 60
        minuto = total_min % 60
        hora = total_min // 60
        return f"{hora}:{minuto:02d}:{seg:02d}.{cent:02d}"

    @staticmethod
    def _escape_ass_text(texto: str) -> str:
        return texto.replace("\\", "\\\\").replace("{", r"\{").replace("}", r"\}")


if __name__ == "__main__":
    exemplo = [
        {"palavra": "legenda", "inicio": 0.0, "fim": 0.5},
        {"palavra": "viral", "inicio": 0.55, "fim": 1.0},
        {"palavra": "amarela", "inicio": 1.05, "fim": 1.6},
    ]
    path = Path("src/workspace/temp/legendas_teste.ass")
    print(GeradorLegendasASS().gerar_arquivo(exemplo, path))
