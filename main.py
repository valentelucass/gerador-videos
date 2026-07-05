"""Official SynthReel command-line entry point."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from src.utils.logger import get_logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Renderiza um video vertical com o SynthReel.")
    parser.add_argument("tema", nargs="*", help="Tema do video. Ex: O imperio de Roma")
    parser.add_argument(
        "--voz",
        default="lucas_clone",
        choices=["lucas_clone", "homem_01", "mulher_01", "mulher_02"],
        help="Voz usada na narracao. Padrao: lucas_clone.",
    )
    parser.add_argument(
        "--manter-artefatos",
        action="store_true",
        help="Mantem audio, downloads Pexels e clipes intermediarios para debug.",
    )
    parser.add_argument(
        "--sem-musica",
        action="store_true",
        help="Renderiza sem musica de fundo.",
    )
    parser.add_argument(
        "--sem-transicoes",
        action="store_true",
        help="Renderiza sem overlays de transicao entre cortes.",
    )
    parser.add_argument(
        "--sem-legendas",
        action="store_true",
        help="Renderiza sem legendas queimadas no video.",
    )
    parser.add_argument(
        "--limpar",
        action="store_true",
        help="Remove sujeira antiga do workspace e preserva apenas MP4s finais.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    tema = " ".join(args.tema).strip()
    logger = get_logger("main")
    inicio = time.time()

    if args.limpar:
        from src.utils.workspace_cleaner import WorkspaceCleaner

        summary = WorkspaceCleaner().clean_existing_artifacts()
        logger.info(
            "SynthReel: limpeza concluida removendo %s arquivos e %s diretorios",
            summary["removed_files"],
            summary["removed_dirs"],
        )
        compact_summary = {
            "mode": summary["mode"],
            "removed_files": summary["removed_files"],
            "removed_dirs": summary["removed_dirs"],
            "removed_mb": round(summary["removed_bytes"] / 1024 / 1024, 2),
            "kept_count": len(summary["kept"]),
            "kept_files": [Path(path).name for path in summary["kept"]],
            "removed_samples": summary.get("removed_samples", []),
            "errors": summary["errors"],
        }
        print(json.dumps(compact_summary, indent=2, ensure_ascii=False))
        return 0

    if not tema:
        raise SystemExit("Informe um tema ou use --limpar para limpar o workspace.")

    try:
        from src.core.pipeline import VideoPipeline

        logger.info("SynthReel: iniciando tema='%s'", tema)
        pipeline = VideoPipeline()
        resultado = pipeline.executar(
            tema,
            voz=args.voz,
            limpar_artefatos=not args.manter_artefatos,
            usar_musica=not args.sem_musica,
            usar_transicoes=not args.sem_transicoes,
            usar_legendas=not args.sem_legendas,
        )
        if pipeline.last_result:
            logger.info("SynthReel: resumo da execucao em %s", pipeline.last_result.get("summary_md"))
    except Exception as exc:
        logger.error("SynthReel: falha na renderizacao: %s", exc)
        return 1

    duracao = time.time() - inicio
    logger.info("SynthReel: concluido em %.2fs", duracao)
    print(json.dumps(resultado.get("videos", {}), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
