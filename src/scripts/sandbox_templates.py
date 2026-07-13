"""Gera previews esterilizados dos 11 layouts horizontais.

Uso:
    python src/scripts/sandbox_templates.py

O sandbox nao le metadata, nao sintetiza narracao e nao executa Whisper. Ele
cria midias sinteticas em um diretorio temporario, delega cada composicao a
``LayoutFactory`` e mantem somente os MP4s de debug em ``workspace/output``.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.config.settings import settings
from src.core.layout_factory import LayoutFactory


OUTPUT_DIR = ROOT_DIR / "workspace" / "output"
TEMP_DIR = ROOT_DIR / "workspace" / "temp"
FPS = 30
DURACAO = 5.0
FRAMES = int(FPS * DURACAO)
TEXTOS_TESTE = {
    4: ("Descrição sem foto!", "Quebra de duas linhas com fonte legal"),
    6: ("Descrição sem foto!", "Quebra de duas linhas com fonte legal"),
    7: ("Alguma informação!",),
    8: (
        "Algumas informações!",
        "Mais e mais informações",
        "aqui nessa parte para preencher",
        "linguiça e mais e mais.",
    ),
    9: (
        "Alguma informação! Mais e mais sendo aqui",
        "no máximo duas linhas para ficar legal",
    ),
    11: ("Topificar 1", "Topificar 2", "Topificar 3", "Topificar 4"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Gera 11 videos curtos para auditoria visual da LayoutFactory."
    )
    parser.add_argument(
        "--ffmpeg",
        default=settings.ffmpeg_bin,
        help="Executavel do FFmpeg (padrao: FFMPEG_BIN ou ffmpeg).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help="Pasta de saida (padrao: workspace/output).",
    )
    return parser.parse_args()


def gerar_sandbox_templates(
    *,
    ffmpeg_bin: str | None = None,
    output_dir: str | Path = OUTPUT_DIR,
) -> list[Path]:
    """Renderiza os templates 1..11 sem depender da esteira horizontal real."""

    ffmpeg = _resolver_executavel(ffmpeg_bin or settings.ffmpeg_bin)
    destino = Path(output_dir).resolve()
    destino.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    factory = LayoutFactory()
    fonte = _resolver_fonte()
    gerados: list[Path] = []

    with tempfile.TemporaryDirectory(prefix="sandbox_templates_", dir=TEMP_DIR) as tmp:
        assets = _gerar_midias_sinteticas(ffmpeg, Path(tmp), fonte)
        mapas = _mapas_de_entrada(assets)

        for template_id in range(1, 12):
            caminhos_midias = mapas[template_id]
            filtro = factory.build_filter_complex(
                template_id,
                caminhos_midias,
                TEXTOS_TESTE.get(template_id, ("TITULO TESTE", "Linha 1")),
                total_frames=FRAMES,
            )
            filtro = _injetar_fonte_drawtext(filtro, fonte)
            output_path = destino / f"debug_template_{template_id:02d}.mp4"
            _renderizar_template(
                ffmpeg=ffmpeg,
                template_id=template_id,
                caminhos_midias=caminhos_midias,
                filtro_layout=filtro,
                output_path=output_path,
            )
            gerados.append(output_path)
            print(f"OK template {template_id:02d}: {output_path}")

    return gerados


def _gerar_midias_sinteticas(
    ffmpeg: str,
    temp_dir: Path,
    fonte: Path,
) -> dict[str, Path]:
    vermelha = temp_dir / "foto_vermelha.jpg"
    azul = temp_dir / "foto_azul.jpg"
    video = temp_dir / "video_silencioso.mp4"
    seta = temp_dir / "seta_teste.png"

    for cor, destaque, output_path in (
        ("red", "yellow", vermelha),
        ("blue", "cyan", azul),
    ):
        _executar(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c={cor}:s=1280x720:r={FPS}",
                "-vf",
                (
                    "drawgrid=w=160:h=90:t=3:c=white@0.75,"
                    f"drawbox=x=40:y=40:w=180:h=120:color={destaque}:t=fill"
                ),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                "-update",
                "1",
                str(output_path),
            ],
            f"geracao da imagem {cor}",
        )

    _executar(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=1280x720:rate={FPS}:duration={DURACAO}",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        "geracao do video silencioso",
    )

    filtro_seta = _injetar_fonte_drawtext(
        "drawtext=text='>':fontcolor=yellow:fontsize=220:"
        "x=(w-text_w)/2:y=(h-text_h)/2",
        fonte,
    )
    _executar(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black@0.0:s=320x320:r=30,format=rgba",
            "-vf",
            filtro_seta,
            "-frames:v",
            "1",
            "-pix_fmt",
            "rgba",
            "-update",
            "1",
            str(seta),
        ],
        "geracao da seta sintetica",
    )

    for path in (vermelha, azul, video, seta):
        _validar_saida(path, "midia sintetica")
    return {"vermelha": vermelha, "azul": azul, "video": video, "seta": seta}


def _mapas_de_entrada(assets: dict[str, Path]) -> dict[int, dict[str, str]]:
    vermelha = str(assets["vermelha"])
    azul = str(assets["azul"])
    video = str(assets["video"])
    return {
        1: {"principal": video},
        2: {"principal": video},
        3: {
            "esquerda": vermelha,
            "direita": azul,
            "seta": str(assets["seta"]),
        },
        4: {},
        5: {"celular_1": vermelha, "celular_2": video, "celular_3": azul},
        6: {"principal": video},
        7: {"esquerda": vermelha, "direita": azul},
        8: {"principal": video},
        9: {"esquerda": vermelha, "direita": azul},
        10: {"esquerda": video, "direita": azul},
        11: {"esquerda": vermelha},
    }


def _renderizar_template(
    *,
    ffmpeg: str,
    template_id: int,
    caminhos_midias: dict[str, str],
    filtro_layout: str,
    output_path: Path,
) -> None:
    args: list[str] = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    for caminho in caminhos_midias.values():
        path = Path(caminho)
        if path.suffix.lower() in {".jpg", ".jpeg", ".png"}:
            args.extend(["-loop", "1", "-framerate", str(FPS), "-i", str(path)])
        else:
            args.extend(["-stream_loop", "-1", "-i", str(path)])

    filtro = (
        f"{filtro_layout};"
        f"[vout]fps={FPS},setsar=1,trim=end_frame={FRAMES},"
        "setpts=PTS-STARTPTS[vdebug]"
    )
    args.extend(
        [
            "-filter_complex",
            filtro,
            "-map",
            "[vdebug]",
            "-an",
            "-frames:v",
            str(FRAMES),
            "-fps_mode",
            "cfr",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    _executar(args, f"renderizacao do template {template_id:02d}")
    _validar_saida(output_path, f"template {template_id:02d}")


def _resolver_executavel(valor: str) -> str:
    path = Path(valor).expanduser()
    if path.is_file():
        return str(path.resolve())
    encontrado = shutil.which(valor)
    if encontrado:
        return encontrado
    raise FileNotFoundError(f"FFmpeg nao encontrado: {valor}")


def _resolver_fonte() -> Path:
    configurada = os.getenv("SYNTHREEL_FONT_FILE", "").strip()
    if configurada:
        path = Path(configurada).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"SYNTHREEL_FONT_FILE nao existe: {path}")
        return path.resolve()

    windir = Path(os.getenv("WINDIR", "C:/Windows"))
    candidatos = (
        windir / "Fonts" / "arial.ttf",
        windir / "Fonts" / "segoeui.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/TTF/DejaVuSans.ttf"),
    )
    for candidato in candidatos:
        if candidato.is_file():
            return candidato.resolve()
    raise FileNotFoundError(
        "Nenhuma fonte encontrada para drawtext. Configure SYNTHREEL_FONT_FILE."
    )


def _injetar_fonte_drawtext(filtro: str, fonte: Path) -> str:
    if "drawtext=" not in filtro:
        return filtro
    path = (
        fonte.as_posix()
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "'" + ("\\" * 3) + "''")
    )
    replacement = f"drawtext=fontfile='{path}':expansion=none:"
    return re.sub(r"drawtext=(?!fontfile=)", lambda _match: replacement, filtro)


def _executar(args: Sequence[str], etapa: str) -> None:
    resultado = subprocess.run(
        [str(arg) for arg in args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if resultado.returncode != 0:
        detalhe = resultado.stderr.strip() or resultado.stdout.strip() or "sem detalhes"
        raise RuntimeError(f"FFmpeg falhou na {etapa}:\n{detalhe}")


def _validar_saida(path: Path, descricao: str) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"{descricao} nao foi gerado ou esta vazio: {path}")


def main() -> int:
    args = parse_args()
    try:
        gerados = gerar_sandbox_templates(
            ffmpeg_bin=args.ffmpeg,
            output_dir=args.output_dir,
        )
    except Exception as exc:
        print(f"ERRO sandbox: {exc}", file=sys.stderr)
        return 1

    print(f"Sandbox concluido: {len(gerados)} videos em {Path(args.output_dir).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
