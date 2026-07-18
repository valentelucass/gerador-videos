"""HTTP boundary for the local React dashboard.

This module deliberately only orchestrates the existing horizontal scripts;
rendering, TTS and layout contracts remain in their dedicated Python modules.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
import subprocess
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.src.utils.text_helpers import normalizar_ascii
from backend.src.core.layout_factory import LayoutFactory
from backend.src.scripts.preparar_horizontal import TEMPLATE_MEDIA_COUNTS
from backend.src.core.tts_neural import TTSNeuralEngine

ROOT = Path(__file__).resolve().parents[2]
PROJECTS = ROOT / ".synthreel" / "projects"
PREVIEWS = ROOT / ".synthreel" / "previews"
LOGS_FILE = ROOT / ".synthreel" / "project_logs.json"
OUTPUT = ROOT / "workspace" / "output" / "horizontal"
ASSETS = ROOT / "workspace" / "assets" / "horizontal"
JOBS: dict[str, dict[str, object]] = {}
MAX_PROCESS_LOGS = 30


def _load_project_logs() -> list[dict[str, object]]:
    """Loads the small persistent project-only activity history."""

    try:
        raw = json.loads(LOGS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)][-MAX_PROCESS_LOGS:]


PROCESS_LOGS: list[dict[str, object]] = _load_project_logs()

app = FastAPI(title="SynthReel Local API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"], allow_headers=["*"],
)
OUTPUT.mkdir(parents=True, exist_ok=True)
app.mount("/media/outputs", StaticFiles(directory=OUTPUT), name="outputs")
app.mount("/media/assets", StaticFiles(directory=ASSETS), name="assets")
PROJECTS.mkdir(parents=True, exist_ok=True)
app.mount("/media/projects", StaticFiles(directory=PROJECTS), name="projects")
PREVIEWS.mkdir(parents=True, exist_ok=True)
app.mount("/media/previews", StaticFiles(directory=PREVIEWS), name="previews")


class ProjectPayload(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    niche: str = Field(min_length=1, max_length=80)
    content: dict
    music_track: str | None = None
    transition_pool: list[str] = Field(default_factory=list)
    transition_assignments: dict[str, str] = Field(default_factory=dict)
    background_default: str | None = None
    background_by_scene: dict[str, str] = Field(default_factory=dict)
    text_color: str = Field(default="black", max_length=20)
    text_border_enabled: bool = True
    text_border_color: str = Field(default="white", max_length=20)
    text_styles: dict[str, dict[str, object]] = Field(default_factory=dict)


class RenameProjectPayload(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class RenderPayload(BaseModel):
    theme_directory: str


class PreviewPayload(BaseModel):
    scene_index: int = Field(ge=0)
    background_default: str | None = None


def _text_style(policy: object, template_id: int) -> dict[str, object]:
    """Resolve one of the three independent editor text styles."""

    data = policy if isinstance(policy, dict) else {}
    styles = data.get("styles") if isinstance(data.get("styles"), dict) else {}
    key = "template_4" if template_id == 4 else "template_6" if template_id == 6 else "others"
    chosen = styles.get(key) if isinstance(styles.get(key), dict) else data
    chosen = chosen if isinstance(chosen, dict) else {}
    return {
        "color": LayoutFactory._normalizar_cor_texto(str(chosen.get("color", data.get("color", "black")))),
        "border_enabled": bool(chosen.get("border_enabled", data.get("border_enabled", True))),
        "border_color": LayoutFactory._normalizar_cor_texto(str(chosen.get("border_color", data.get("border_color", "white")))),
    }


def _record_process_log(
    kind: str,
    *,
    status: str,
    command: list[str],
    output: str = "",
    project_id: str | None = None,
) -> None:
    """Keeps the most recent project activity across local API restarts."""

    if not project_id:
        return
    PROCESS_LOGS.append({
        "id": uuid4().hex,
        "kind": kind,
        "status": status,
        "command": command,
        "output": output[-12000:],
        "project_id": project_id,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    })
    del PROCESS_LOGS[:-MAX_PROCESS_LOGS]
    try:
        LOGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOGS_FILE.write_text(json.dumps(PROCESS_LOGS, ensure_ascii=False), encoding="utf-8")
    except OSError:
        # Logs are diagnostics; a failed write must never abort a render.
        pass


def _drawtext_font() -> Path:
    """Resolve a deterministic Windows font so FFmpeg never needs Fontconfig."""
    candidates = [
        os.getenv("SYNTHREEL_FONT_FILE", ""),
        str(Path(os.getenv("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf"),
        str(Path(os.getenv("WINDIR", "C:/Windows")) / "Fonts" / "segoeui.ttf"),
    ]
    font = next((Path(value) for value in candidates if value and Path(value).is_file()), None)
    if font is None:
        raise HTTPException(status_code=500, detail="Fonte local não encontrada. Configure SYNTHREEL_FONT_FILE para um arquivo .ttf.")
    return font


def _escape_filter_path(path: Path) -> str:
    return path.as_posix().replace("\\", "\\\\").replace(":", "\\:").replace("'", "'" + ("\\" * 3) + "''")


def _inject_drawtext_font(graph: str) -> str:
    """Give every drawtext a fontfile; this avoids the broken Fontconfig lookup."""
    if "drawtext=" not in graph:
        return graph
    font_path = _escape_filter_path(_drawtext_font())
    windir = Path(os.getenv("WINDIR", "C:/Windows"))
    display = windir / "Fonts" / "ariblk.ttf"
    if not display.is_file():
        display = windir / "Fonts" / "arialbd.ttf"
    display_path = _escape_filter_path(display if display.is_file() else _drawtext_font())
    graph = graph.replace("__SYNTHREEL_EDITORIAL_FONT__", font_path)
    graph = graph.replace("__SYNTHREEL_DISPLAY_FONT__", display_path)
    return re.sub(r"drawtext=(?!fontfile=)", f"drawtext=fontfile='{font_path}':expansion=none:", graph)


def _screen_text_for_preview(scene: dict) -> list[str]:
    """Mirror T12's authoring contract before its renderer-side expansion."""
    raw = scene.get("textos_tela") or scene.get("texto_tela")
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if scene.get("template_id") == 12 and isinstance(scene.get("sub_cenas"), list):
        topics = [str(item.get("topico", "")).strip() for item in scene["sub_cenas"] if isinstance(item, dict)]
        if topics:
            return topics
    return [str(scene.get("texto", ""))]


def _scene_narration_text(scene: dict) -> str:
    """Resolve the authoring-only T12 sub-scenes into spoken editor text."""
    if scene.get("template_id") == 12 and isinstance(scene.get("sub_cenas"), list):
        parts = [str(item.get("texto", "")).strip() for item in scene["sub_cenas"] if isinstance(item, dict)]
        return " ".join(part for part in parts if part)
    return str(scene.get("texto", "")).strip()


def _media_duration(path: Path) -> float:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=20)
    try:
        duration = float(result.stdout.decode("utf-8", errors="replace").strip())
    except ValueError as exc:
        raise RuntimeError(f"Não foi possível ler a duração de {path.name}.") from exc
    if result.returncode or duration <= 0:
        raise RuntimeError(f"FFprobe não retornou duração válida para {path.name}.")
    return duration


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", normalizar_ascii(value)).strip("_") or "projeto"


def _browser_preview(path: Path) -> str | None:
    if path.suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm"}:
        return None
    target = PREVIEWS / f"{hashlib.sha256(str(path).encode()).hexdigest()}.mp4"
    if not target.exists():
        subprocess.run(["ffmpeg", "-y", "-ss", "0.2", "-i", str(path), "-t", "6", "-vf", "scale=480:-2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return f"/media/previews/{target.name}" if target.is_file() else None


def _preview_scene(project_id: str, scene_index: int, background_default: str | None) -> str:
    """Build a short, disposable browser preview with the production layout factory.

    This intentionally composes only a selected scene.  It never invokes TTS,
    transitions or the final renderer, so checking a template is fast and
    cannot alter the prepared horizontal lot.
    """
    directory = _project_directory(project_id)
    script_path = directory / "roteiro.json"
    if not script_path.is_file():
        raise HTTPException(status_code=404, detail="Roteiro do projeto não encontrado.")
    content = json.loads(script_path.read_text(encoding="utf-8"))
    scenes = _scenes(content)
    if scene_index >= len(scenes) or not isinstance(scenes[scene_index], dict):
        raise HTTPException(status_code=422, detail="Cena selecionada inválida para prévia.")
    scene = scenes[scene_index]
    template_id = scene.get("template_id")
    if not isinstance(template_id, int) or template_id not in TEMPLATE_MEDIA_COUNTS:
        raise HTTPException(status_code=422, detail="Template inválido para prévia.")

    # An isolated scene preview follows the same saved background policy as
    # the complete preview. A caller can still pass an explicit override while
    # the editor is applying a new background before its autosave completes.
    manifest_path = directory / "curadoria.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    except json.JSONDecodeError:
        manifest = {}
    text_policy = manifest.get("text_policy") if isinstance(manifest, dict) else {}
    style = _text_style(text_policy, template_id)
    text_color = style["color"]
    text_border_enabled = style["border_enabled"]
    text_border_color = style["border_color"]
    if background_default is None:
        policy = manifest.get("background_policy") if isinstance(manifest, dict) else {}
        policy = policy if isinstance(policy, dict) else {}
        by_scene = policy.get("by_scene") if isinstance(policy.get("by_scene"), dict) else {}
        selected_background = by_scene.get(str(scene_index + 1), policy.get("default"))
        if isinstance(selected_background, str):
            candidate = Path(selected_background)
            if candidate.is_file() and ASSETS.resolve() in candidate.resolve().parents:
                background_default = str(candidate.resolve().relative_to(ROOT))

    allowed = {".mp4", ".mov", ".mkv", ".webm", ".jpg", ".jpeg", ".png"}
    media = sorted(
        (path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in allowed),
        key=lambda path: path.name.casefold(),
    )
    needed = TEMPLATE_MEDIA_COUNTS[template_id]
    if len(media) < needed:
        raise HTTPException(
            status_code=422,
            detail=f"A cena usa o template {template_id}, que precisa de {needed} mídia(s); há {len(media)} importada(s).",
        )
    # The dashboard follows the same deterministic ingest order as the local
    # project: scene 1 consumes the first required slot(s), scene 2 the next,
    # and so on.  This makes the pre-composed preview match the imported media
    # board instead of showing the first image in every scene.
    previous_slots = sum(
        TEMPLATE_MEDIA_COUNTS.get(item.get("template_id"), 1)
        for item in scenes[:scene_index]
        if isinstance(item, dict)
    )
    selected_media = media[previous_slots : previous_slots + needed]
    if len(selected_media) < needed:
        selected_media = media[:needed]
    roles = (
        [] if needed == 0
        else ["esquerda", "direita"] if template_id in {3, 7, 9, 10}
        else ["celular_1", "celular_2", "celular_3"] if template_id == 5
        else ["esquerda"] if template_id in {11, 12}
        else ["principal"]
    )
    while len(roles) < needed:
        roles.append(f"midia_{len(roles) + 1}")
    paths = {role: str(path) for role, path in zip(roles, selected_media, strict=True)}
    image_indices = frozenset(index for index, path in enumerate(selected_media) if path.suffix.lower() in LayoutFactory.IMAGE_EXTENSIONS)
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for path in selected_media:
        if path.suffix.lower() in LayoutFactory.IMAGE_EXTENSIONS:
            args.extend(["-loop", "1", "-framerate", "30", "-i", str(path)])
        else:
            args.extend(["-stream_loop", "-1", "-i", str(path)])

    if template_id == 3:
        arrow = ASSETS / "overlays" / "seta_apontamento.png"
        if not arrow.is_file():
            raise HTTPException(status_code=422, detail="Asset persistente seta_apontamento.png ausente.")
        args.extend(["-loop", "1", "-framerate", "30", "-i", str(arrow)])
        paths["seta"] = str(arrow)
    if template_id in {3, 4, 7, 8, 9, 10, 11, 12}:
        chosen = None
        if background_default:
            candidate = (ROOT / background_default).resolve()
            if ASSETS.resolve() in candidate.parents and candidate.is_file():
                chosen = candidate
        if chosen is None:
            chosen = next((path for path in sorted((ASSETS / "fundos_estaticos").glob("*")) if path.suffix.lower() in LayoutFactory.IMAGE_EXTENSIONS), None)
        if chosen is None:
            raise HTTPException(status_code=422, detail="Este template requer um fundo estático; selecione ou adicione um fundo.")
        args.extend(["-loop", "1", "-framerate", "30", "-i", str(chosen)])
        paths["fundo_estatico"] = str(chosen)

    screen_text = _screen_text_for_preview(scene)
    # The editor must show the same motion span used by the renderer: four
    # seconds for ordinary scenes and six for the progressive Template 12.
    preview_seconds = 6 if template_id == 12 else 4
    # Bump this when preview-only composition behavior changes.  Cached MP4s
    # must never conceal a newer template contract in the editor.
    preview_contract = "preview-v12-template-ten-right-card"
    digest = hashlib.sha256((preview_contract + "|" + json.dumps(scene, ensure_ascii=False, sort_keys=True) + "|" + "|".join(str(path.stat().st_mtime_ns) for path in selected_media) + "|" + str(screen_text) + "|" + str(background_default) + "|" + str(text_color) + "|" + str(text_border_enabled) + "|" + str(text_border_color)).encode()).hexdigest()
    target = PREVIEWS / f"scene-{project_id}-{scene_index}-{digest[:16]}.mp4"
    if not target.is_file():
        try:
            graph = LayoutFactory.build_filter_complex(template_id, paths, screen_text, indices_imagens=image_indices, total_frames=preview_seconds * 30, cor_texto=str(text_color), borda_texto=text_border_enabled, cor_borda_texto=str(text_border_color))
            graph = _inject_drawtext_font(graph)
            args.extend(["-filter_complex", f"{graph};[vout]fps=30,format=yuv420p,scale=960:540[vpreview]", "-map", "[vpreview]", "-an", "-t", str(preview_seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(target)])
            result = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=False, timeout=90)
        except (OSError, subprocess.TimeoutExpired) as exc:
            _record_process_log("prévia", status="failed", command=args, output=str(exc), project_id=project_id)
            raise HTTPException(status_code=500, detail=f"Falha ao gerar prévia: {exc}") from exc
        if result.returncode or not target.is_file() or target.stat().st_size == 0:
            target.unlink(missing_ok=True)
            detail = result.stderr.decode("utf-8", errors="replace")[-400:]
            _record_process_log("prévia", status="failed", command=args, output=result.stderr.decode("utf-8", errors="replace"), project_id=project_id)
            raise HTTPException(status_code=422, detail=f"Não foi possível compor esta prévia: {detail or 'FFmpeg recusou a mídia.'}")
        _record_process_log("prévia", status="completed", command=args, output=result.stderr.decode("utf-8", errors="replace"), project_id=project_id)
    return f"/media/previews/{target.name}"


def _narration_preview(project_id: str) -> str:
    """Create a cached narration track for timeline scrubbing, never a final render."""
    directory = _project_directory(project_id)
    script_path = directory / "roteiro.json"
    if not script_path.is_file():
        raise HTTPException(status_code=404, detail="Roteiro do projeto não encontrado.")
    content = json.loads(script_path.read_text(encoding="utf-8"))
    texts = [_scene_narration_text(scene) for scene in _scenes(content) if isinstance(scene, dict)]
    text = "\n\n".join(part for part in texts if part)
    if not text:
        raise HTTPException(status_code=422, detail="Não há texto de narração no roteiro.")
    language = str(content.get("idioma") or "pt-BR")
    digest = hashlib.sha256(f"{language}|{text}".encode()).hexdigest()[:20]
    target = PREVIEWS / f"narration-{project_id}-{digest}.mp3"
    if not target.is_file() or target.stat().st_size == 0:
        try:
            TTSNeuralEngine().sintetizar_sync(texto=text, idioma=language, caminho_saida=target)
        except Exception as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=502, detail=f"Não foi possível sintetizar a narração temporária: {exc}") from exc
    return f"/media/previews/{target.name}"


def _full_preview(project_id: str) -> str:
    """Compose a disposable, complete editor playback without invoking final render."""
    directory = _project_directory(project_id)
    content = json.loads((directory / "roteiro.json").read_text(encoding="utf-8"))
    scenes = [scene for scene in _scenes(content) if isinstance(scene, dict)]
    if not scenes:
        raise HTTPException(status_code=422, detail="Não há cenas para compor a prévia completa.")
    manifest_path = directory / "curadoria.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    background_policy = manifest.get("background_policy") or {}
    background = background_policy.get("default")
    by_scene = background_policy.get("by_scene") or {}

    def background_value_for(index: int) -> str | None:
        selected = by_scene.get(str(index + 1), background)
        return str(Path(selected).resolve().relative_to(ROOT)) if isinstance(selected, str) and Path(selected).is_file() else None

    scene_paths = [
        PREVIEWS / Path(_preview_scene(project_id, index, background_value_for(index))).name
        for index in range(len(scenes))
    ]
    narration_path = PREVIEWS / Path(_narration_preview(project_id)).name
    narration_duration = _media_duration(narration_path)
    words = [max(1, len(_scene_narration_text(scene).split())) for scene in scenes]
    assignments = manifest.get("transition_assignments") or {}
    transitions: list[Path | None] = []
    for index in range(len(scenes) - 1):
        value = assignments.get(str(index))
        candidate = Path(value) if isinstance(value, str) else None
        transitions.append(candidate if candidate and candidate.is_file() and ASSETS.resolve() in candidate.resolve().parents else None)
    transition_seconds = sum(0.7 for item in transitions if item)
    visual_budget = max(1.0, narration_duration - transition_seconds)
    scene_durations = [visual_budget * count / sum(words) for count in words]
    # Keep each scene's source duration so a longer narration can hold the
    # final frame instead of looping the whole entry animation again.
    components: list[tuple[Path, float, float]] = []
    for index, path in enumerate(scene_paths):
        source_duration = 6.0 if scenes[index].get("template_id") == 12 else 4.0
        components.append((path, scene_durations[index], source_duration))
        if index < len(transitions) and transitions[index]:
            components.append((transitions[index], 0.7, 0.7))
    music_value = manifest.get("music_track")
    music_path = Path(music_value) if isinstance(music_value, str) else None
    if music_path and (not music_path.is_file() or ASSETS.resolve() not in music_path.resolve().parents):
        music_path = None
    fingerprint = "|".join(f"{path}:{path.stat().st_mtime_ns}:{duration:.3f}:{source_duration:.3f}" for path, duration, source_duration in components)
    digest = hashlib.sha256(f"full-preview-v2-hold-final-frame|{fingerprint}|{narration_path.stat().st_mtime_ns}|{music_path}".encode()).hexdigest()[:18]
    target = PREVIEWS / f"full-{project_id}-{digest}.mp4"
    if target.is_file() and target.stat().st_size:
        return f"/media/previews/{target.name}"
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for path, _, _ in components:
        args.extend(["-i", str(path)])
    narration_index = len(components)
    args.extend(["-i", str(narration_path)])
    music_index: int | None = None
    if music_path:
        music_index = narration_index + 1
        args.extend(["-stream_loop", "-1", "-i", str(music_path)])
    filters = [
        f"[{index}:v]fps=30,scale=960:540:force_original_aspect_ratio=increase,"
        f"crop=960:540,setsar=1,tpad=stop_mode=clone:stop_duration="
        f"{max(0.0, duration - source_duration):.3f},trim=duration={duration:.3f},"
        f"setpts=PTS-STARTPTS[v{index}]"
        for index, (_, duration, source_duration) in enumerate(components)
    ]
    filters.append("".join(f"[v{index}]" for index in range(len(components))) + f"concat=n={len(components)}:v=1:a=0[vout]")
    if music_index is not None:
        filters.append(f"[{narration_index}:a]asplit=2[voice][voice_sidechain]")
        filters.append(f"[{music_index}:a]volume=0.22[music]")
        filters.append("[music][voice_sidechain]sidechaincompress=threshold=0.03:ratio=10:attack=20:release=300[ducked]")
        filters.append("[voice][ducked]amix=inputs=2:normalize=0[aout]")
        audio_map = "[aout]"
    else:
        audio_map = f"{narration_index}:a"
    args.extend(["-filter_complex", ";".join(filters), "-map", "[vout]", "-map", audio_map, "-t", f"{narration_duration:.3f}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(target)])
    try:
        result = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=False, timeout=240)
    except (OSError, subprocess.TimeoutExpired) as exc:
        _record_process_log("prévia completa", status="failed", command=args, output=str(exc), project_id=project_id)
        raise HTTPException(status_code=500, detail=f"Falha ao gerar prévia completa: {exc}") from exc
    if result.returncode or not target.is_file() or not target.stat().st_size:
        target.unlink(missing_ok=True)
        detail = result.stderr.decode("utf-8", errors="replace")
        _record_process_log("prévia completa", status="failed", command=args, output=detail, project_id=project_id)
        raise HTTPException(status_code=422, detail=f"Não foi possível compor a prévia completa: {detail[-4000:]}")
    _record_process_log("prévia completa", status="completed", command=args, project_id=project_id)
    return f"/media/previews/{target.name}"


def _scenes(content: dict) -> list[dict]:
    scenes = content.get("cenas")
    return scenes if isinstance(scenes, list) else []


def _validate(content: dict) -> list[str]:
    errors: list[str] = []
    scenes = _scenes(content)
    if not scenes:
        return ["O roteiro deve conter uma lista não vazia em 'cenas'."]
    for index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            errors.append(f"Cena {index:02d}: formato inválido.")
            continue
        if not str(scene.get("texto", "")).strip() and not scene.get("sub_cenas"):
            errors.append(f"Cena {index:02d}: texto ausente.")
        template = scene.get("template_id")
        if not isinstance(template, int) or not 1 <= template <= 12:
            errors.append(f"Cena {index:02d}: template_id deve estar entre 1 e 12.")
        if template != 4 and not scene.get("fonte_midia"):
            errors.append(f"Cena {index:02d}: fonte_midia ausente.")
    return errors


def _project_directory(project_id: str) -> Path:
    if _slug(project_id) != project_id:
        raise HTTPException(status_code=422, detail="Identificador de projeto inválido.")
    return PROJECTS / project_id


def _save_project(payload: ProjectPayload) -> dict[str, object]:
    errors = _validate(payload.content)
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    project_id = _slug(payload.title)
    directory = _project_directory(project_id)
    directory.mkdir(parents=True, exist_ok=True)
    content = dict(payload.content)
    content["tema"] = payload.title
    target = directory / "roteiro.json"
    target.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    def asset_path(value: str | None) -> str | None:
        if not value:
            return None
        candidate = (ROOT / value).resolve()
        if ASSETS.resolve() not in candidate.parents or not candidate.is_file():
            raise HTTPException(status_code=422, detail="Asset selecionado não pertence à biblioteca do Estúdio.")
        return str(candidate)
    transitions = [asset_path(value) for value in payload.transition_pool]
    assignments = {
        str(cut): asset_path(path)
        for cut, path in payload.transition_assignments.items()
        if str(cut).isdigit() and asset_path(path)
    }
    backgrounds_by_scene = {
        str(index): value
        for index, path in payload.background_by_scene.items()
        if str(index).isdigit() and (value := asset_path(path))
    }
    manifest = {
        "schema_version": 1, "pipeline": "horizontal",
        "music_track": asset_path(payload.music_track),
        "transition_pool": [value for value in transitions if value],
        "transition_assignments": assignments,
        "local_media": [str(path.resolve()) for path in directory.iterdir() if path.is_file() and path.name != "roteiro.json" and path.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".jpg", ".jpeg", ".png"}],
        "visual_policy": {"diversify_consecutive_templates": True},
        "background_policy": {
            "mode": "selecionado",
            "default": asset_path(payload.background_default),
            "by_template": {},
            "by_scene": backgrounds_by_scene,
        },
        "text_policy": {
            "styles": {
                key: _text_style(
                    {
                        "color": payload.text_color,
                        "border_enabled": payload.text_border_enabled,
                        "border_color": payload.text_border_color,
                        "styles": payload.text_styles,
                    },
                    template_id,
                )
                for key, template_id in (("template_4", 4), ("template_6", 6), ("others", 1))
            }
        },
    }
    manifest_path = directory / "curadoria.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"id": project_id, "path": str(target.relative_to(ROOT)), "manifest": str(manifest_path), "scenes": len(_scenes(content))}


async def _run_job(job_id: str, command: list[str], manifest: str | None = None) -> None:
    job = JOBS[job_id]
    job["status"] = "running"
    process = await asyncio.create_subprocess_exec(
        *command, cwd=str(ROOT.parent), env={**os.environ, **({"SYNTHREEL_PROJECT_MANIFEST": manifest} if manifest else {})}, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    job["log"] = output.decode("utf-8", errors="replace")[-12000:]
    job["status"] = "completed" if process.returncode == 0 else "failed"
    job["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    project_id = None
    if manifest:
        try:
            manifest_path = Path(manifest).resolve()
            if manifest_path.parent.parent == PROJECTS.resolve():
                project_id = manifest_path.parent.name
        except OSError:
            pass
    _record_process_log("renderização" if "renderizar_horizontal" in command else "preparo", status=str(job["status"]), command=command, output=str(job["log"]), project_id=project_id)


def _start(command: list[str], manifest: str | None = None) -> dict[str, object]:
    job_id = uuid4().hex
    job = {"id": job_id, "status": "queued", "command": command[1:], "log": "", "created_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    JOBS[job_id] = job
    asyncio.create_task(_run_job(job_id, command, manifest))
    return job


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/logs")
def logs() -> list[dict[str, object]]:
    """Recent preview, preparation and rendering diagnostics for the local UI."""
    return list(reversed(PROCESS_LOGS))


@app.get("/api/assets")
def assets() -> dict[str, list[dict[str, str | None]]]:
    extensions = {".mp4", ".mov", ".mkv", ".webm", ".png", ".jpg", ".jpeg", ".mp3", ".wav", ".m4a", ".aac", ".ogg"}
    result: dict[str, list[dict[str, str | None]]] = {"tracks": [], "backgrounds": [], "transitions": []}
    mapping = {"tracks": ASSETS / "trilhas", "backgrounds": ASSETS / "fundos_estaticos", "transitions": ASSETS / "overlays"}
    for key, folder in mapping.items():
        if folder.exists():
            result[key] = [
                {
                    "name": path.name,
                    "path": str(path.relative_to(ROOT)),
                    "url": _browser_preview(path) or f"/media/assets/{path.relative_to(ASSETS).as_posix()}",
                    "kind": "audio" if path.suffix.lower() in {".mp3", ".wav", ".m4a", ".aac", ".ogg"} else "video" if path.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"} else "image",
                }
                for path in sorted(folder.rglob("*")) if path.is_file() and path.suffix.lower() in extensions
            ]
    return result


@app.get("/api/outputs")
def outputs() -> list[dict[str, object]]:
    if not OUTPUT.exists(): return []
    return [{"name": path.name, "url": f"/media/outputs/{path.name}", "size": path.stat().st_size} for path in sorted(OUTPUT.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)]


@app.get("/api/projects")
def projects() -> list[dict[str, object]]:
    if not PROJECTS.exists():
        return []
    result: list[dict[str, object]] = []
    for directory in sorted((path for path in PROJECTS.iterdir() if path.is_dir()), key=lambda path: path.stat().st_mtime, reverse=True):
        script = directory / "roteiro.json"
        media = [path.name for path in directory.iterdir() if path.is_file() and path.name != "roteiro.json"]
        title = directory.name
        if script.is_file():
            try:
                title = str(json.loads(script.read_text(encoding="utf-8")).get("tema") or title)
            except json.JSONDecodeError:
                pass
        result.append({"id": directory.name, "title": title, "has_script": script.is_file(), "media_count": len(media), "updated_at": datetime.fromtimestamp(directory.stat().st_mtime).astimezone().isoformat(timespec="seconds")})
    return result


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> dict[str, object]:
    """Return all editor state required to resume a local project."""
    directory = _project_directory(project_id)
    script_path = directory / "roteiro.json"
    if not script_path.is_file():
        raise HTTPException(status_code=404, detail="Projeto não encontrado.")
    try:
        content = json.loads(script_path.read_text(encoding="utf-8"))
        manifest_path = directory / "curadoria.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="Projeto salvo possui JSON inválido.") from exc

    def relative_asset(value: object) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            return str(Path(value).resolve().relative_to(ROOT))
        except ValueError:
            return None

    return {
        "id": project_id,
        "content": content,
        "music_track": relative_asset(manifest.get("music_track")),
        "background_default": relative_asset((manifest.get("background_policy") or {}).get("default")),
        "background_by_scene": {
            str(index): path
            for index, value in ((manifest.get("background_policy") or {}).get("by_scene") or {}).items()
            if (path := relative_asset(value))
        },
        "text_styles": (manifest.get("text_policy") or {}).get("styles") or {
            "template_4": _text_style(manifest.get("text_policy"), 4),
            "template_6": _text_style(manifest.get("text_policy"), 6),
            "others": _text_style(manifest.get("text_policy"), 1),
        },
        "transition_pool": [path for value in manifest.get("transition_pool", []) if (path := relative_asset(value))],
        "transition_assignments": {str(cut): path for cut, value in (manifest.get("transition_assignments") or {}).items() if (path := relative_asset(value))},
    }


@app.put("/api/projects/{project_id}")
def rename_project(project_id: str, payload: RenameProjectPayload) -> dict[str, str]:
    """Renames the visible title and moves its isolated project directory."""

    directory = _project_directory(project_id)
    if not directory.is_dir():
        raise HTTPException(status_code=404, detail="Projeto não encontrado.")
    new_id = _slug(payload.title)
    target = _project_directory(new_id)
    if target != directory and target.exists():
        raise HTTPException(status_code=409, detail="Já existe um projeto com esse nome.")
    script_path = directory / "roteiro.json"
    if not script_path.is_file():
        raise HTTPException(status_code=404, detail="Roteiro do projeto não encontrado.")
    try:
        content = json.loads(script_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="Roteiro do projeto possui JSON inválido.") from exc
    content["tema"] = payload.title.strip()
    script_path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    if target != directory:
        shutil.move(str(directory), str(target))
    return {"id": new_id, "title": payload.title.strip()}


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str) -> dict[str, str]:
    """Delete exactly one project folder; persistent asset libraries are untouched."""
    directory = _project_directory(project_id)
    if not directory.is_dir():
        raise HTTPException(status_code=404, detail="Projeto não encontrado.")
    if directory.parent.resolve() != PROJECTS.resolve():
        raise HTTPException(status_code=422, detail="Pasta de projeto inválida.")
    def remove_readonly(func: object, path: str, _exc: object) -> None:
        """Allow removal of a local file left read-only by an encoder."""
        os.chmod(path, 0o700)
        func(path)  # type: ignore[operator]

    try:
        shutil.rmtree(directory, onerror=remove_readonly)
    except OSError as exc:
        raise HTTPException(status_code=409, detail=f"Não foi possível excluir o projeto: {exc}") from exc
    # Previews are disposable derivatives of the project. Remove them too so
    # an old card/video can never reappear after a project with the same name.
    for preview in PREVIEWS.glob(f"*-{project_id}-*"):
        preview.unlink(missing_ok=True)
    return {"id": project_id, "status": "deleted"}


@app.get("/api/projects/{project_id}/media")
def project_media(project_id: str) -> list[dict[str, str]]:
    directory = _project_directory(project_id)
    if not directory.is_dir():
        raise HTTPException(status_code=404, detail="Projeto não encontrado.")
    allowed = {".mp4", ".mov", ".mkv", ".webm", ".jpg", ".jpeg", ".png"}
    return [
        {
            "name": path.name,
            "path": str(path.relative_to(ROOT)),
            "url": f"/media/projects/{project_id}/{path.name}",
            "kind": "video" if path.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"} else "image",
        }
        for path in sorted(directory.iterdir())
        if path.is_file() and path.suffix.lower() in allowed
    ]


@app.post("/api/projects")
def save_project(payload: ProjectPayload) -> dict[str, object]:
    return _save_project(payload)


@app.post("/api/projects/{project_id}/media")
async def upload_media(project_id: str, files: list[UploadFile] = File(...)) -> dict[str, object]:
    directory = _project_directory(project_id)
    if not (directory / "roteiro.json").is_file():
        raise HTTPException(status_code=404, detail="Salve o roteiro antes de enviar mídias.")
    allowed = {".mp4", ".mov", ".mkv", ".webm", ".jpg", ".jpeg", ".png"}
    uploaded: list[str] = []
    for item in files:
        name = Path(item.filename or "").name
        if not name or Path(name).suffix.lower() not in allowed:
            raise HTTPException(status_code=422, detail=f"Tipo de mídia não suportado: {name or 'sem nome'}")
        target = directory / name
        with target.open("wb") as output:
            while chunk := await item.read(1024 * 1024):
                output.write(chunk)
        uploaded.append(name)
    return {"project_id": project_id, "uploaded": uploaded}


@app.post("/api/projects/{project_id}/preview")
def preview_scene(project_id: str, payload: PreviewPayload) -> dict[str, str]:
    return {"url": _preview_scene(project_id, payload.scene_index, payload.background_default)}


@app.post("/api/projects/{project_id}/preview/full")
def preview_full(project_id: str) -> dict[str, str]:
    return {"url": _full_preview(project_id)}


@app.post("/api/projects/{project_id}/narration")
def narration_preview(project_id: str) -> dict[str, str]:
    return {"url": _narration_preview(project_id)}


@app.post("/api/jobs/prepare")
def prepare(payload: ProjectPayload) -> dict[str, object]:
    saved = _save_project(payload)
    source = ROOT / str(saved["path"])
    return _start([sys.executable, "-m", "backend.src.scripts.preparar_horizontal", str(source), payload.niche], str(saved["manifest"]))


@app.post("/api/jobs/render")
def render(payload: RenderPayload) -> dict[str, object]:
    directory = (ROOT / payload.theme_directory).resolve()
    lots = ROOT / "workspace" / "lotes_horizontais"
    if not directory.is_dir() or lots not in directory.parents:
        raise HTTPException(status_code=422, detail="Tema horizontal preparado inválido.")
    return _start([sys.executable, "-m", "backend.src.scripts.renderizar_horizontal", str(directory)])


@app.get("/api/jobs/{job_id}")
def job(job_id: str) -> dict[str, object]:
    if job_id not in JOBS: raise HTTPException(status_code=404, detail="Job não encontrado.")
    return JOBS[job_id]
