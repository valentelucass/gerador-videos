import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "assets"
IMAGE_DIR = ASSETS / "images"
BACKGROUND_DIR = ROOT / "fundos"
MUSIC_DIR = ROOT / "music"
SOUND_DIR = ROOT / "sound"
WORKSPACE = ROOT / "workspace" / "lotes_horizontais"
OUTPUT_DIR = WORKSPACE
VOICE_PREVIEW_DIR = ROOT / "workspace" / "assets" / "horizontal" / "voice_previews"
FFMPEG = os.getenv("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.getenv("FFPROBE_BIN", "ffprobe")

for directory in (IMAGE_DIR, BACKGROUND_DIR, WORKSPACE, VOICE_PREVIEW_DIR):
    directory.mkdir(parents=True, exist_ok=True)

