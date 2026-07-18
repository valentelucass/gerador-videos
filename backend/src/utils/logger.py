"""Clean logging helpers."""

from __future__ import annotations

import logging
import sys
from pathlib import Path


LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
DATE_FORMAT = "%H:%M:%S"


def setup_logging(level: int = logging.INFO) -> None:
    """Configures console logging once."""

    root = logging.getLogger()
    if root.handlers:
        root.setLevel(level)
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """Returns a logger with the project default formatting."""

    setup_logging()
    return logging.getLogger(name)


def configure_file_logging(log_path: str | Path, level: int = logging.INFO) -> Path:
    """Adds a per-run file handler to the root logger."""

    setup_logging(level)
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    resolved = path.resolve()

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_synthreel_run_handler", False):
            root.removeHandler(handler)
            handler.close()

    for handler in root.handlers:
        if isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) == resolved:
            return resolved

    handler = logging.FileHandler(resolved, encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    handler.setLevel(level)
    handler._synthreel_run_handler = True
    root.addHandler(handler)
    root.setLevel(level)
    return resolved
