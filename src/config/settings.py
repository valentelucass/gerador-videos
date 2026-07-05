"""Central settings for SynthReel."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional until requirements are installed.
    load_dotenv = None


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
WORKSPACE_DIR = SRC_DIR / "workspace"
TEMP_DIR = WORKSPACE_DIR / "temp"
OUTPUT_DIR = WORKSPACE_DIR / "output"
ASSETS_DIR = WORKSPACE_DIR / "assets"
BACKGROUND_MUSIC_DIR = ASSETS_DIR / "background_music"
TRANSITIONS_DIR = ASSETS_DIR / "transitions"
VOICE_REFS_DIR = WORKSPACE_DIR / "voice_refs"
LOGS_DIR = ROOT_DIR / "logs"


def _load_env(path: Path) -> None:
    if load_dotenv is not None:
        load_dotenv(path)
        return

    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


_load_env(ROOT_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    """Runtime configuration loaded from environment variables."""

    pexels_api_key: str = os.getenv("PEXELS_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "qwen2.5-coder:7b")
    ffmpeg_bin: str = os.getenv("FFMPEG_BIN", "ffmpeg")
    ffprobe_bin: str = os.getenv("FFPROBE_BIN", "ffprobe")
    default_width: int = int(os.getenv("VIDEO_WIDTH", "1080"))
    default_height: int = int(os.getenv("VIDEO_HEIGHT", "1920"))
    default_fps: int = int(os.getenv("VIDEO_FPS", "25"))


settings = Settings()
