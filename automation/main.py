from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    # Compatibilidade para quem executar ``python automation/main.py``. Ao
    # remover a pasta automation do primeiro caminho de busca, selectors.py
    # não sombreia o módulo selectors da biblioteca padrão.
    sys.path[0] = str(Path(__file__).resolve().parent.parent)
    from automation.browser import persistent_page
    from automation.config import Settings
    from automation.logger import build_logger
    from automation.workflow import AnimationWorkflow
    from automation.utils import unique_files_by_content
else:
    from .browser import persistent_page
    from .config import Settings
    from .logger import build_logger
    from .workflow import AnimationWorkflow
    from .utils import unique_files_by_content

import asyncio


def input_images(manifest_path: Path) -> list[Path]:
    """Recebe o manifesto seguro criado pela API a partir da biblioteca do painel."""
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    paths = [Path(item) for item in payload.get("images", [])]
    if not paths or any(not path.is_file() for path in paths):
        raise ValueError("O manifesto da automação não contém mídias de cena válidas.")
    return paths


async def main(manifest: Path) -> None:
    settings = Settings.load()
    settings.ensure_directories()
    logger = build_logger(settings.logs_dir)
    source_images = input_images(manifest)
    images, duplicates = unique_files_by_content(source_images)
    if duplicates:
        logger.warning("%s duplicata(s) exata(s) ignorada(s): %s", len(duplicates), ", ".join(item.name for item in duplicates))
    logger.info("Abrindo navegador em nova guia. Headless=%s | imagens únicas=%s", settings.headless, len(images))
    async with persistent_page(settings) as (_, page):
        await AnimationWorkflow(page, settings, logger).run(images, duplicates=duplicates)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Automação de animação das mídias do SynthReel.")
    parser.add_argument("--manifest", required=True, type=Path, help="Manifesto de mídias criado pela API local.")
    arguments = parser.parse_args()
    try:
        asyncio.run(main(arguments.manifest))
    except KeyboardInterrupt:
        print("Automação interrompida pelo operador. O checkpoint foi preservado.")
