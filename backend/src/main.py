from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import BACKGROUND_DIR, IMAGE_DIR, VOICE_PREVIEW_DIR, WORKSPACE
from .core.horizontal_renderer import render
from .core.tts_neural import TTSNeuralEngine, VOICE_CATALOG
from .models import RenderRequest, Script
from .services import AUDIO_EXTENSIONS, catalog, google_flow_prompt, validate_script

app = FastAPI(title="Slideshow YouTube API")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.mount("/assets/images", StaticFiles(directory=IMAGE_DIR), name="images")
app.mount("/assets/backgrounds", StaticFiles(directory=BACKGROUND_DIR), name="backgrounds")
app.mount("/assets/voice-previews", StaticFiles(directory=VOICE_PREVIEW_DIR), name="voice-previews")


@app.get("/api/catalog")
def get_catalog() -> dict[str, list[str]]:
    return catalog()


@app.post("/api/validate")
def validate(script: Script) -> dict[str, object]:
    return validate_script(script)


@app.post("/api/prompts")
def prompts(script: Script) -> list[dict[str, str]]:
    return [
        {"block_id": block.id, "scene_id": scene.id, "image": scene.image, "prompt": google_flow_prompt(script, block.id, scene.id)}
        for block in script.blocks for scene in block.scenes
    ]


@app.post("/api/images")
async def upload_images(files: list[UploadFile] = File(...)) -> dict[str, list[str]]:
    saved = []
    for file in files:
        name = Path(file.filename or "").name
        if not name or Path(name).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise HTTPException(status_code=400, detail=f"Imagem inválida: {file.filename}")
        target = IMAGE_DIR / name
        with target.open("wb") as output:
            shutil.copyfileobj(file.file, output)
        saved.append(name)
    return {"saved": saved}


def _render_in_background(request: RenderRequest, job_dir: Path) -> None:
    manifest_path = job_dir / "manifest.json"
    try:
        output = render(request.script, BACKGROUND_DIR / request.background_image, job_dir, request.voice)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update({"status": "complete", "output": str(output)})
    except Exception as exc:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update({"status": "failed", "error": str(exc)})
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


@app.post("/api/render")
def start_render(request: RenderRequest, background_tasks: BackgroundTasks) -> dict[str, object]:
    report = validate_script(request.script)
    if not report["valid"]:
        raise HTTPException(status_code=422, detail=report)
    if report["missing_images"]:
        raise HTTPException(status_code=422, detail={"message": "Envie as imagens antes de renderizar.", "missing_images": report["missing_images"]})
    if Path(request.background_image).name != request.background_image or not (BACKGROUND_DIR / request.background_image).is_file():
        raise HTTPException(status_code=422, detail="Escolha uma imagem de fundo válida do catálogo.")

    job_dir = WORKSPACE / uuid4().hex
    job_dir.mkdir(parents=True)
    manifest = {"script": request.script.model_dump(by_alias=True), "background_image": request.background_image, "validation": report, "status": "rendering"}
    (job_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    background_tasks.add_task(_render_in_background, request, job_dir)
    return {"job_id": job_dir.name, "status": manifest["status"]}


PREVIEW_TEXT = {
    "pt-BR": "Olá. Esta é uma amostra da minha voz para o seu próximo vídeo.",
    "pl-PL": "Cześć. To jest próbka mojego głosu do następnego filmu.",
    "hr-HR": "Pozdrav. Ovo je uzorak mog glasa za vaš sljedeći video.",
    "en-US": "Hello. This is a sample of my voice for your next video.",
    "es-ES": "Hola. Esta es una muestra de mi voz para tu próximo vídeo.",
    "de-DE": "Hallo. Dies ist eine Hörprobe meiner Stimme für dein nächstes Video.",
}


def _preview_filename(voice: str) -> str:
    return f"{voice}.mp3"


def _voice_response() -> dict[str, object]:
    languages: list[dict[str, object]] = []
    for locale, groups in VOICE_CATALOG.items():
        rendered_groups: dict[str, list[dict[str, str | None]]] = {}
        for gender, voices in groups.items():
            rendered_groups[gender] = [
                {"id": voice, "preview_url": f"/assets/voice-previews/{_preview_filename(voice)}" if (VOICE_PREVIEW_DIR / _preview_filename(voice)).is_file() else None}
                for voice in voices
            ]
        languages.append({"locale": locale, "groups": rendered_groups})
    total = sum(len(voices) for groups in VOICE_CATALOG.values() for voices in groups.values())
    generated = sum(1 for voice in (voice for groups in VOICE_CATALOG.values() for voices in groups.values() for voice in voices) if (VOICE_PREVIEW_DIR / _preview_filename(voice)).is_file())
    return {"languages": languages, "total": total, "generated": generated}


@app.get("/api/voices")
def voices() -> dict[str, object]:
    return _voice_response()


def _generate_voice_previews() -> None:
    engine = TTSNeuralEngine()
    for locale, groups in VOICE_CATALOG.items():
        for voices in groups.values():
            for voice in voices:
                target = VOICE_PREVIEW_DIR / _preview_filename(voice)
                if not target.is_file() or target.stat().st_size == 0:
                    engine.synthesize_sync(PREVIEW_TEXT[locale], locale, target, voice)


@app.post("/api/voice-previews")
def generate_voice_previews(background_tasks: BackgroundTasks) -> dict[str, object]:
    background_tasks.add_task(_generate_voice_previews)
    return {"status": "generating", **_voice_response()}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, object]:
    if Path(job_id).name != job_id:
        raise HTTPException(status_code=404, detail="Lote não encontrado.")
    manifest = WORKSPACE / job_id / "manifest.json"
    if not manifest.is_file():
        raise HTTPException(status_code=404, detail="Lote não encontrado.")
    return json.loads(manifest.read_text(encoding="utf-8"))

