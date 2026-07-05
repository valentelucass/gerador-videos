"""Physical integration test for the SynthReel mock pipeline."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.core.pipeline import VideoPipeline
from src.utils.logger import get_logger


def main() -> None:
    logger = get_logger("test_pipeline")
    logger.info("Teste pipeline: inicio")
    pipeline = VideoPipeline()
    resultado = pipeline.executar_mock()
    logger.info("Teste pipeline: fim")
    print(json.dumps(resultado, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

