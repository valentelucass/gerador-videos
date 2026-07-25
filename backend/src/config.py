import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_project_env() -> None:
    """Carrega somente variáveis ausentes do .env local, sem imprimir segredos."""
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            os.environ.setdefault(key, value.strip().strip('"').strip("'"))


_load_project_env()
ASSETS = ROOT / "assets"
IMAGE_DIR = ASSETS / "images"
VIDEO_DIR = ASSETS / "videos"
BACKGROUND_DIR = ROOT / "fundos"
DEFAULT_BACKGROUND_NAME = "Wireframe_grid_on_black_background_202607190011.jpeg"
MUSIC_DIR = ROOT / "music"
SOUND_DIR = ROOT / "sound"
WORKSPACE = ROOT / "workspace" / "lotes_horizontais"
# Os intermediários de FFmpeg são grandes e muito mutáveis. Mantê-los fora do
# OneDrive evita sincronização/locks durante a renderização; só o resultado e
# o manifesto final retornam ao workspace do projeto.
RENDER_CACHE_DIR = Path(os.getenv("SYNTHREEL_RENDER_CACHE", Path(os.getenv("LOCALAPPDATA", str(ROOT))) / "SynthReel" / "render-cache"))
# Entrega somente os MP4s prontos em uma pasta estável e legível. Os diretórios
# UUID continuam sendo o registro técnico de cada trabalho, sem misturar os
# artefatos do compositor com os vídeos publicados.
FINAL_OUTPUT_DIR = WORKSPACE / "finalizados"
OUTPUT_DIR = WORKSPACE
VOICE_PREVIEW_DIR = ROOT / "workspace" / "assets" / "horizontal" / "voice_previews"
FFMPEG = os.getenv("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.getenv("FFPROBE_BIN", "ffprobe")

for directory in (IMAGE_DIR, VIDEO_DIR, BACKGROUND_DIR, MUSIC_DIR, WORKSPACE, FINAL_OUTPUT_DIR, VOICE_PREVIEW_DIR, RENDER_CACHE_DIR):
    directory.mkdir(parents=True, exist_ok=True)
