"""Renderizador horizontal com cartões e cenas fullscreen intercaladas."""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ..config import FFMPEG, FFPROBE, IMAGE_DIR, ROOT
from ..models import Script
from .tts_neural import TTSNeuralEngine

FPS = 24
WIDTH, HEIGHT = 1920, 1080
CARD_W, CARD_H = 1500, 844
FULLSCREEN_RATIO = 0.40
MAX_FULLSCREEN_RUN = 2
MAX_CARD_RUN = 3
# O preview usa exatamente dez quadros de transição. Em 24 fps, 0,40 s seriam
# 9,6 quadros e causariam uma cadência irregular no zoom/xfade.
TRANSITION_FRAMES = 10
TRANSITION_SECONDS = TRANSITION_FRAMES / FPS
FOCUS_POINTS = (
    (0.22, 0.28, 72),
    (0.54, 0.45, 54),
    (0.68, 0.35, 65),
    (0.30, 0.45, 68),
    (0.62, 0.42, 63),
    (0.49, 0.30, 50),
    (0.30, 0.50, 68),
    (0.55, 0.50, 72),
)


def _run(command: list[str]) -> None:
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "erro desconhecido do FFmpeg").strip()
        raise RuntimeError(f"FFmpeg não conseguiu renderizar o vídeo:\n{detail[-1800:]}") from exc


def _duration(path: Path) -> float:
    result = subprocess.run(
        [str(FFPROBE), "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    return float(result.stdout.strip())


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return result or "video"


def _seed(title: str) -> int:
    return int.from_bytes(hashlib.sha256(title.encode("utf-8")).digest()[:8], "big")


def _layout_modes(scenes: list[object]) -> list[str]:
    """O JSON escolhe cada layout; ``zoom_in`` é uma cena fullscreen."""
    return ["fullscreen" if scene.transition.in_ == "zoom_in" else "card" for scene in scenes]


def _transition_directions(scenes: list[object]) -> list[str]:
    """Mantém a direção declarada no roteiro, sem sorteio no renderizador."""
    return [scene.transition.out for scene in scenes]


def _background_filter(animation: str) -> str:
    if animation == "movimento_sutil":
        return (
            "scale=2048:1152:force_original_aspect_ratio=increase,crop=2048:1152,"
            "perspective="
            f"x0='64+14*sin(0.20*on/{FPS})':y0='36+8*cos(0.17*on/{FPS})':"
            f"x1='1984+14*sin(0.20*on/{FPS})':y1='36+8*cos(0.17*on/{FPS})':"
            f"x2='64+14*sin(0.20*on/{FPS})':y2='1116+8*cos(0.17*on/{FPS})':"
            f"x3='1984+14*sin(0.20*on/{FPS})':y3='1116+8*cos(0.17*on/{FPS})':"
            "interpolation=cubic:eval=frame,crop=1920:1080"
        )
    if animation == "movimento_lateral":
        return "scale=2048:1152:force_original_aspect_ratio=increase,crop=2048:1152,crop=1920:1080:x='64+10*sin(0.15*t)':y='36'"
    if animation == "pulsacao":
        return "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,eq=brightness='0.015*sin(0.55*t)':eval=frame"
    return "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080"


def _background_light_filter(animation: str) -> str:
    if animation == "movimento_sutil":
        return (
            "eq=contrast='1.075+0.008*sin(0.18*t)':"
            "brightness='-0.035+0.008*sin(0.42*t)':"
            "saturation='0.76+0.018*sin(0.17*t)':eval=frame"
        )
    return "eq=contrast=1.07:brightness=-0.035:saturation=0.76"


def _scene_durations(script: Script, total_seconds: float) -> list[float]:
    blocks = script.blocks
    weights = [max(1, len(re.findall(r"\b[\wÀ-ÿ'-]+\b", block.text))) for block in blocks]
    total = sum(weights)
    durations: list[float] = []
    for block, weight in zip(blocks, weights, strict=True):
        block_seconds = total_seconds * weight / total
        durations.extend([block_seconds / len(block.scenes)] * len(block.scenes))
    return durations


def _scene_x(index: int, scene: object, modes: list[str], directions: list[str], start: float, seconds: float) -> str:
    """Replica o slide bidirecional do preview para cartões e fullscreen."""
    centered_x = "0" if modes[index] == "fullscreen" else "210"
    width = "main_w" if modes[index] == "fullscreen" else "overlay_w"
    enter_end = start + TRANSITION_SECONDS
    exit_start = start + seconds - TRANSITION_SECONDS

    if index == 0 or scene.transition.in_ == "none":
        entry = centered_x
    elif scene.transition.in_ == "from_left":
        entry = f"-{width}+({centered_x}+{width})*(t-{start:.3f})/{TRANSITION_SECONDS:.3f}"
    elif scene.transition.in_ == "from_right":
        entry = f"main_w-(main_w-{centered_x})*(t-{start:.3f})/{TRANSITION_SECONDS:.3f}"
    elif directions[index - 1] == "to_right":
        entry = f"-{width}+({centered_x}+{width})*(t-{start:.3f})/{TRANSITION_SECONDS:.3f}"
    else:
        entry = f"main_w-(main_w-{centered_x})*(t-{start:.3f})/{TRANSITION_SECONDS:.3f}"

    if index == len(modes) - 1 or scene.transition.out == "none":
        exiting = centered_x
    elif scene.transition.out == "to_right":
        exiting = f"{centered_x}+(main_w-{centered_x})*(t-{exit_start:.3f})/{TRANSITION_SECONDS:.3f}"
    else:
        exiting = f"{centered_x}-({centered_x}+{width})*(t-{exit_start:.3f})/{TRANSITION_SECONDS:.3f}"

    return f"if(lt(t,{enter_end:.3f}),{entry},if(lt(t,{exit_start:.3f}),{centered_x},{exiting}))"


def _fullscreen_filter(index: int, seconds: float) -> str:
    focus_x, focus_y, zoom = FOCUS_POINTS[index % len(FOCUS_POINTS)]
    progress = f"(on/{max(1, round(seconds * FPS) - 1)})"
    viewport_w = f"(2000*2304/(2304+{zoom}*{progress}))"
    viewport_h = f"(({viewport_w})*0.5625)"
    viewport_x = f"(2400-({viewport_w}))*(0.50+({focus_x:.2f}-0.50)*{progress})"
    viewport_y = f"(1350-({viewport_h}))*(0.50+({focus_y:.2f}-0.50)*{progress})"
    return (
        "scale=2400:1350:force_original_aspect_ratio=increase,crop=2400:1350,"
        f"perspective=x0='{viewport_x}':y0='{viewport_y}':"
        f"x1='({viewport_x})+({viewport_w})':y1='{viewport_y}':"
        f"x2='{viewport_x}':y2='({viewport_y})+({viewport_h})':"
        f"x3='({viewport_x})+({viewport_w})':y3='({viewport_y})+({viewport_h})':"
        "sense=source:interpolation=cubic:eval=frame,"
        f"scale={WIDTH}:{HEIGHT}:flags=lanczos,"
        f"fps={FPS},settb=1/{FPS},setpts=PTS-STARTPTS"
    )


def _escape_drawtext(value: str) -> str:
    """Escapa somente os caracteres que têm significado no filtro drawtext."""
    return value.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:").replace("%", "\\%")


def _annotation_window(scene_start: float, scene_seconds: float, at: str, has_emoji: bool) -> tuple[float, float]:
    display_seconds = min(scene_seconds - 0.24, 3.2 if has_emoji else 2.5)
    display_seconds = max(0.8, display_seconds)
    if at == "start":
        start = scene_start + 0.22
    elif at == "end":
        start = scene_start + scene_seconds - display_seconds - 0.20
    else:
        start = scene_start + (scene_seconds - display_seconds) / 2
    return max(scene_start, start), min(scene_start + scene_seconds - 0.06, start + display_seconds)


def _annotation_filters(
    graph: list[str],
    video_label: str,
    annotation_index: int,
    lines: list[str],
    emoji: str | None,
    start: float,
    end: float,
) -> str:
    """Acrescenta uma CTA legível sem alterar a ortografia oficial do JSON."""
    output = f"[annotation{annotation_index}]"
    text = "\\n".join(_escape_drawtext(line) for line in lines)
    enable = f"between(t,{start:.3f},{end:.3f})"
    graph.append(
        f"{video_label}drawtext=fontfile='C\\:/Windows/Fonts/impact.ttf':text='{text}':"
        "fontcolor=white:fontsize=64:borderw=4:bordercolor=black@0.92:"
        "box=1:boxcolor=black@0.32:boxborderw=20:x=(w-text_w)/2:y=(h-text_h)/2:"
        f"enable='{enable}'{output}"
    )
    if not emoji:
        return output

    emoji_output = f"[annotation{annotation_index}_emoji]"
    graph.append(
        f"{output}drawtext=fontfile='C\\:/Windows/Fonts/seguiemj.ttf':text='{_escape_drawtext(emoji)}':"
        "fontcolor=white:fontsize=62:borderw=2:bordercolor=black@0.90:"
        "x='(w+text_w)/2+28':y='h/2-text_h/2':"
        f"enable='{enable}'{emoji_output}"
    )
    return emoji_output


def _render_lite(script: Script, background: Path, job_dir: Path, voice: str | None = None) -> Path:
    """Renderiza uma narrativa horizontal com cartões preservados e destaques fullscreen."""
    if not shutil.which(str(FFMPEG)) and not Path(FFMPEG).is_file():
        raise FileNotFoundError("FFmpeg não foi encontrado. Configure FFMPEG_BIN ou instale o FFmpeg.")
    if not shutil.which(str(FFPROBE)) and not Path(FFPROBE).is_file():
        raise FileNotFoundError("FFprobe não foi encontrado. Configure FFPROBE_BIN ou instale o FFmpeg.")
    if not background.is_file():
        raise FileNotFoundError(f"Imagem de fundo não encontrada: {background.name}")

    scenes = [scene for block in script.blocks for scene in block.scenes]
    missing = [scene.image for scene in scenes if not (IMAGE_DIR / scene.image).is_file()]
    if missing:
        raise FileNotFoundError("Imagens de cena ausentes: " + ", ".join(missing))

    job_dir.mkdir(parents=True, exist_ok=True)
    narration = job_dir / "narracao.mp3"
    text = " ".join(block.text.strip() for block in script.blocks)
    TTSNeuralEngine().synthesize_sync(text, script.language, narration, voice)
    narration_seconds = _duration(narration)
    modes = _layout_modes(scenes)
    directions = _transition_directions(scenes)
    transition = TRANSITION_SECONDS if len(scenes) > 1 else 0.0
    scene_seconds = narration_seconds + transition * max(0, len(scenes) - 1)
    durations = _scene_durations(script, scene_seconds)
    if transition and min(durations) <= transition:
        raise ValueError("As cenas ficaram curtas demais para aplicar a transição horizontal com segurança.")

    inputs = ["-loop", "1", "-framerate", str(FPS), "-i", str(background)]
    for scene, seconds in zip(scenes, durations, strict=True):
        inputs.extend(["-loop", "1", "-framerate", str(FPS), "-t", f"{seconds:.3f}", "-i", str(IMAGE_DIR / scene.image)])

    card_indices = [index for index, mode in enumerate(modes) if mode == "card"]
    graph: list[str] = []
    background_labels: dict[int, str] = {}
    if card_indices:
        labels = "".join(f"[background_{index}]" for index in card_indices)
        graph.append(
            f"[0:v]{_background_filter(script.background_animation)},format=rgb24,"
            "lutrgb=r='min(255,val*5)':g='min(255,val*5)':b='min(255,val*5)',"
            f"{_background_light_filter(script.background_animation)},vignette=PI/4:eval=frame,"
            f"fps={FPS},settb=1/{FPS},split={len(card_indices)}{labels}"
        )
        background_labels = {index: f"[background_{index}]" for index in card_indices}

    scene_starts = [0.0]
    for index in range(1, len(durations)):
        scene_starts.append(scene_starts[-1] + durations[index - 1] - transition)

    scene_labels: list[str] = []
    fullscreen_index = 0
    for index, (scene, seconds, mode) in enumerate(zip(scenes, durations, modes, strict=True)):
        input_index = index + 1
        output = f"[scene{index}]"
        if mode == "fullscreen":
            graph.append(f"[{input_index}:v]{_fullscreen_filter(fullscreen_index, seconds)}{output}")
            fullscreen_index += 1
        else:
            card = f"[card{index}]"
            shadow = f"[shadow{index}]"
            shadow_layer = f"[shadow_layer{index}]"
            centered_x = "(main_w-overlay_w)/2"
            entry = centered_x
            if index and modes[index - 1] == "fullscreen":
                if directions[index - 1] == "to_left":
                    entry = f"main_w-(main_w-{centered_x})*t/{transition:.3f}"
                else:
                    entry = f"-overlay_w+({centered_x}+overlay_w)*t/{transition:.3f}"
            exit_start = seconds - transition
            exiting = centered_x
            if index < len(scenes) - 1 and modes[index + 1] == "fullscreen":
                if directions[index] == "to_left":
                    exiting = f"{centered_x}-({centered_x}+overlay_w)*(t-{exit_start:.3f})/{transition:.3f}"
                elif directions[index] == "to_right":
                    exiting = f"{centered_x}+(main_w-{centered_x})*(t-{exit_start:.3f})/{transition:.3f}"
            card_x = (
                f"if(lt(t,{transition:.3f}),{entry},"
                f"if(lt(t,{exit_start:.3f}),{centered_x},{exiting}))"
            )
            graph.extend([
                f"[{input_index}:v]scale={CARD_W}:{CARD_H}:force_original_aspect_ratio=increase,crop={CARD_W}:{CARD_H},"
                f"fps={FPS},setsar=1,fade=t=in:st=0:d=0.16,"
                "drawbox=x=3:y=3:w=iw-6:h=ih-6:color=white@0.18:t=3,"
                f"settb=1/{FPS},setpts=PTS-STARTPTS,split=2{card}[shadow_source{index}]",
                f"[shadow_source{index}]format=rgba,colorchannelmixer=rr=0:gg=0:bb=0:aa=0.42,boxblur=18:2{shadow}",
                f"{background_labels[index]}trim=duration={seconds:.3f},setpts=PTS-STARTPTS[background_trim{index}]",
                f"[background_trim{index}]{shadow}overlay=x='({card_x})+18':y='(main_h-overlay_h)/2+22':format=auto{shadow_layer}",
                f"{shadow_layer}{card}overlay=x='{card_x}':y='(main_h-overlay_h)/2':format=auto,"
                f"trim=duration={seconds:.3f},settb=1/{FPS},setpts=PTS-STARTPTS{output}",
            ])
        scene_labels.append(output)

    video_label = scene_labels[0]
    elapsed = durations[0]
    for index in range(1, len(scene_labels)):
        output = "[transitioned_video]" if index == len(scene_labels) - 1 else f"[transition_{index}]"
        offset = elapsed - transition
        if modes[index - 1] == "card" and modes[index] == "card":
            graph.append(f"{video_label}{scene_labels[index]}xfade=transition=fade:duration={transition:.3f}:offset={offset:.3f}{output}")
        elif directions[index - 1] == "to_left":
            graph.append(f"{video_label}{scene_labels[index]}xfade=transition=smoothleft:duration={transition:.3f}:offset={offset:.3f}{output}")
        elif directions[index - 1] == "to_right":
            graph.extend([
                f"{video_label}hflip[flip_current_{index}]",
                f"{scene_labels[index]}hflip[flip_next_{index}]",
                f"[flip_current_{index}][flip_next_{index}]xfade=transition=smoothleft:duration={transition:.3f}:offset={offset:.3f}[flip_mix_{index}]",
                f"[flip_mix_{index}]hflip{output}",
            ])
        else:
            graph.append(f"{video_label}{scene_labels[index]}xfade=transition=fade:duration={transition:.3f}:offset={offset:.3f}{output}")
        video_label = output
        elapsed += durations[index] - transition
    annotation_index = 0
    for index, (scene, start, seconds) in enumerate(zip(scenes, scene_starts, durations, strict=True)):
        if scene.annotation is None:
            continue
        annotation_start, annotation_end = _annotation_window(
            start, seconds, scene.annotation.at, scene.annotation.emoji is not None
        )
        video_label = _annotation_filters(
            graph, video_label, annotation_index, scene.annotation.lines, scene.annotation.emoji,
            annotation_start, annotation_end,
        )
        annotation_index += 1

    graph.append(f"{video_label}trim=duration={narration_seconds:.3f},format=yuv420p[video]")
    audio_input = len(scenes) + 1
    graph.append(f"[{audio_input}:a]aresample=48000,apad,atrim=duration={narration_seconds:.3f}[audio]")

    output = job_dir / f"{_slug(script.title)}.mp4"
    command = [
        str(FFMPEG), "-y", *inputs, "-i", str(narration), "-filter_complex", ";".join(graph),
        "-map", "[video]", "-map", "[audio]", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", str(output),
    ]
    _run(command)
    (job_dir / "metadata.json").write_text(
        json.dumps({
            "title": script.title,
            "duration_seconds": narration_seconds,
            "background": background.name,
            "layout_modes": modes,
            "transition_directions": directions,
            "status": "complete",
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output


def render(script: Script, background: Path, job_dir: Path, voice: str | None = None) -> Path:
    """Renderiza pelo compositor aprovado de cartões, anotações e sound design.

    O painel prepara a narração com ``TTSNeuralEngine`` para conservar a voz
    escolhida. A composição final é delegada ao mesmo motor que gerou a
    preview aprovada: clipes pré-renderizados, transições dos cartões, CTA
    digitada, trilha com sidechain e todos os SFX declarados no roteiro.
    """
    if not background.is_file():
        raise FileNotFoundError(f"Imagem de fundo não encontrada: {background.name}")

    scenes = [scene for block in script.blocks for scene in block.scenes]
    missing = [scene.image for scene in scenes if not (IMAGE_DIR / scene.image).is_file()]
    if missing:
        raise FileNotFoundError("Imagens de cena ausentes: " + ", ".join(missing))

    compositor = ROOT / "scripts" / "legado" / "renderizar_animais_com_transicoes.py"
    if not compositor.is_file():
        raise FileNotFoundError("Compositor horizontal aprovado não encontrado.")

    job_dir.mkdir(parents=True, exist_ok=True)
    narration = job_dir / "narracao.mp3"
    narration_text = " ".join(block.text.strip() for block in script.blocks)
    TTSNeuralEngine().synthesize_sync(narration_text, script.language, narration, voice)

    approved_script = job_dir / "roteiro_painel.json"
    approved_script.write_text(
        json.dumps(script.model_dump(by_alias=True), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    command = [
        sys.executable,
        str(compositor),
        "--roteiro",
        str(approved_script),
        "--saida",
        str(job_dir),
        "--fundo",
        str(background.resolve()),
        "--manter-temporarios",
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        detail = (result.stderr or result.stdout or "erro desconhecido do compositor aprovado").strip()
        raise RuntimeError(f"Compositor horizontal não conseguiu renderizar o vídeo:\n{detail[-1800:]}")

    output = job_dir / "roteiro_painel_final.mp4"
    if not output.is_file():
        raise RuntimeError("O compositor terminou sem criar o MP4 final esperado.")

    (job_dir / "metadata.json").write_text(
        json.dumps({
            "title": script.title,
            "duration_seconds": _duration(output),
            "background": background.name,
            "layout_modes": _layout_modes(scenes),
            "transition_directions": _transition_directions(scenes),
            "renderer": "approved_card_sound_compositor",
            "status": "complete",
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output
