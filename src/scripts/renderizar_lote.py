"""Renderiza temas ja preparados e curados manualmente.

Uso:
    python src/scripts/renderizar_lote.py workspace/lotes_preparados/estoicismo/versao_longa/
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.core.pipeline import VideoPipeline
from src.utils.logger import get_logger
from src.utils.text_helpers import normalizar_ascii


OUTPUT_DIR = ROOT_DIR / "workspace" / "output"
METADATA_FILENAME = "metadata.json"
VIDEO_EXTENSIONS = {".mp4"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Renderiza um lote preparado a partir das midias curadas localmente."
    )
    parser.add_argument("pasta_alvo", type=Path, help="Pasta de versao ou tema preparado.")
    parser.add_argument(
        "--voz",
        default="lucas_clone",
        choices=["lucas_clone", "homem_01", "mulher_01", "mulher_02"],
        help="Voz usada na narracao. Padrao: lucas_clone.",
    )
    parser.add_argument(
        "--manter-artefatos",
        action="store_true",
        help="Mantem arquivos intermediarios do Pipeline para auditoria.",
    )
    parser.add_argument("--sem-musica", action="store_true", help="Renderiza sem musica de fundo.")
    parser.add_argument(
        "--sem-transicoes",
        action="store_true",
        help="Renderiza sem overlays de transicao entre cortes.",
    )
    parser.add_argument("--sem-legendas", action="store_true", help="Renderiza sem legendas.")
    return parser.parse_args()


def renderizar_lote(
    pasta_alvo: str | Path,
    *,
    voz: str = "lucas_clone",
    limpar_artefatos: bool = True,
    usar_musica: bool = True,
    usar_transicoes: bool = True,
    usar_legendas: bool = True,
) -> dict[str, Any]:
    """Varre a pasta alvo, injeta midias locais no Pipeline e renderiza cada tema."""

    logger = get_logger(__name__)
    alvo = Path(pasta_alvo).resolve()
    if not alvo.exists() or not alvo.is_dir():
        raise FileNotFoundError(f"Pasta alvo nao encontrada: {alvo}")

    metadata_files = _descobrir_metadata(alvo)
    if not metadata_files:
        raise FileNotFoundError(f"Nenhum metadata.json encontrado em: {alvo}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    pipeline = VideoPipeline()
    resultados: list[dict[str, Any]] = []

    for metadata_path in metadata_files:
        tema_dir = metadata_path.parent
        try:
            metadata = _ler_metadata(metadata_path)
            videos = _listar_videos_curados(tema_dir)
            contexto = _inferir_contexto(metadata_path, metadata)

            if not videos:
                logger.error(
                    "Render lote: nenhum .mp4 curado encontrado em %s; tema pulado.",
                    tema_dir,
                )
                resultados.append(
                    {
                        **contexto,
                        "metadata": str(metadata_path),
                        "diretorio": str(tema_dir),
                        "status": "sem_midias",
                        "erro": "Nenhum .mp4 curado encontrado.",
                    }
                )
                continue

            output_filename = _output_filename(contexto)
            metadata_pipeline = _metadata_para_pipeline(
                metadata=metadata,
                contexto=contexto,
                videos=videos,
                output_filename=output_filename,
            )

            logger.info(
                "Render lote: renderizando %s/%s/%s com %s videos curados.",
                contexto["nicho"],
                contexto["versao"],
                contexto["tema"],
                len(videos),
            )
            resultado = pipeline.executar(
                metadata_pipeline,
                voz=voz,
                limpar_artefatos=limpar_artefatos,
                usar_musica=usar_musica,
                usar_transicoes=usar_transicoes,
                usar_legendas=usar_legendas,
            )
            destino_final = _mover_saida_final(resultado, output_filename)
            resultado["video_final"] = str(destino_final.resolve())
            resultados.append(
                {
                    **contexto,
                    "metadata": str(metadata_path),
                    "diretorio": str(tema_dir),
                    "status": "ok",
                    "videos_curados": [str(path.resolve()) for path in videos],
                    "video_final": str(destino_final.resolve()),
                    "log_dir": resultado.get("log_dir"),
                    "summary_md": resultado.get("summary_md"),
                    "summary_json": resultado.get("summary_json"),
                }
            )
        except Exception as exc:
            logger.error(
                "Render lote: falha ao renderizar %s: %s",
                metadata_path,
                exc,
            )
            resultados.append(
                {
                    "metadata": str(metadata_path),
                    "diretorio": str(metadata_path.parent),
                    "status": "erro_render",
                    "erro": str(exc),
                }
            )

    resumo = {
        "pasta_alvo": str(alvo),
        "metadata_encontrados": len(metadata_files),
        "renderizados": len([item for item in resultados if item.get("status") == "ok"]),
        "pulados": len([item for item in resultados if item.get("status") != "ok"]),
        "output_dir": str(OUTPUT_DIR.resolve()),
        "resultados": resultados,
    }
    logger.info(
        "Render lote: concluido com %s renderizados e %s pulados.",
        resumo["renderizados"],
        resumo["pulados"],
    )
    return resumo


def _descobrir_metadata(alvo: Path) -> list[Path]:
    if alvo.name == METADATA_FILENAME and alvo.is_file():
        return [alvo]
    if (alvo / METADATA_FILENAME).exists():
        return [alvo / METADATA_FILENAME]
    return sorted(alvo.rglob(METADATA_FILENAME), key=lambda path: str(path).lower())


def _ler_metadata(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"metadata.json invalido: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"metadata.json deve ser um objeto JSON: {path}")
    return payload


def _listar_videos_curados(tema_dir: Path) -> list[Path]:
    return sorted(
        [
            path
            for path in tema_dir.iterdir()
            if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
        ],
        key=_natural_key,
    )


def _metadata_para_pipeline(
    *,
    metadata: dict[str, Any],
    contexto: dict[str, str],
    videos: list[Path],
    output_filename: str,
) -> dict[str, Any]:
    metadata_pipeline = copy.deepcopy(metadata)
    metadata_pipeline["nicho"] = contexto["nicho"]
    metadata_pipeline["versao"] = contexto["versao"]
    metadata_pipeline.setdefault("tema", contexto["tema"])
    metadata_pipeline["source"] = "metadata.json+curadoria_local"
    metadata_pipeline["output_filename"] = output_filename
    metadata_pipeline["midias_locais"] = [
        {
            "path": str(path.resolve()),
            "nome": path.name,
            "cena": _indice_cena_por_nome(path),
        }
        for path in videos
    ]
    return metadata_pipeline


def _inferir_contexto(metadata_path: Path, metadata: dict[str, Any]) -> dict[str, str]:
    tema_dir = metadata_path.parent
    tema = _tema_metadata(metadata) or tema_dir.name
    versao = _normalizar_versao(str(metadata.get("versao") or tema_dir.parent.name))
    nicho = str(metadata.get("nicho") or _nicho_por_caminho(metadata_path) or tema_dir.parent.parent.name)
    return {
        "nicho": _slug_filename(nicho),
        "versao": versao,
        "tema": tema,
        "tema_slug": _slug_filename(tema_dir.name or tema),
    }


def _nicho_por_caminho(path: Path) -> str | None:
    parts = list(path.resolve().parts)
    lowered = [part.lower() for part in parts]
    if "lotes_preparados" not in lowered:
        return None
    index = lowered.index("lotes_preparados")
    if index + 1 >= len(parts):
        return None
    return parts[index + 1]


def _tema_metadata(metadata: dict[str, Any]) -> str | None:
    for key in ("tema", "nome_do_tema", "titulo", "title", "nome", "assunto"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _normalizar_versao(value: str) -> str:
    value = value.strip().lower().replace("-", "_").replace(" ", "_")
    if value in {"longa", "longo", "long", "tiktok", "kwai"}:
        return "versao_longa"
    if value in {"curta", "curto", "short", "shorts", "reels"}:
        return "versao_curta"
    return value or "versao_longa"


def _output_filename(contexto: dict[str, str]) -> str:
    return f"{contexto['nicho']}_{contexto['versao']}_{contexto['tema_slug']}.mp4"


def _mover_saida_final(resultado: dict[str, Any], output_filename: str) -> Path:
    origem = Path(str(resultado["video_final"]))
    if not origem.exists() or origem.stat().st_size == 0:
        raise RuntimeError(f"Pipeline nao gerou video final valido: {origem}")

    destino = OUTPUT_DIR / output_filename
    destino.parent.mkdir(parents=True, exist_ok=True)
    origem.replace(destino)
    return destino


def _indice_cena_por_nome(path: Path) -> int | None:
    match = re.match(r"cena_(\d+)(?:_|$)", path.stem, flags=re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def _slug_filename(value: str) -> str:
    slug = normalizar_ascii(value)
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    slug = slug.strip("_")
    return slug or "video"


def _natural_key(path: Path) -> list[int | str]:
    parts = re.split(r"(\d+)", path.name.lower())
    return [int(part) if part.isdigit() else part for part in parts]


def main() -> int:
    args = parse_args()
    logger = get_logger(__name__)

    try:
        resumo = renderizar_lote(
            args.pasta_alvo,
            voz=args.voz,
            limpar_artefatos=not args.manter_artefatos,
            usar_musica=not args.sem_musica,
            usar_transicoes=not args.sem_transicoes,
            usar_legendas=not args.sem_legendas,
        )
    except Exception as exc:
        logger.error("Render lote: falha geral: %s", exc)
        return 1

    print(json.dumps(resumo, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
