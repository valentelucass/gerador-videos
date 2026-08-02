from __future__ import annotations

from hashlib import sha256
from pathlib import Path


def batches[T](items: list[T], size: int) -> list[list[T]]:
    return [items[index:index + size] for index in range(0, len(items), size)]


def prompt_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8").strip()
    # Permite um comentário inicial de instrução sem enviá-lo à plataforma.
    text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("<!--")).strip()
    if not text:
        raise ValueError(f"O prompt está vazio: {path}")
    return text


def unique_files_by_content(paths: list[Path]) -> tuple[list[Path], list[Path]]:
    """Remove cópias idênticas sem depender de nome ou posição do arquivo."""
    seen: dict[str, Path] = {}
    unique: list[Path] = []
    duplicates: list[Path] = []
    for path in paths:
        digest = sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        key = digest.hexdigest()
        if key in seen:
            duplicates.append(path)
        else:
            seen[key] = path
            unique.append(path)
    return unique, duplicates
