from __future__ import annotations

import json
import logging
import shutil
import traceback
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .config import BACKGROUND_DIR, FINAL_OUTPUT_DIR, IMAGE_DIR, MUSIC_DIR, SOUND_DIR, VOICE_PREVIEW_DIR, WORKSPACE
from .core.horizontal_renderer import render
from .core.tts_neural import TTSNeuralEngine, VOICE_CATALOG
from .models import RenderRequest, Script, ValidationRequest
from .services import (
    AUDIO_EXTENSIONS,
    catalog,
    default_background_name,
    google_flow_prompt,
    semantic_image_bindings,
    validate_script,
)

app = FastAPI(title="Slideshow YouTube API")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.mount("/assets/images", StaticFiles(directory=IMAGE_DIR), name="images")
app.mount("/assets/backgrounds", StaticFiles(directory=BACKGROUND_DIR), name="backgrounds")
app.mount("/assets/sounds", StaticFiles(directory=SOUND_DIR), name="sounds")
app.mount("/assets/voice-previews", StaticFiles(directory=VOICE_PREVIEW_DIR), name="voice-previews")
app.mount("/outputs", StaticFiles(directory=FINAL_OUTPUT_DIR), name="outputs")
LOGGER = logging.getLogger("synthreel.api")


def _complete_automatic_image_bindings(
    script: Script,
    image_bindings: dict[str, str],
    uploaded_images: list[str],
) -> dict[str, str]:
    """Completa somente vínculos semanticamente compatíveis com a cena."""
    return semantic_image_bindings(script, image_bindings, uploaded_images)


@app.get("/api/catalog")
def get_catalog() -> dict[str, object]:
    return catalog()


@app.post("/api/validate")
def validate(request: ValidationRequest) -> dict[str, object]:
    bindings = _complete_automatic_image_bindings(
        request.script, request.manual_image_bindings, request.uploaded_images,
    )
    return validate_script(request.script, bindings)


@app.post("/api/prompts")
def prompts(script: Script) -> list[dict[str, str | int]]:
    return [
        {
            "block_id": block.id,
            "scene_id": scene.id,
            "image_id": scene.image_id,
            "asset_key": scene.asset_key,
            "image": scene.image,
            "suggested_filename": f"{scene.image_id} - ",
            "prompt": google_flow_prompt(script, block.id, scene.id),
        }
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


@app.post("/api/backgrounds")
async def upload_backgrounds(files: list[UploadFile] = File(...)) -> dict[str, list[str]]:
    """Importa fundos locais que podem ser usados e pré-visualizados no painel."""
    saved = []
    for file in files:
        name = Path(file.filename or "").name
        if not name or Path(name).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise HTTPException(status_code=400, detail=f"Imagem de fundo inválida: {file.filename}")
        target = BACKGROUND_DIR / name
        with target.open("wb") as output:
            shutil.copyfileobj(file.file, output)
        saved.append(name)
    return {"saved": saved}


def _write_manifest(path: Path, manifest: dict[str, object]) -> None:
    """Atualiza o job de forma atômica para o polling nunca ler JSON parcial."""
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _read_manifest(path: Path) -> dict[str, object]:
    """Lê o estado persistido de um job com uma mensagem útil se ele corromper."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Não foi possível ler o manifesto do trabalho {path.parent.name}.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"O manifesto do trabalho {path.parent.name} não é um objeto JSON.")
    return payload


def _append_job_event(job_dir: Path, event: str, **data: object) -> None:
    """Mantém um histórico JSONL independente do estado atual do manifesto."""
    payload = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": event,
        **data,
    }
    with (job_dir / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _job_logger(job_dir: Path) -> logging.Logger:
    """Cria um log técnico por job sem depender da saída efêmera do Uvicorn."""
    logger = logging.getLogger(f"synthreel.render.{job_dir.name}")
    logger.setLevel(logging.INFO)
    logger.propagate = True
    if not logger.handlers:
        handler = logging.FileHandler(job_dir / "render.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"
        ))
        logger.addHandler(handler)
    return logger


def _close_job_logger(logger: logging.Logger) -> None:
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)


def _completed_job_archive(job_id: str) -> Path:
    """Retorna o registro mínimo de um lote já limpo após publicação."""
    return FINAL_OUTPUT_DIR / ".jobs" / f"{job_id}.json"


def _archive_and_clean_completed_job(job_dir: Path, manifest: dict[str, object]) -> None:
    """Remove artefatos de trabalho somente depois da entrega estar publicada.

    O MP4 final já mora em ``finalizados/``. Conservamos apenas um pequeno
    manifesto nesse diretório para o polling do painel conseguir observar a
    conclusão mesmo depois que a pasta UUID for removida. Falhas nunca passam
    por aqui: seus áudios, logs e eventos ficam disponíveis para diagnóstico.
    """
    workspace = WORKSPACE.resolve()
    target = job_dir.resolve()
    if target.parent != workspace:
        raise ValueError(f"Limpeza recusada para um lote fora do workspace: {target}")
    archive = _completed_job_archive(job_dir.name)
    archive.parent.mkdir(parents=True, exist_ok=True)
    _write_manifest(archive, manifest)
    shutil.rmtree(target)


def _update_render_progress(manifest_path: Path, progress: int, stage: str, logger: logging.Logger | None = None) -> None:
    manifest = _read_manifest(manifest_path)
    current = int(manifest.get("progress", 0))
    # Um callback tardio nunca pode fazer a barra andar para trás.
    safe_progress = max(current, min(99, int(progress)))
    manifest.update({"status": "rendering", "progress": safe_progress, "stage": stage})
    _write_manifest(manifest_path, manifest)
    _append_job_event(manifest_path.parent, "progress", progress=safe_progress, stage=stage)
    if logger is not None:
        logger.info("[%s%%] %s", safe_progress, stage)


def _public_failure(exc: Exception) -> tuple[str, str]:
    """Separa orientação para o operador do detalhe técnico já salvo no log."""
    detail = str(exc).strip() or exc.__class__.__name__
    if isinstance(exc, (ValueError, FileNotFoundError)):
        return "validation_or_asset_error", detail
    if "time-codes" in detail or "narração" in detail:
        return "acoustic_timing_error", detail
    if "FFmpeg" in detail or "Compositor" in detail:
        return "composition_error", "A composição do vídeo falhou. Abra o log técnico para ver a saída do FFmpeg."
    return "internal_render_error", "Ocorreu um erro interno na renderização. Abra o log técnico e envie-o junto com o roteiro."


def _render_in_background(
    script: Script,
    background_image: str,
    music_name: str | None,
    image_bindings: dict[str, str],
    job_dir: Path,
) -> None:
    manifest_path = job_dir / "manifest.json"
    logger = _job_logger(job_dir)
    completed = False
    try:
        logger.info("Job aceito para renderização: fundo=%s; música=%s", background_image, music_name or "sem trilha")
        _append_job_event(job_dir, "render_started", background_image=background_image, music_name=music_name)
        _update_render_progress(manifest_path, 8, "Gerando narração e preparando a composição", logger)
        output = render(
            script,
            BACKGROUND_DIR / background_image,
            job_dir,
            music_name=music_name,
            image_bindings=image_bindings,
            progress_callback=lambda progress, stage: _update_render_progress(manifest_path, progress, stage, logger),
            job_logger=logger,
        )
        manifest = _read_manifest(manifest_path)
        manifest.update({
            "status": "complete",
            "progress": 100,
            "stage": "Vídeo final pronto",
            "output": str(output),
            "output_url": f"/outputs/{output.name}",
        })
        _append_job_event(job_dir, "render_completed", output=str(output))
        logger.info("Renderização concluída com sucesso.")
        completed = True
    except Exception as exc:
        code, message = _public_failure(exc)
        trace = traceback.format_exc()
        logger.error("Renderização falhou (%s): %s\n%s", code, exc, trace)
        manifest = _read_manifest(manifest_path)
        manifest.update({
            "status": "failed",
            "stage": "Falha na renderização",
            "error": message,
            "error_code": code,
            "error_detail": str(exc),
            "error_type": exc.__class__.__name__,
            "log_url": f"/api/jobs/{job_dir.name}/log",
            "events_url": f"/api/jobs/{job_dir.name}/events",
        })
        _append_job_event(job_dir, "render_failed", code=code, error_type=exc.__class__.__name__, error=str(exc))
    finally:
        try:
            _write_manifest(manifest_path, manifest if "manifest" in locals() else _read_manifest(manifest_path))
        finally:
            _close_job_logger(logger)
    if completed:
        try:
            _archive_and_clean_completed_job(job_dir, manifest)
        except Exception:
            # A entrega já está publicada; se a limpeza falhar, preserve o
            # lote para que seja possível removê-lo manualmente depois.
            LOGGER.exception("Não foi possível limpar o lote concluído %s.", job_dir.name)


@app.post("/api/render")
def start_render(request: RenderRequest, background_tasks: BackgroundTasks) -> dict[str, object]:
    image_bindings = _complete_automatic_image_bindings(
        request.script, request.manual_image_bindings, request.uploaded_images,
    )
    report = validate_script(request.script, image_bindings)
    if not report["valid"]:
        LOGGER.warning(
            "Render recusado na validação: título=%r; erros=%s; imagens_pendentes=%s; "
            "enviadas=%s; vínculos_resolvidos=%s.",
            request.script.title,
            report["errors"],
            report["missing_images"],
            len(request.uploaded_images),
            len(image_bindings),
        )
        raise HTTPException(
            status_code=422,
            detail={
                "message": "O roteiro não atende ao contrato de renderização.",
                "errors": report["errors"],
                "missing_images": report["missing_images"],
            },
        )
    if report["missing_images"]:
        LOGGER.warning(
            "Render recusado por imagens sem vínculo: título=%r; pendentes=%s; enviadas=%s; vínculos_resolvidos=%s.",
            request.script.title,
            report["missing_images"],
            len(request.uploaded_images),
            len(image_bindings),
        )
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Não foi possível associar todas as imagens às cenas do roteiro.",
                "missing_images": report["missing_images"],
                "hint": "Use 'Escolha manual por cena' para indicar qual arquivo enviado pertence a cada cena pendente.",
            },
        )
    background_image = request.background_image or default_background_name()
    if not background_image:
        raise HTTPException(status_code=422, detail="Nenhuma imagem de fundo está disponível.")
    if Path(background_image).name != background_image or not (BACKGROUND_DIR / background_image).is_file():
        raise HTTPException(status_code=422, detail="Escolha uma imagem de fundo válida do catálogo.")
    music_name = request.music_name
    if music_name:
        music_path = MUSIC_DIR / music_name
        if (
            Path(music_name).name != music_name
            or music_path.suffix.lower() not in AUDIO_EXTENSIONS
            or not music_path.is_file()
        ):
            raise HTTPException(status_code=422, detail="Escolha uma música válida do catálogo.")

    job_dir = WORKSPACE / uuid4().hex
    job_dir.mkdir(parents=True)
    manifest = {
        "script": request.script.model_dump(by_alias=True),
        # Preserva o contrato editorial do roteiro e registra separadamente a
        # fonte física escolhida para cada nome de cena.
        "image_bindings": image_bindings,
        "resolved_image_sources": report["resolved_image_sources"],
        "background_image": background_image,
        "music_name": music_name,
        "validation": report,
        "status": "rendering",
        "progress": 2,
        "stage": "Trabalho aceito; aguardando renderização",
    }
    _write_manifest(job_dir / "manifest.json", manifest)
    _append_job_event(job_dir, "job_created", scene_count=sum(len(block.scenes) for block in request.script.blocks))
    background_tasks.add_task(
        _render_in_background,
        request.script,
        background_image,
        music_name,
        image_bindings,
        job_dir,
    )
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
    if manifest.is_file():
        return _read_manifest(manifest)
    archived_manifest = _completed_job_archive(job_id)
    if archived_manifest.is_file():
        return _read_manifest(archived_manifest)
    raise HTTPException(status_code=404, detail="Lote não encontrado.")


@app.get("/api/jobs/{job_id}/log", response_class=PlainTextResponse)
def get_job_log(job_id: str) -> str:
    if Path(job_id).name != job_id:
        raise HTTPException(status_code=404, detail="Lote não encontrado.")
    log_path = WORKSPACE / job_id / "render.log"
    if not log_path.is_file():
        raise HTTPException(status_code=404, detail="O log técnico ainda não está disponível.")
    return log_path.read_text(encoding="utf-8", errors="replace")


@app.get("/api/jobs/{job_id}/events", response_class=PlainTextResponse)
def get_job_events(job_id: str) -> str:
    if Path(job_id).name != job_id:
        raise HTTPException(status_code=404, detail="Lote não encontrado.")
    events_path = WORKSPACE / job_id / "events.jsonl"
    if not events_path.is_file():
        raise HTTPException(status_code=404, detail="O histórico técnico ainda não está disponível.")
    return events_path.read_text(encoding="utf-8", errors="replace")
