from __future__ import annotations

import re
from pathlib import Path

from .config import BACKGROUND_DIR, IMAGE_DIR, MUSIC_DIR, SOUND_DIR
from .models import Script

MEDIA_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg"}


def list_media(directory: Path, extensions: set[str]) -> list[str]:
    if not directory.exists():
        return []
    return sorted(item.name for item in directory.iterdir() if item.is_file() and item.suffix.lower() in extensions)


def catalog() -> dict[str, list[str]]:
    return {
        "images": list_media(IMAGE_DIR, MEDIA_EXTENSIONS),
        "backgrounds": list_media(BACKGROUND_DIR, MEDIA_EXTENSIONS),
        "music": list_media(MUSIC_DIR, AUDIO_EXTENSIONS),
        "sounds": list_media(SOUND_DIR, AUDIO_EXTENSIONS),
    }


def words(text: str) -> int:
    return len(re.findall(r"\b[\wÀ-ÿ'-]+\b", text))


def google_flow_prompt(script: Script, block_id: str, scene_id: str) -> str:
    scene = next(scene for block in script.blocks if block.id == block_id for scene in block.scenes if scene.id == scene_id)
    visual = scene.visual
    return (
        f"{visual.subject}. {visual.action}. {visual.setting}. "
        f"{visual.framing}. {visual.details}. "
        "Imagem ilustrativa horizontal para documentário do YouTube, sem palavras, sem legendas, sem logotipos, sem marcas d'água."
    )


def validate_script(script: Script) -> dict[str, object]:
    block_ids = [block.id for block in script.blocks]
    scene_ids = [scene.id for block in script.blocks for scene in block.scenes]
    errors: list[str] = []
    if len(block_ids) != len(set(block_ids)):
        errors.append("IDs de blocos precisam ser únicos.")
    if len(scene_ids) != len(set(scene_ids)):
        errors.append("IDs de cenas precisam ser únicos no roteiro.")

    blocks = []
    for block in script.blocks:
        blocks.append({
            "id": block.id,
            "word_count": words(block.text),
            "scene_count": len(block.scenes),
            "status": "ok",
        })

    expected_images = [scene.image for block in script.blocks for scene in block.scenes]
    missing_images = sorted({name for name in expected_images if not (IMAGE_DIR / name).is_file()})
    return {"valid": not errors, "errors": errors, "blocks": blocks, "missing_images": missing_images}


