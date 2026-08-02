"""Logging em console e arquivo rotativo para execuções de longa duração."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def build_logger(log_dir: Path) -> logging.Logger:
    logger = logging.getLogger("animation_automation")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    file_handler = RotatingFileHandler(log_dir / "automation.log", encoding="utf-8", maxBytes=5_000_000, backupCount=5)
    file_handler.setFormatter(formatter)
    logger.addHandler(console)
    logger.addHandler(file_handler)
    return logger
