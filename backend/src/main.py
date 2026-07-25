from __future__ import annotations

import json
import logging
import math
import os
import subprocess
import sys
import shutil
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from threading import Lock, Thread
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .config import BACKGROUND_DIR, FINAL_OUTPUT_DIR, IMAGE_DIR, MUSIC_DIR, ROOT, SOUND_DIR, VIDEO_DIR, VOICE_PREVIEW_DIR, WORKSPACE
from .core.horizontal_renderer import narration_duration, preview_scene_timing, render
from .core.tts_neural import TTSNeuralEngine, VOICE_CATALOG
from .models import PexelsCandidatesRequest, PexelsDownloadRequest, RenderRequest, Script, TranslationRequest, ValidationRequest
from .pexels import PexelsError, download_selected_video, search_videos, translate_to_portuguese
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
app.mount("/assets/videos", StaticFiles(directory=VIDEO_DIR), name="videos")
app.mount("/assets/backgrounds", StaticFiles(directory=BACKGROUND_DIR), name="backgrounds")
app.mount("/assets/music", StaticFiles(directory=MUSIC_DIR), name="music")
app.mount("/assets/sounds", StaticFiles(directory=SOUND_DIR), name="sounds")
app.mount("/assets/voice-previews", StaticFiles(directory=VOICE_PREVIEW_DIR), name="voice-previews")
app.mount("/outputs", StaticFiles(directory=FINAL_OUTPUT_DIR), name="outputs")
LOGGER = logging.getLogger("synthreel.api")

# A fila é intencionalmente exclusiva da esteira horizontal. Um render 1080p
# abre muitos streams e não pode disputar memória com outro FFmpeg pesado no
# mesmo processo.
_RENDER_MIN_FREE_DISK_ENV = "SYNTHREEL_HORIZONTAL_MIN_FREE_DISK_GIB"
_DEFAULT_RENDER_MIN_FREE_DISK_GIB = 8.0
_GIB = 1024 ** 3
_MANIFEST_REPLACE_ATTEMPTS = 8
_MANIFEST_REPLACE_INITIAL_DELAY_SECONDS = 0.08


@dataclass(frozen=True)
class _QueuedRender:
    script: Script
    background_image: str
    music_name: str | None
    image_bindings: dict[str, str]
    job_dir: Path


_RENDER_QUEUE: Queue[_QueuedRender] = Queue()
_RENDER_QUEUE_LOCK = Lock()
_RENDER_QUEUE_THREAD: Thread | None = None
_RENDER_QUEUE_PENDING_IDS: list[str] = []
_ACTIVE_RENDER_JOB_ID: str | None = None


class RenderDiskSpaceError(RuntimeError):
    """A reserva mínima de disco não está disponível para um job horizontal."""

    def __init__(self, free_bytes: int, required_bytes: int, required_gib: float) -> None:
        self.free_bytes = free_bytes
        self.required_bytes = required_bytes
        self.required_gib = required_gib
        super().__init__(
            "Espaço em disco insuficiente para iniciar a renderização "
            f"({free_bytes / _GIB:.2f} GiB livres; reserva mínima: {required_gib:.2f} GiB)."
        )


def _complete_automatic_image_bindings(
    script: Script,
    image_bindings: dict[str, str],
    uploaded_images: list[str],
) -> dict[str, str]:
    """Completa somente vínculos semanticamente compatíveis com a cena."""
    return semantic_image_bindings(script, image_bindings, uploaded_images)


def _timing_preview(script: Script) -> dict[str, object]:
    """Sintetiza em diretório temporário para revelar cortes antes do render."""
    with TemporaryDirectory(prefix="synthreel_timing_") as directory:
        narration = Path(directory) / "narracao_previa.mp3"
        boundaries = TTSNeuralEngine().synthesize_with_word_boundaries_sync(
            " ".join(block.text for block in script.blocks), script.language, narration, script.voice,
        )
        return preview_scene_timing(script, boundaries, narration_duration(narration))


@app.get("/api/catalog")
def get_catalog() -> dict[str, object]:
    return catalog()


@app.get("/api/script-prompt", response_class=PlainTextResponse)
def get_script_prompt() -> str:
    """Entrega o contrato canônico para o operador copiar sem abrir arquivos."""
    prompt_path = ROOT / "backend" / "PROMPT_JSON_ROTEIRO.md"
    if not prompt_path.is_file():
        raise HTTPException(status_code=404, detail="O prompt canônico não foi encontrado no projeto.")
    return prompt_path.read_text(encoding="utf-8")


@app.post("/api/validate")
def validate(request: ValidationRequest) -> dict[str, object]:
    bindings = _complete_automatic_image_bindings(
        request.script, request.manual_image_bindings, request.uploaded_images,
    )
    report = validate_script(request.script, bindings)
    if request.measure_timing and report["valid"]:
        try:
            report["timing"] = _timing_preview(request.script)
        except Exception as exc:
            LOGGER.exception("Pré-validação acústica falhou para %r.", request.script.title)
            report["timing_error"] = f"Não foi possível medir a narração: {exc}"
    return report


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
        for block in script.blocks for scene in block.scenes if scene.tipo_midia == "imagem"
    ]


@app.post("/api/pexels/candidates")
def pexels_candidates(request: PexelsCandidatesRequest) -> dict[str, object]:
    """Busca alternativas horizontais; nada é baixado antes da aprovação humana."""
    targets = [
        (block, scene)
        for block in request.script.blocks
        for scene in block.scenes
        if scene.tipo_midia == "video_generico" and (request.scene_id is None or scene.id == request.scene_id)
    ]
    if request.scene_id is not None and not targets:
        raise HTTPException(status_code=404, detail="Cena de B-roll não encontrada no roteiro enviado.")

    def item_for(block: object, scene: object) -> dict[str, object]:
        # Os atributos são garantidos pelo contrato Pydantic de Script/Scene.
        query = request.queries.get(scene.id, (scene.asset_key or "").replace("-", " "))
        visual = scene.visual
        reference = " · ".join(part for part in [visual.subject, visual.action, visual.setting] if part)
        item = {
            "scene_id": scene.id,
            "scene_image": scene.image,
            "query": query,
            "asset_key": scene.asset_key,
            "text": block.text,
            "visual_reference": reference,
            "is_annotation": scene.annotation is not None,
        }
        try:
            item["candidates"] = search_videos(query)
        except PexelsError as exc:
            # Uma busca sem resultado ou uma falha transitória não pode fazer
            # a cena desaparecer. Ela continua visível para o operador ajustar
            # a descrição e tentar novamente.
            item["candidates"] = []
            item["search_error"] = str(exc)
        return item

    try:
        # Quatro buscas simultâneas reduzem a espera sem disparar dezenas de
        # requisições paralelas contra o Pexels.
        with ThreadPoolExecutor(max_workers=min(4, max(1, len(targets)))) as executor:
            items = list(executor.map(lambda target: item_for(*target), targets))
        expected_scene_ids = [scene.id for _, scene in targets]
        return {
            "items": items,
            "count": len(targets),
            "expected_scene_ids": expected_scene_ids,
            "folder_url": "/api/pexels/open-folder",
        }
    except PexelsError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/pexels/download")
def pexels_download(request: PexelsDownloadRequest) -> dict[str, object]:
    for block in request.script.blocks:
        for scene in block.scenes:
            if scene.id != request.scene_id:
                continue
            if scene.tipo_midia != "video_generico":
                raise HTTPException(status_code=422, detail="Somente cenas video_generico podem receber B-roll do Pexels.")
            query = request.queries.get(scene.id, (scene.asset_key or "").replace("-", " "))
            try:
                return download_selected_video(query, request.video_id, scene.image)
            except PexelsError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
    raise HTTPException(status_code=404, detail="Cena de B-roll não encontrada no roteiro enviado.")


@app.post("/api/translate")
def translate(request: TranslationRequest) -> dict[str, str]:
    try:
        return {"original": request.text, "portuguese": translate_to_portuguese(request.text, request.source_language)}
    except PexelsError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/pexels/open-folder")
def open_pexels_folder() -> dict[str, str]:
    """Abre somente a pasta fixa e local de B-roll aprovado pelo operador."""
    try:
        if os.name == "nt":
            os.startfile(str(VIDEO_DIR))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(VIDEO_DIR)])
        else:
            subprocess.Popen(["xdg-open", str(VIDEO_DIR)])
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Não foi possível abrir a pasta local de vídeos.") from exc
    return {"folder": str(VIDEO_DIR)}


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
    """Atualiza o job atomicamente, tolerando locks breves do OneDrive/Windows.

    O manifesto é consultado pelo painel durante a renderização. A substituição
    continua atômica para jamais servir JSON parcial, mas sincronizadores e
    antivírus podem manter o arquivo aberto por alguns milissegundos no Windows.
    """
    payload = json.dumps(manifest, ensure_ascii=False, indent=2)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        for attempt in range(_MANIFEST_REPLACE_ATTEMPTS):
            try:
                temporary.write_text(payload, encoding="utf-8")
                os.replace(temporary, path)
                if attempt:
                    LOGGER.info(
                        "Manifesto do job %s publicado após %s tentativa(s) por bloqueio transitório.",
                        path.parent.name,
                        attempt + 1,
                    )
                return
            except PermissionError as exc:
                # WinError 5 (acesso negado) e 32 (arquivo em uso) são locks
                # transitórios comuns em pastas sincronizadas pelo OneDrive.
                winerror = getattr(exc, "winerror", None)
                if winerror not in {5, 32} or attempt == _MANIFEST_REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(_MANIFEST_REPLACE_INITIAL_DELAY_SECONDS * (attempt + 1))
    finally:
        # Após ``os.replace`` o caminho temporário deixa de existir. Se todas
        # as tentativas falharem, não deixamos lixo no workspace do job.
        temporary.unlink(missing_ok=True)


def _read_manifest(path: Path) -> dict[str, object]:
    """Lê o estado persistido de um job com uma mensagem útil se ele corromper."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Não foi possível ler o manifesto do trabalho {path.parent.name}.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"O manifesto do trabalho {path.parent.name} não é um objeto JSON.")
    return payload


def _minimum_free_disk_bytes() -> tuple[int, float]:
    """Lê a reserva de disco da esteira horizontal sem fixá-la no código.

    ``SYNTHREEL_HORIZONTAL_MIN_FREE_DISK_GIB`` aceita um número decimal de
    GiB. A leitura ocorre a cada job para permitir ajustes operacionais sem
    reiniciar o painel.
    """
    raw_value = os.getenv(_RENDER_MIN_FREE_DISK_ENV, str(_DEFAULT_RENDER_MIN_FREE_DISK_GIB))
    try:
        gib = float(raw_value)
    except ValueError:
        gib = _DEFAULT_RENDER_MIN_FREE_DISK_GIB
        LOGGER.warning(
            "%s=%r é inválido; usando a reserva padrão de %.1f GiB.",
            _RENDER_MIN_FREE_DISK_ENV,
            raw_value,
            gib,
        )
    if not math.isfinite(gib) or gib < 0:
        LOGGER.warning(
            "%s=%r precisa ser um número finito maior ou igual a zero; usando %.1f GiB.",
            _RENDER_MIN_FREE_DISK_ENV,
            raw_value,
            _DEFAULT_RENDER_MIN_FREE_DISK_GIB,
        )
        gib = _DEFAULT_RENDER_MIN_FREE_DISK_GIB
    return math.ceil(gib * _GIB), gib


def _check_render_disk_headroom() -> None:
    """Verifica a reserva de disco antes de criar temporários de FFmpeg."""
    required_bytes, required_gib = _minimum_free_disk_bytes()
    free_bytes = shutil.disk_usage(WORKSPACE).free
    if free_bytes >= required_bytes:
        return

    raise RenderDiskSpaceError(free_bytes, required_bytes, required_gib)


def _require_render_disk_headroom() -> None:
    """Recusa um novo job antes de ele criar arquivos temporários pesados."""
    try:
        _check_render_disk_headroom()
    except OSError as exc:
        LOGGER.exception("Não foi possível medir o espaço do workspace horizontal.")
        raise HTTPException(
            status_code=503,
            detail="Não foi possível verificar o espaço em disco para a renderização.",
        ) from exc
    except RenderDiskSpaceError as exc:
        free_gib = exc.free_bytes / _GIB
        required_gib = exc.required_gib

        LOGGER.warning(
            "Render horizontal recusado por espaço em disco: livre=%.2f GiB; reserva=%.2f GiB.",
            free_gib,
            required_gib,
        )
        raise HTTPException(
            status_code=507,
            detail={
                "message": "Espaço em disco insuficiente para iniciar a renderização.",
                "free_gib": round(free_gib, 2),
                "required_free_gib": round(required_gib, 2),
                "setting": _RENDER_MIN_FREE_DISK_ENV,
            },
        ) from exc


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


def _workspace_job_directory(job_dir: Path) -> Path:
    """Confere que uma limpeza jamais escape dos jobs horizontais."""
    workspace = WORKSPACE.resolve()
    target = job_dir.resolve()
    if target.parent != workspace:
        raise ValueError(f"Limpeza recusada para um lote fora do workspace: {target}")
    return target


def _archive_and_clean_completed_job(job_dir: Path, manifest: dict[str, object]) -> None:
    """Remove artefatos de trabalho somente depois da entrega estar publicada.

    O MP4 final já mora em ``finalizados/``. Conservamos apenas um pequeno
    manifesto nesse diretório para o polling do painel conseguir observar a
    conclusão mesmo depois que a pasta UUID for removida. Falhas nunca passam
    por aqui: seus áudios, logs e eventos ficam disponíveis para diagnóstico.
    """
    target = _workspace_job_directory(job_dir)
    archive = _completed_job_archive(job_dir.name)
    archive.parent.mkdir(parents=True, exist_ok=True)
    _write_manifest(archive, manifest)
    shutil.rmtree(target)


_FAILED_JOB_PRESERVED_ARTIFACTS = frozenset({
    "manifest.json",
    "render.log",
    "events.jsonl",
    "roteiro_painel.json",
    "timings_cenas.json",
})


def _preserve_failed_job_artifact(artifact: Path) -> bool:
    """Retém os diagnósticos estáveis sem depender do compositor usado."""
    if artifact.name in _FAILED_JOB_PRESERVED_ARTIFACTS:
        return True
    normalized = artifact.name.casefold()
    return artifact.suffix.casefold() == ".json" and normalized.startswith(("roteiro", "timings"))


def _clean_failed_job_intermediates(job_dir: Path, logger: logging.Logger) -> dict[str, list[str]]:
    """Remove temporários do job, preservando o diagnóstico do job.

    Falhas precisam continuar auditáveis pelo painel. Por isso manifesto, log,
    eventos, roteiro aprovado e time-codes não entram na limpeza. Como cada
    job tem uma pasta isolada e validada, os demais filhos são descartáveis —
    sem acoplar este guardrail aos nomes internos de um compositor específico.
    """
    target = _workspace_job_directory(job_dir)
    removed: list[str] = []
    errors: list[str] = []
    if not target.is_dir():
        return {"removed": removed, "errors": errors, "preserved": []}

    candidates: list[Path] = []
    preserved: list[str] = []
    for artifact in target.iterdir():
        if _preserve_failed_job_artifact(artifact):
            preserved.append(artifact.name)
            continue
        candidates.append(artifact)

    for artifact in candidates:
        try:
            # Nunca seguimos links: um arquivo malformado no job não pode
            # apontar a limpeza para fora do workspace horizontal.
            is_junction = getattr(artifact, "is_junction", lambda: False)()
            if artifact.is_symlink() or is_junction:
                if artifact.is_dir():
                    artifact.rmdir()
                else:
                    artifact.unlink()
            elif artifact.is_file():
                artifact.unlink()
            elif artifact.is_dir():
                shutil.rmtree(artifact)
            else:
                continue
            removed.append(artifact.name)
        except OSError as exc:
            errors.append(f"{artifact.name}: {exc}")
            logger.warning("Não foi possível remover temporário de job falho %s: %s", artifact.name, exc)

    if removed:
        logger.info("Limpeza de falha removeu temporários: %s", ", ".join(removed))
    return {"removed": removed, "errors": errors, "preserved": sorted(preserved)}


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
    if isinstance(exc, RenderDiskSpaceError):
        return "insufficient_disk_space", detail
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
        queued_manifest = _read_manifest(manifest_path)
        queued_manifest.update({
            "status": "rendering",
            "progress": max(3, int(queued_manifest.get("progress", 0))),
            "stage": "Renderização iniciada; preparando narração e composição",
        })
        queued_manifest.pop("queue_position", None)
        _write_manifest(manifest_path, queued_manifest)
        logger.info("Job retirado da fila: fundo=%s; música=%s", background_image, music_name or "sem trilha")
        _append_job_event(job_dir, "render_started", background_image=background_image, music_name=music_name)
        # A fila pode esperar vários minutos. Revalidamos a reserva no instante
        # em que o FFmpeg vai começar, pois outro processo pode ter ocupado o
        # disco depois do aceite HTTP inicial.
        _check_render_disk_headroom()
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
        cleanup = _clean_failed_job_intermediates(job_dir, logger)
        manifest["cleanup"] = {
            "removed": cleanup["removed"],
            "errors": cleanup["errors"],
            "preserved": cleanup["preserved"],
        }
        _append_job_event(
            job_dir,
            "failed_intermediates_cleaned",
            removed=cleanup["removed"],
            errors=cleanup["errors"],
        )
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


def _mark_queue_worker_failure(job: _QueuedRender, exc: Exception) -> None:
    """Não deixa um job preso em ``rendering`` se a própria fila falhar."""
    try:
        manifest_path = job.job_dir / "manifest.json"
        manifest = _read_manifest(manifest_path)
        if manifest.get("status") in {"complete", "failed"}:
            return
        manifest.update({
            "status": "failed",
            "stage": "Falha interna na fila de renderização",
            "error": "A fila de renderização encontrou uma falha interna. Abra o log técnico.",
            "error_code": "queue_worker_error",
            "error_detail": str(exc),
            "error_type": exc.__class__.__name__,
            "log_url": f"/api/jobs/{job.job_dir.name}/log",
            "events_url": f"/api/jobs/{job.job_dir.name}/events",
        })
        cleanup = _clean_failed_job_intermediates(job.job_dir, LOGGER)
        manifest["cleanup"] = {
            "removed": cleanup["removed"],
            "errors": cleanup["errors"],
            "preserved": cleanup["preserved"],
        }
        _write_manifest(manifest_path, manifest)
        _append_job_event(job.job_dir, "queue_worker_failed", error_type=exc.__class__.__name__, error=str(exc))
        _append_job_event(
            job.job_dir,
            "failed_intermediates_cleaned",
            removed=cleanup["removed"],
            errors=cleanup["errors"],
        )
    except Exception:
        LOGGER.exception("Não foi possível registrar a falha interna do job %s.", job.job_dir.name)


def _render_queue_worker() -> None:
    """Consome serialmente a única fila de FFmpeg pesado do processo."""
    global _ACTIVE_RENDER_JOB_ID
    while True:
        job = _RENDER_QUEUE.get()
        try:
            with _RENDER_QUEUE_LOCK:
                if job.job_dir.name in _RENDER_QUEUE_PENDING_IDS:
                    _RENDER_QUEUE_PENDING_IDS.remove(job.job_dir.name)
                _ACTIVE_RENDER_JOB_ID = job.job_dir.name
            _render_in_background(
                job.script,
                job.background_image,
                job.music_name,
                job.image_bindings,
                job.job_dir,
            )
        except Exception as exc:
            # ``_render_in_background`` já converte erros esperados em um
            # manifesto failed. Este bloco cobre apenas uma pane no worker.
            LOGGER.exception("A fila de renderização falhou no job %s.", job.job_dir.name)
            _mark_queue_worker_failure(job, exc)
        finally:
            with _RENDER_QUEUE_LOCK:
                if _ACTIVE_RENDER_JOB_ID == job.job_dir.name:
                    _ACTIVE_RENDER_JOB_ID = None
            _RENDER_QUEUE.task_done()


def _ensure_render_queue_worker() -> None:
    """Inicia um único consumidor, inclusive quando a API não usa lifespan."""
    global _RENDER_QUEUE_THREAD
    with _RENDER_QUEUE_LOCK:
        if _RENDER_QUEUE_THREAD is not None and _RENDER_QUEUE_THREAD.is_alive():
            return
        _RENDER_QUEUE_THREAD = Thread(
            target=_render_queue_worker,
            name="synthreel-horizontal-render-queue",
            daemon=True,
        )
        _RENDER_QUEUE_THREAD.start()


def _enqueue_render(job: _QueuedRender) -> int:
    """Enfileira um job e retorna a posição observada no momento da entrada."""
    _ensure_render_queue_worker()
    with _RENDER_QUEUE_LOCK:
        position = len(_RENDER_QUEUE_PENDING_IDS) + (1 if _ACTIVE_RENDER_JOB_ID else 0) + 1
        # Registre o evento antes de liberar o item para o worker: assim um
        # job muito curto nunca pode ser arquivado antes deste histórico.
        _append_job_event(job.job_dir, "job_queued", queue_position=position)
        _RENDER_QUEUE_PENDING_IDS.append(job.job_dir.name)
        _RENDER_QUEUE.put(job)
    return position


@app.post("/api/render")
def start_render(request: RenderRequest) -> dict[str, object]:
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
        scene_count = sum(len(block.scenes) for block in request.script.blocks)
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
                "hint": (
                    f"Foram enviadas {len(request.uploaded_images)} imagem(ns) para {scene_count} cena(s). "
                    "Os arquivos podem manter nomes descritivos; envie a foto que falta ou use "
                    "'Escolha manual por cena' apenas quando uma foto existente realmente pertencer à cena pendente."
                ),
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

    _require_render_disk_headroom()
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
        "status": "queued",
        "progress": 2,
        "stage": "Trabalho aceito; aguardando a vez na fila de renderização",
    }
    _write_manifest(job_dir / "manifest.json", manifest)
    _append_job_event(job_dir, "job_created", scene_count=sum(len(block.scenes) for block in request.script.blocks))
    try:
        queue_position = _enqueue_render(_QueuedRender(
            script=request.script,
            background_image=background_image,
            music_name=music_name,
            image_bindings=image_bindings,
            job_dir=job_dir,
        ))
    except Exception as exc:
        LOGGER.exception("Não foi possível enfileirar o job horizontal %s.", job_dir.name)
        manifest.update({
            "status": "failed",
            "stage": "Falha ao entrar na fila de renderização",
            "error": "Não foi possível iniciar a fila de renderização.",
            "error_code": "queue_unavailable",
            "error_detail": str(exc),
            "error_type": exc.__class__.__name__,
            "log_url": f"/api/jobs/{job_dir.name}/log",
            "events_url": f"/api/jobs/{job_dir.name}/events",
        })
        _write_manifest(job_dir / "manifest.json", manifest)
        _append_job_event(job_dir, "queue_unavailable", error_type=exc.__class__.__name__, error=str(exc))
        raise HTTPException(
            status_code=503,
            detail="A fila de renderização não pôde ser iniciada. Tente novamente.",
        ) from exc
    return {"job_id": job_dir.name, "status": manifest["status"], "queue_position": queue_position}


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
