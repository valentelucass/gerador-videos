import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "assets"
IMAGE_DIR = ASSETS / "images"
BACKGROUND_DIR = ROOT / "fundos"
DEFAULT_BACKGROUND_NAME = "Wireframe_grid_on_black_background_202607190011.jpeg"
MUSIC_DIR = ROOT / "music"
SOUND_DIR = ROOT / "sound"
WORKSPACE = ROOT / "workspace" / "lotes_horizontais"
# Entrega somente os MP4s prontos em uma pasta estável e legível. Os diretórios
# UUID continuam sendo o registro técnico de cada trabalho, sem misturar os
# artefatos do compositor com os vídeos publicados.
FINAL_OUTPUT_DIR = WORKSPACE / "finalizados"
OUTPUT_DIR = WORKSPACE
VOICE_PREVIEW_DIR = ROOT / "workspace" / "assets" / "horizontal" / "voice_previews"
FFMPEG = os.getenv("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.getenv("FFPROBE_BIN", "ffprobe")

for directory in (IMAGE_DIR, BACKGROUND_DIR, WORKSPACE, FINAL_OUTPUT_DIR, VOICE_PREVIEW_DIR):
    directory.mkdir(parents=True, exist_ok=True)
