"""Renderização documental estável para o roteiro de animais.

Por padrão, remove os artefatos temporários depois de criar o vídeo final.
Use ``--animacao-fundo movimento_sutil`` para dar movimento ao fundo, ou
``--manter-temporarios`` somente quando for preciso inspecionar a renderização.
"""

from __future__ import annotations

import math
import json
import argparse
import hashlib
import random
import re
import shutil
import subprocess
from pathlib import Path

from renderizar_teste_animais import FFMPEG, FFPROBE, IMAGE_ORDER, IMAGES, MUSIC, OUTPUT, SCRIPT


ROOT = Path(__file__).resolve().parents[2]
SOUND_DIR = ROOT / "sound"
SOUND_EFFECTS = {
    "whoosh_fast": SOUND_DIR / "fast-whoosh-118248.mp3",
    "whoosh_cinematic": SOUND_DIR / "mixkit-cinematic-wind-swoosh-1471.wav",
    "whoosh_soft": SOUND_DIR / "whoosh sound effect.mp3",
    "click": SOUND_DIR / "Mouse Click.mp3",
    "wrong_answer": SOUND_DIR / "Wrong Answer.mp3",
    "camera_shutter": SOUND_DIR / "camera-shutter-6305.mp3",
    "cash_register": SOUND_DIR / "Cash Register sounds effects No Copyright free use.mp3",
    "crumpled_paper": SOUND_DIR / "crumpled paper sound fx.mp3",
    "new_idea": SOUND_DIR / "Mountain Audio - New Idea Notification.mp3",
    "boxing_bell": SOUND_DIR / "NO COPYRIGHT BOXING BELL SOUND EFFECT.mp3",
    "paper_flip": SOUND_DIR / "Paper Flip Sound Effect.mp3",
    "shutter_click": SOUND_DIR / "Shutter Click sound effect (no copyright ).mp3",
    "bottle_cork": SOUND_DIR / "bottle cork.mp3",
    "celebration": SOUND_DIR / "kids yeyy.mp3",
    "writing": SOUND_DIR / "Writing Sound Effect 1.mp3",
    "typing": SOUND_DIR / "keyboard-typing-5997.mp3",
}
SOUND_VOLUMES = {"typing": 0.58, "click": 0.42, "bottle_cork": 0.48, "new_idea": 0.46, "whoosh_fast": 0.48, "whoosh_cinematic": 0.44, "whoosh_soft": 0.42}
SOUND_CLIP_SECONDS = {"typing": 1.15, "click": 0.18, "bottle_cork": 0.75, "new_idea": 0.75, "whoosh_fast": 0.55, "whoosh_cinematic": 0.75, "whoosh_soft": 0.55}
# Alguns arquivos de efeito possuem silêncio gravado antes do transiente. Ele
# não deve atrasar o símbolo da CTA: o efeito audível começa no mesmo quadro.
SOUND_LEAD_TRIM = {"bottle_cork": 0.272, "new_idea": 0.024}
BACKGROUND = ROOT / "fundos/Wireframe_grid_on_black_background_202607190011.jpeg"
ANNOTATION_FONT = Path(r"C:/Windows/Fonts/impact.ttf")
EMOJI_FONT = Path(r"C:/Windows/Fonts/seguiemj.ttf")
ANNOTATION_TYPING_DELAY = 0.25
ANNOTATION_TYPING_STEP = 0.045
ANNOTATION_LINE_GAP = 0.08
# Mantém a nota editorial um pouco mais tempo após a última letra, sem
# aproximá-la da duração longa das CTAs.
ANNOTATION_POST_TYPING_HOLD = 1.45
# CTAs são lidas por mais tempo, mas escrevem ligeiramente mais rápido.
CTA_TYPING_STEP = 0.035
CTA_POST_TYPING_HOLD = 4.2
FPS = 24
# A duração precisa ocupar um número inteiro de quadros. Com 0,40 s a 24 fps
# o xfade recebia 9,6 quadros, alternando a cadência no fim do zoom.
TRANSITION_FRAMES = 10
TRANSITION_DURATION = TRANSITION_FRAMES / FPS
# As cenas fullscreen já nascem a 24 fps. Compor a 30 fps duplicava quadros
# do Ken Burns e deixava o zoom com microtravadas.
BACKGROUND_FPS = FPS
CARD_WIDTH = 1500
CARD_HEIGHT = 844
FULLSCREEN_RATIO = 0.40
MAX_FULLSCREEN_RUN = 2
MAX_CARD_RUN = 3
FULLSCREEN_FOCUS_POINTS = (
    (0.22, 0.28, 72),
    (0.54, 0.45, 54),
    (0.68, 0.35, 65),
    (0.30, 0.45, 68),
    (0.62, 0.42, 63),
    (0.49, 0.30, 50),
    (0.30, 0.50, 68),
    (0.55, 0.50, 72),
)
BACKGROUND_ANIMATIONS = ("none", "movimento_sutil", "movimento_lateral", "pulsacao")


def background_animation_filter(animation: str) -> str:
    """Aplica deslocamento interpolado: sem os saltos de um crop por pixel."""
    if animation == "none":
        return "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080"
    if animation == "movimento_sutil":
        # perspective com interpolação cúbica aceita coordenadas fracionárias.
        # A oscilação percorre a grade devagar e sem reamostrar por degraus.
        return (
            "scale=2048:1152:force_original_aspect_ratio=increase,crop=2048:1152,"
            "perspective="
            f"x0='64+14*sin(0.20*on/{BACKGROUND_FPS})':y0='36+8*cos(0.17*on/{BACKGROUND_FPS})':"
            f"x1='1984+14*sin(0.20*on/{BACKGROUND_FPS})':y1='36+8*cos(0.17*on/{BACKGROUND_FPS})':"
            f"x2='64+14*sin(0.20*on/{BACKGROUND_FPS})':y2='1116+8*cos(0.17*on/{BACKGROUND_FPS})':"
            f"x3='1984+14*sin(0.20*on/{BACKGROUND_FPS})':y3='1116+8*cos(0.17*on/{BACKGROUND_FPS})':"
            "interpolation=cubic:eval=frame,"
            "crop=1920:1080"
        )
    if animation == "movimento_lateral":
        return "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080"
    if animation == "pulsacao":
        return "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080"
    choices = ", ".join(BACKGROUND_ANIMATIONS)
    raise ValueError(f"Animação de fundo inválida: {animation}. Use: {choices}.")


def background_light_filter(animation: str) -> str:
    """Anima luz e cor ao longo do tempo, sem mover nem reamostrar a grade."""
    if animation == "none":
        return "eq=contrast=1.07:brightness=-0.035:saturation=0.76"
    if animation == "movimento_sutil":
        return (
            "eq=contrast='1.075+0.008*sin(0.18*t)':"
            "brightness='-0.035+0.008*sin(0.42*t)':"
            "saturation='0.76+0.018*sin(0.17*t)':eval=frame"
        )
    if animation == "movimento_lateral":
        return (
            "eq=contrast='1.07+0.010*sin(0.24*t)':"
            "brightness='-0.035+0.010*sin(0.31*t+0.8)':"
            "saturation='0.77+0.020*sin(0.20*t)':eval=frame"
        )
    if animation == "pulsacao":
        return (
            "eq=contrast='1.075+0.015*sin(0.55*t)':"
            "brightness='-0.035+0.012*sin(0.55*t)':"
            "saturation='0.76+0.025*sin(0.55*t)':eval=frame"
        )
    choices = ", ".join(BACKGROUND_ANIMATIONS)
    raise ValueError(f"Animação de fundo inválida: {animation}. Use: {choices}.")


def resolve_music(music_name: str | None) -> Path:
    """Aceita somente um arquivo do catálogo local de músicas."""
    if not music_name:
        return MUSIC
    if Path(music_name).name != music_name:
        raise ValueError("Informe somente o nome do arquivo de música dentro da pasta music.")
    music = MUSIC.parent / music_name
    if not music.is_file():
        raise FileNotFoundError(f"Música não encontrada: {music}")
    return music


def music_output_path(music: Path, custom_music: bool, output_dir: Path, output_stem: str) -> Path:
    if not custom_music:
        return output_dir / f"{output_stem}_final.mp4"
    slug = re.sub(r"[^a-z0-9]+", "_", music.stem.lower()).strip("_")
    return output_dir / f"{output_stem}_{slug}.mp4"


def effect_ids(value: object, field: str) -> list[str]:
    """Normaliza IDs explícitos sem inventar nenhuma cadência automática."""
    if value is None or value == "none":
        return []
    if isinstance(value, str):
        if value == "auto":
            raise ValueError(f"{field} não aceita 'auto'; escolha efeitos explicitamente no JSON.")
        ids = [value]
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        ids = value
    else:
        raise ValueError(f"{field} deve ser uma lista de IDs de efeitos.")
    invalid = [effect for effect in ids if effect not in SOUND_EFFECTS]
    if invalid:
        raise ValueError(f"{field} contém efeito(s) inválido(s): {', '.join(invalid)}.")
    return ids


def moment_in_scene(start: float, duration: float, moment: str) -> float:
    if moment == "start":
        return start
    if moment == "middle":
        return start + duration / 2
    if moment == "end":
        return start + max(0.0, duration - 0.18)
    raise ValueError("O momento de um efeito deve ser start, middle ou end.")


def annotation_timing(lines: list[str], emoji: str | None = None) -> tuple[float, float]:
    """Retorna passo de digitação e tempo de leitura adequados ao tipo de nota."""
    if emoji in {"👍", "🔔"}:
        return CTA_TYPING_STEP, CTA_POST_TYPING_HOLD
    return ANNOTATION_TYPING_STEP, ANNOTATION_POST_TYPING_HOLD


def annotation_duration(lines: list[str], emoji: str | None = None) -> float:
    """Tempo de escrita mais uma breve janela de leitura após a última letra."""
    typing_step, post_typing_hold = annotation_timing(lines, emoji)
    return (
        ANNOTATION_TYPING_DELAY
        + sum(len(line) for line in lines) * typing_step
        + max(0, len(lines) - 1) * ANNOTATION_LINE_GAP
        + post_typing_hold
    )


def annotation_emoji_time(annotation_start: float, lines: list[str]) -> float:
    """Instante em que o emoji entra, após a última letra ser digitada."""
    return (
        annotation_start
        + ANNOTATION_TYPING_DELAY
        + sum(len(line) for line in lines) * CTA_TYPING_STEP
        + len(lines) * ANNOTATION_LINE_GAP
    )


def annotation_window(start: float, duration: float, moment: str, lines: list[str], emoji: str | None = None) -> tuple[float, float]:
    display_duration = annotation_duration(lines, emoji)
    event_start = moment_in_scene(start, duration, moment)
    if moment == "middle":
        event_start -= display_duration / 2
    elif moment == "end":
        event_start -= display_duration
    event_start = max(start, event_start)
    return event_start, event_start + display_duration


def escape_filter_path(path: Path) -> str:
    """Escapa um caminho Windows para uma opção de filtro do FFmpeg."""
    return path.as_posix().replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def escape_drawtext(text: str) -> str:
    return text.replace("\\", r"\\").replace("'", r"\'").replace(":", r"\:").replace("%", r"\%")


def typing_annotation_filters(
    filters: list[str],
    source: str,
    annotation_index: int,
    lines: list[str],
    annotation_start: float,
    annotation_end: float,
    emoji: str | None = None,
) -> str:
    """Digita um único estado de texto por vez, sem sobreposições."""
    current = source
    cursor = annotation_start + ANNOTATION_TYPING_DELAY
    typing_step, _ = annotation_timing(lines, emoji)
    line_centers = ("h/2-52", "h/2+52") if len(lines) == 2 else ("h/2",)
    for line_index, (line, y_center) in enumerate(zip(lines, line_centers, strict=True)):
        for char_count in range(1, len(line) + 1):
            next_cursor = cursor + typing_step
            output = f"[annotation_{annotation_index}_typed_{line_index}_{char_count}]"
            filters.append(
                f"{current}drawtext=fontfile='{escape_filter_path(ANNOTATION_FONT)}':"
                f"text='{escape_drawtext(line[:char_count])}':fontcolor=0xFFD429:fontsize=102:"
                "borderw=5:bordercolor=black@0.96:"
                f"x=(w-text_w)/2:y={y_center}-text_h/2:"
                f"enable='between(t,{cursor:.3f},{next_cursor:.3f})'{output}"
            )
            current = output
            cursor = next_cursor
        output = f"[annotation_{annotation_index}_line_{line_index}]"
        filters.append(
            f"{current}drawtext=fontfile='{escape_filter_path(ANNOTATION_FONT)}':"
            f"text='{escape_drawtext(line)}':fontcolor=0xFFD429:fontsize=102:"
            "borderw=5:bordercolor=black@0.96:"
            f"x=(w-text_w)/2:y={y_center}-text_h/2:enable='between(t,{cursor:.3f},{annotation_end:.3f})'{output}"
        )
        current = output
        cursor += ANNOTATION_LINE_GAP
    if emoji:
        output = f"[annotation_{annotation_index}_emoji]"
        emoji_y = "h/2-52-text_h/2" if len(lines) == 2 else "h/2-text_h/2"
        filters.append(
            f"{current}drawtext=fontfile='{escape_filter_path(EMOJI_FONT)}':"
            f"text='{escape_drawtext(emoji)}':fontcolor=white:fontsize=82:"
            "borderw=2:bordercolor=black@0.92:"
            f"x='w/2+300':y='{emoji_y}':enable='between(t,{cursor:.3f},{annotation_end:.3f})'{output}"
        )
        current = output
    return current


def execute(command: list[str]) -> None:
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:])


def media_duration(path: Path) -> float:
    result = subprocess.run(
        [str(FFPROBE), "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        check=True, text=True, capture_output=True,
    )
    return float(result.stdout.strip())


def scene_filter(frames: int) -> str:
    """Cria o cartão com uma borda leve; o movimento é aplicado à composição."""
    return (
        "[0:v]"
        f"scale={CARD_WIDTH}:{CARD_HEIGHT}:force_original_aspect_ratio=increase,crop={CARD_WIDTH}:{CARD_HEIGHT},"
        f"fps={FPS},setsar=1,fade=t=in:st=0:d=0.16,"
        "drawbox=x=3:y=3:w=iw-6:h=ih-6:color=white@0.18:t=3[out]"
    )


def stable_seed(title: str) -> int:
    return int.from_bytes(hashlib.sha256(title.encode("utf-8")).digest()[:8], "big")


def layout_modes(scene_count: int, title: str) -> list[str]:
    """Intercala cartão e tela cheia sem cadência previsível, perto de 40/60."""
    if scene_count == 1:
        return ["fullscreen"]
    target_fullscreen = max(1, round(scene_count * FULLSCREEN_RATIO))
    rng = random.Random(stable_seed(title))
    modes: list[str] = []
    fullscreen_count = 0
    run_mode: str | None = None
    run_length = 0
    for index in range(scene_count):
        remaining_slots = scene_count - index
        remaining_fullscreen = target_fullscreen - fullscreen_count
        candidates = ["fullscreen", "card"]
        if remaining_fullscreen == 0:
            candidates = ["card"]
        elif remaining_fullscreen == remaining_slots:
            candidates = ["fullscreen"]
        if run_mode == "fullscreen" and run_length >= MAX_FULLSCREEN_RUN and "card" in candidates:
            candidates = ["card"]
        if run_mode == "card" and run_length >= MAX_CARD_RUN and "fullscreen" in candidates:
            candidates = ["fullscreen"]
        if len(candidates) == 1:
            mode = candidates[0]
        else:
            score = remaining_fullscreen / remaining_slots + rng.uniform(-0.24, 0.24)
            if run_mode == "fullscreen":
                score -= 0.20
            elif run_mode == "card":
                score += 0.14
            mode = "fullscreen" if score >= 0.50 else "card"
        modes.append(mode)
        fullscreen_count += mode == "fullscreen"
        run_length = run_length + 1 if mode == run_mode else 1
        run_mode = mode
    return modes


def transition_exits(scene_count: int, title: str) -> list[str]:
    """Varia ida e volta sem deixar uma única direção dominar a narrativa."""
    rng = random.Random(stable_seed(title) ^ 0x5A17)
    exits: list[str] = []
    last: str | None = None
    run_length = 0
    for _ in range(max(0, scene_count - 1)):
        if run_length >= 2:
            direction = "right" if last == "left" else "left"
        elif last is None:
            direction = "left" if rng.random() < 0.5 else "right"
        else:
            direction = ("right" if last == "left" else "left") if rng.random() < 0.66 else last
        run_length = run_length + 1 if direction == last else 1
        last = direction
        exits.append("to_right" if direction == "right" else "to_left")
    return exits + [exits[-1] if exits else "to_left"]


def layout_modes_from_script(scene_specs: list[dict]) -> list[str]:
    """Usa o layout declarado no JSON, sem alterar a sequência aprovada."""
    modes: list[str] = []
    for index, scene in enumerate(scene_specs, start=1):
        transition = scene.get("transition")
        if not isinstance(transition, dict):
            raise ValueError(f"cena {index}.transition deve ser um objeto.")
        entering = transition.get("in")
        if entering not in {"zoom_in", "from_left", "from_right", "none"}:
            raise ValueError(f"cena {index}.transition.in é inválido: {entering!r}.")
        modes.append("fullscreen" if entering == "zoom_in" else "card")
    return modes


def transition_exits_from_script(scene_specs: list[dict]) -> list[str]:
    """Mantém a direção de saída escolhida no roteiro para cada cena."""
    exits: list[str] = []
    for index, scene in enumerate(scene_specs, start=1):
        transition = scene.get("transition")
        exiting = transition.get("out") if isinstance(transition, dict) else None
        if exiting not in {"to_left", "to_right", "none"}:
            raise ValueError(f"cena {index}.transition.out é inválido: {exiting!r}.")
        exits.append(exiting)
    return exits


def fullscreen_scene_filter(frames: int, focus_index: int) -> str:
    """Ken Burns subpixel: a janela se move sem arredondar crop/scale por quadro."""
    focus_x, focus_y, zoom = FULLSCREEN_FOCUS_POINTS[focus_index % len(FULLSCREEN_FOCUS_POINTS)]
    # O `scale` de saída tem dimensões inteiras e, sozinho, repete ou pula
    # pixels. A perspectiva amostra uma janela fracionária a cada quadro com
    # interpolação cúbica; o resize final continua fixo em 1920x1080.
    progress = f"(on/{max(1, frames - 1)})"
    viewport_w = f"(2000*2304/(2304+{zoom}*{progress}))"
    viewport_h = f"(({viewport_w})*0.5625)"
    viewport_x = f"(2400-({viewport_w}))*(0.50+({focus_x:.2f}-0.50)*{progress})"
    viewport_y = f"(1350-({viewport_h}))*(0.50+({focus_y:.2f}-0.50)*{progress})"
    return (
        "[0:v]"
        "scale=2400:1350:force_original_aspect_ratio=increase,crop=2400:1350,"
        f"perspective=x0='{viewport_x}':y0='{viewport_y}':"
        f"x1='({viewport_x})+({viewport_w})':y1='{viewport_y}':"
        f"x2='{viewport_x}':y2='({viewport_y})+({viewport_h})':"
        f"x3='({viewport_x})+({viewport_w})':y3='({viewport_y})+({viewport_h})':"
        "sense=source:interpolation=cubic:eval=frame,"
        f"scale=1920:1080:flags=lanczos,fps={FPS},setsar=1[out]"
    )


def clean_temporary_artifacts(clips: Path, voice: Path, filter_script: Path, output_dir: Path) -> None:
    """Remove somente arquivos de trabalho conhecidos, mantendo o MP4 final."""
    if clips.is_dir():
        shutil.rmtree(clips)
    for artifact in (voice, filter_script, output_dir / "imagens.ffconcat", output_dir / "video_sem_audio.mp4"):
        if artifact.is_file():
            artifact.unlink()


def create_narration_if_needed(voice: Path, payload: dict) -> None:
    """Gera novamente a narração quando a limpeza da renderização a removeu."""
    if voice.is_file():
        return
    narration = " ".join(block["text"] for block in payload["blocks"])
    if not narration.strip():
        raise ValueError("O roteiro não contém texto para gerar a narração.")
    execute([
        "edge-tts", "--voice", "pt-BR-AntonioNeural", "--rate=-10%",
        "--text", narration, "--write-media", str(voice),
    ])


def main(
    use_vintage_effect: bool = False,
    keep_intermediates: bool = False,
    background_animation: str | None = None,
    music_name: str | None = None,
    script_path: Path | None = None,
    output_dir_override: Path | None = None,
    background_path: Path | None = None,
) -> None:
    selected_music = resolve_music(music_name)
    selected_script = (script_path or SCRIPT).resolve()
    if not selected_script.is_file():
        raise FileNotFoundError(f"Roteiro não encontrado: {selected_script}")
    payload = json.loads(selected_script.read_text(encoding="utf-8"))
    scene_specs = [scene for block in payload["blocks"] for scene in block["scenes"]]
    if not scene_specs:
        raise ValueError("O roteiro não contém cenas.")
    image_order = IMAGE_ORDER if script_path is None else [scene["image"] for scene in scene_specs]
    output_stem = "animais_fundo_do_mar" if script_path is None else re.sub(r"[^a-z0-9]+", "_", selected_script.stem.lower()).strip("_")
    output_dir = output_dir_override.resolve() if output_dir_override is not None else (OUTPUT if script_path is None else OUTPUT.parent / output_stem)
    output_dir.mkdir(parents=True, exist_ok=True)
    voice = output_dir / "narracao.mp3"
    final = music_output_path(selected_music, custom_music=music_name is not None, output_dir=output_dir, output_stem=output_stem)
    clips = output_dir / "cenas_com_movimento"
    selected_background = (background_path or BACKGROUND).resolve()
    if not selected_background.is_file():
        raise FileNotFoundError(f"Fundo ausente para a renderização: {selected_background}")
    if not ANNOTATION_FONT.is_file():
        raise FileNotFoundError(f"Fonte de anotação ausente: {ANNOTATION_FONT}")
    if not EMOJI_FONT.is_file():
        raise FileNotFoundError(f"Fonte de emoji ausente: {EMOJI_FONT}")
    clips.mkdir(parents=True, exist_ok=True)

    # A opção explícita da linha de comando permite testar alternativas sem
    # editar o roteiro. Na ausência dela, usa-se a escolha salva no JSON.
    selected_background_animation = background_animation or payload.get("background_animation", "movimento_sutil")
    background_filter = background_animation_filter(selected_background_animation)
    background_light = background_light_filter(selected_background_animation)
    if len(scene_specs) != len(image_order):
        raise ValueError("O roteiro e a lista de imagens precisam ter a mesma quantidade de cenas.")
    missing_images = [name for name in image_order if not (IMAGES / name).is_file()]
    if missing_images:
        raise FileNotFoundError("Imagens-fonte ausentes:\n" + "\n".join(missing_images))
    # O roteiro decide quais cenas são fullscreen e em que direção cada uma
    # sai. Assim, o render final preserva a dinâmica aprovada no preview.
    modes = layout_modes_from_script(scene_specs)
    exits = transition_exits_from_script(scene_specs)
    create_narration_if_needed(voice, payload)

    # Cada cena absorve a sobreposição da transição, mantendo o final alinhado à narração.
    narration_duration = media_duration(voice)
    target_scene_duration = (narration_duration + TRANSITION_DURATION * (len(image_order) - 1)) / len(image_order)
    frames = math.ceil(target_scene_duration * FPS)
    scene_duration = frames / FPS
    scene_interval = scene_duration - TRANSITION_DURATION

    # Todos os efeitos são declarados por intenção no JSON. Não existe fallback
    # por índice, porcentagem ou repetição previsível.
    sound_events: list[tuple[str, float]] = []
    annotations: list[tuple[list[str], float, float, str | None]] = []
    for index, scene in enumerate(scene_specs):
        scene_start = index * scene_interval
        sounds = scene.get("sounds", {})
        if not isinstance(sounds, dict):
            raise ValueError(f"{scene.get('id', f'cena {index + 1}')}.sounds deve ser um objeto.")
        if index < len(scene_specs) - 1:
            for effect in effect_ids(sounds.get("transition", []), f"{scene.get('id', index)}.sounds.transition"):
                sound_events.append((effect, (index + 1) * scene_interval))
        context = sounds.get("context")
        if context is not None:
            if not isinstance(context, dict):
                raise ValueError(f"{scene.get('id', index)}.sounds.context deve ser um objeto.")
            context_effects = effect_ids(context.get("type"), f"{scene.get('id', index)}.sounds.context.type")
            if len(context_effects) != 1:
                raise ValueError("sounds.context.type deve identificar exatamente um efeito.")
            sound_events.append((context_effects[0], moment_in_scene(scene_start, scene_duration, context.get("at", "middle"))))
        annotation = scene.get("annotation")
        if annotation is not None:
            if not isinstance(annotation, dict):
                raise ValueError(f"{scene.get('id', index)}.annotation deve ser um objeto.")
            lines = annotation.get("lines")
            if not isinstance(lines, list) or not 1 <= len(lines) <= 2 or not all(isinstance(line, str) and line.strip() for line in lines):
                raise ValueError(f"{scene.get('id', index)}.annotation.lines deve ter uma ou duas linhas de texto.")
            if any(len(line.strip()) > 32 for line in lines):
                raise ValueError(f"{scene.get('id', index)}.annotation.lines aceita até 32 caracteres por linha.")
            emoji = annotation.get("emoji")
            if emoji is not None and (not isinstance(emoji, str) or not emoji.strip() or len(emoji) > 8):
                raise ValueError(f"{scene.get('id', index)}.annotation.emoji deve ser um emoji curto opcional.")
            annotation_start, annotation_end = annotation_window(
                scene_start, scene_duration, annotation.get("at", "start"), [line.strip() for line in lines], emoji
            )
            annotations.append(([line.strip() for line in lines], annotation_start, annotation_end, emoji))
            # A digitação começa com a primeira letra; isso preserva o clique
            # da CTA no primeiro instante sem os dois sons se encobrirem.
            sound_events.append(("typing", annotation_start + ANNOTATION_TYPING_DELAY))
            # CTAs com emoji mantêm o clique inicial e ganham um reforço
            # específico apenas quando o símbolo correspondente se revela.
            if emoji == "👍":
                sound_events.append(("bottle_cork", annotation_emoji_time(annotation_start, [line.strip() for line in lines])))
            elif emoji == "🔔":
                sound_events.append(("new_idea", annotation_emoji_time(annotation_start, [line.strip() for line in lines])))
    missing_effects = sorted({effect for effect, _ in sound_events if not SOUND_EFFECTS[effect].is_file()})
    if missing_effects:
        raise FileNotFoundError("Efeitos sonoros ausentes: " + ", ".join(missing_effects))
    # A CTA final pode terminar depois da última palavra. Mantemos a imagem
    # final e a trilha até a anotação completar a leitura.
    tail_duration = max(0.0, max((end for _, _, end, _ in annotations), default=narration_duration) - narration_duration)

    rendered_clips: list[Path] = []
    fullscreen_index = 0
    for index, image_name in enumerate(image_order):
        clip = clips / f"cena_{index + 1:02d}.mp4"
        # Cartões preservam o desenho atualizado. A tela cheia usa um zoom
        # contínuo com foco editorial, sem os degraus do zoompan.
        if modes[index] == "fullscreen":
            filter_graph = fullscreen_scene_filter(frames, fullscreen_index)
            fullscreen_index += 1
        else:
            filter_graph = scene_filter(frames)
        execute([
            str(FFMPEG), "-y", "-loop", "1", "-framerate", str(FPS), "-i", str(IMAGES / image_name),
            "-filter_complex", filter_graph, "-map", "[out]", "-an",
            "-frames:v", str(frames), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22", "-pix_fmt", "yuv420p", "-r", str(FPS), str(clip),
        ])
        rendered_clips.append(clip)

    video_inputs: list[str] = []
    for clip in rendered_clips:
        video_inputs.extend(["-i", str(clip)])
    background_index = len(rendered_clips)
    card_indices = [index for index, mode in enumerate(modes) if mode == "card"]
    background_prefix = (
        f"[{background_index}:v]{background_filter},"
        f"format=rgb24,lutrgb=r='min(255,val*5)':g='min(255,val*5)':b='min(255,val*5)',"
        f"{background_light},vignette=PI/4:eval=frame,"
        f"fps={BACKGROUND_FPS},settb=1/{FPS},setsar=1"
    )
    filters: list[str] = []
    background_labels: dict[int, str] = {}
    if card_indices:
        labels = "".join(f"[background_{index}]" for index in card_indices)
        filters.append(f"{background_prefix},split={len(card_indices)}{labels}")
        background_labels = {index: f"[background_{index}]" for index in card_indices}
    else:
        filters.append(f"{background_prefix}[background]")

    # Cada cena vira uma tela completa antes de entrar na transição. O xfade
    # smoothleft é a máscara que revela suavemente a próxima tela no preview
    # de referência; para a ida oposta, ele é espelhado sem mudar os assets.
    scene_labels: list[str] = []
    for index in range(len(rendered_clips)):
        output = f"[scene{index}]"
        is_fullscreen = modes[index] == "fullscreen"
        if is_fullscreen:
            padding = f",tpad=stop_mode=clone:stop_duration={tail_duration:.3f}" if index == len(rendered_clips) - 1 and tail_duration else ""
            filters.append(f"[{index}:v]settb=1/{FPS},setpts=PTS-STARTPTS{padding}{output}")
        else:
            card = f"[card{index}]"
            shadow = f"[shadow{index}]"
            shadow_layer = f"[shadow_layer{index}]"
            centered_x = "(main_w-overlay_w)/2"
            entry = centered_x
            if index and modes[index - 1] == "fullscreen":
                # Ao sair de fullscreen, o cartão acompanha a revelação: vem
                # da direita quando a tela viaja à esquerda e vice-versa.
                if exits[index - 1] == "to_left":
                    entry = f"main_w-(main_w-{centered_x})*t/{TRANSITION_DURATION:.3f}"
                else:
                    entry = f"-overlay_w+({centered_x}+overlay_w)*t/{TRANSITION_DURATION:.3f}"

            # A mesma composição precisa acompanhar a saída cartão -> tela
            # cheia. Antes, ela só animava a entrada no sentido inverso.
            exit_start = scene_duration - TRANSITION_DURATION
            exiting = centered_x
            if index < len(rendered_clips) - 1 and modes[index + 1] == "fullscreen":
                if exits[index] == "to_left":
                    exiting = f"{centered_x}-({centered_x}+overlay_w)*(t-{exit_start:.3f})/{TRANSITION_DURATION:.3f}"
                elif exits[index] == "to_right":
                    exiting = f"{centered_x}+(main_w-{centered_x})*(t-{exit_start:.3f})/{TRANSITION_DURATION:.3f}"
            card_x = (
                f"if(lt(t,{TRANSITION_DURATION:.3f}),{entry},"
                f"if(lt(t,{exit_start:.3f}),{centered_x},{exiting}))"
            )
            filters.append(
                f"[{index}:v]settb=1/{FPS},setpts=PTS-STARTPTS,split=2{card}[shadow_source{index}]"
            )
            filters.append(
                f"[shadow_source{index}]format=rgba,colorchannelmixer=rr=0:gg=0:bb=0:aa=0.42,boxblur=18:2{shadow}"
            )
            filters.append(
                f"{background_labels[index]}trim=duration={scene_duration:.3f},setpts=PTS-STARTPTS[background_trim{index}]"
            )
            filters.append(
                f"[background_trim{index}]{shadow}overlay=x='({card_x})+18':y='(main_h-overlay_h)/2+22':format=auto{shadow_layer}"
            )
            padding = f",tpad=stop_mode=clone:stop_duration={tail_duration:.3f}" if index == len(rendered_clips) - 1 and tail_duration else ""
            filters.append(
                f"{shadow_layer}{card}overlay=x='{card_x}':y='(main_h-overlay_h)/2':format=auto{padding}{output}"
            )
        scene_labels.append(output)

    video_label = scene_labels[0]
    elapsed = scene_duration
    for index in range(1, len(scene_labels)):
        output = "[transitioned_video]" if index == len(scene_labels) - 1 else f"[transition_{index}]"
        offset = elapsed - TRANSITION_DURATION
        previous_mode = modes[index - 1]
        current_mode = modes[index]
        if previous_mode == "card" and current_mode == "card":
            filters.append(
                f"{video_label}{scene_labels[index]}xfade=transition=fade:duration={TRANSITION_DURATION:.3f}:offset={offset:.3f}{output}"
            )
        elif exits[index - 1] == "to_left":
            filters.append(
                f"{video_label}{scene_labels[index]}xfade=transition=smoothleft:duration={TRANSITION_DURATION:.3f}:offset={offset:.3f}{output}"
            )
        elif exits[index - 1] == "to_right":
            filters.extend([
                f"{video_label}hflip[flip_current_{index}]",
                f"{scene_labels[index]}hflip[flip_next_{index}]",
                f"[flip_current_{index}][flip_next_{index}]xfade=transition=smoothleft:duration={TRANSITION_DURATION:.3f}:offset={offset:.3f}[flip_mix_{index}]",
                f"[flip_mix_{index}]hflip{output}",
            ])
        else:
            filters.append(
                f"{video_label}{scene_labels[index]}xfade=transition=fade:duration={TRANSITION_DURATION:.3f}:offset={offset:.3f}{output}"
            )
        video_label = output
        elapsed += scene_duration - TRANSITION_DURATION
    for index, (lines, annotation_start, annotation_end, emoji) in enumerate(annotations):
        base = f"[annotation_base{index}]"
        blur = f"[annotation_blur{index}]"
        blurred = f"[annotation_layer{index}]"
        enabled = f"between(t,{annotation_start:.3f},{annotation_end:.3f})"
        filters.append(f"{video_label}split=2{base}[annotation_source{index}]")
        filters.append(f"[annotation_source{index}]boxblur=22:6:enable='{enabled}'{blur}")
        filters.append(f"{base}{blur}overlay=0:0:enable='{enabled}'{blurred}")
        video_label = typing_annotation_filters(filters, blurred, index, lines, annotation_start, annotation_end, emoji)
    if use_vintage_effect:
        vintage_output = "[video_vintage]"
        # Grão temporal muito leve, sem tremor geométrico ou perda de foco.
        filters.append(f"{video_label}noise=alls=4:allf=t+u,eq=contrast=1.03:saturation=0.90{vintage_output}")
        video_label = vintage_output

    voice_index = background_index + 1
    music_index = voice_index + 1
    effect_input_start = music_index + 1
    events_by_effect: dict[str, list[float]] = {}
    for effect, event_time in sound_events:
        events_by_effect.setdefault(effect, []).append(event_time)
    sound_input_indices = {effect: effect_input_start + index for index, effect in enumerate(events_by_effect)}
    effect_labels: dict[str, list[str]] = {}
    for effect, event_times in events_by_effect.items():
        labels = [f"[{effect}_source_{index}]" for index in range(len(event_times))]
        effect_labels[effect] = labels
        duration = SOUND_CLIP_SECONDS.get(effect, 0.9)
        volume = SOUND_VOLUMES.get(effect, 0.46)
        lead_trim = SOUND_LEAD_TRIM.get(effect, 0.0)
        filters.append(
            f"[{sound_input_indices[effect]}:a]aresample=48000,atrim=start={lead_trim:.3f}:end={lead_trim + duration:.3f},volume={volume:.2f},"
            f"asplit={len(labels)}{''.join(labels)}"
        )
    delayed: list[str] = []
    for effect, event_times in events_by_effect.items():
        for index, event_time in enumerate(event_times):
            delay = round(event_time * 1000)
            label = f"[{effect}_hit_{index}]"
            # Os SFX são estéreo. Sem all=1, o FFmpeg atrasaria apenas um
            # canal e deixaria o outro tocar indevidamente no início.
            filters.append(f"{effect_labels[effect][index]}adelay={delay}:all=1{label}")
            delayed.append(label)
    if delayed:
        filters.append("".join(delayed) + f"amix=inputs={len(delayed)}:normalize=0[sfx_track]")
    else:
        filters.append("anullsrc=r=48000:cl=stereo,atrim=0:0[sfx_track]")
    filters.extend([
        f"[{voice_index}:a]aresample=48000,apad=pad_dur={tail_duration:.3f},asplit=2[voice_mix][voice_key]",
        f"[{music_index}:a]aresample=48000,volume=0.20[music]",
        "[music][voice_key]sidechaincompress=threshold=0.035:ratio=8:attack=20:release=250[ducked]",
        "[voice_mix][ducked][sfx_track]amix=inputs=3:duration=first:normalize=0[audio]",
    ])

    filter_script = output_dir / "filtros_renderizacao.ffscript"
    filter_script.write_text(";".join(filters), encoding="utf-8")
    execute([
        str(FFMPEG), "-y", *video_inputs, "-loop", "1", "-framerate", str(BACKGROUND_FPS), "-i", str(selected_background),
        "-i", str(voice), "-stream_loop", "-1", "-i", str(selected_music),
        *[item for effect in events_by_effect for item in ("-i", str(SOUND_EFFECTS[effect]))],
        "-filter_complex_script", str(filter_script), "-map", video_label, "-map", "[audio]", "-c:v", "libx264",
        "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-r", str(BACKGROUND_FPS), "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", "-shortest", str(final),
    ])
    if not keep_intermediates:
        clean_temporary_artifacts(clips, voice, filter_script, output_dir)
    print(f"Vídeo pronto: {final} (animação do fundo: {selected_background_animation})")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Renderiza o vídeo com zoom de entrada estável.")
    parser.add_argument(
        "--efeito-vintage",
        action="store_true",
        help="adiciona um grão sutil e leve dessaturação com aparência documental antiga",
    )
    parser.add_argument(
        "--manter-temporarios",
        action="store_true",
        help="não remove cenas e narração de trabalho após uma renderização bem-sucedida",
    )
    parser.add_argument(
        "--animacao-fundo",
        choices=BACKGROUND_ANIMATIONS,
        help="movimento contínuo exclusivo da foto de fundo (padrão: valor no roteiro ou movimento_sutil)",
    )
    parser.add_argument(
        "--musica",
        help="nome de um MP3 existente na pasta music; cria um arquivo final separado para essa trilha",
    )
    parser.add_argument(
        "--roteiro",
        type=Path,
        help="caminho de um roteiro JSON; usa os nomes de imagem declarados nele e cria uma pasta de saída isolada",
    )
    parser.add_argument(
        "--saida",
        type=Path,
        help="pasta de saída isolada; útil para previews sem substituir o vídeo final",
    )
    parser.add_argument(
        "--fundo",
        type=Path,
        help="imagem de fundo escolhida pelo painel para esta renderização",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    main(
        use_vintage_effect=args.efeito_vintage,
        keep_intermediates=args.manter_temporarios,
        background_animation=args.animacao_fundo,
        music_name=args.musica,
        script_path=args.roteiro,
        output_dir_override=args.saida,
        background_path=args.fundo,
    )
