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
from dataclasses import dataclass
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
TIMING_TOLERANCE = 1 / FPS
# O compositor nunca deve crescer junto com o vídeo inteiro. Estes limites
# mantêm cada invocação pesada do FFmpeg pequena o bastante para a máquina de
# 16 GiB usada pelo painel, sem limitar a duração total do roteiro.
MAX_SCENES_PER_SEGMENT = 12
MAX_SEGMENT_SECONDS = 90.0
# Proteção adicional do FFmpeg: se um grafo tentar reter frames demais, ele
# falha com uma mensagem diagnóstica antes de esgotar a memória do Windows.
MAX_FILTER_BUFFERED_FRAMES = 128


@dataclass(frozen=True)
class SceneTimingPlan:
    """Cronograma acústico por cena, sempre relativo ao início da narração."""

    starts: list[float]
    speech_durations: list[float]
    clip_durations: list[float]
    visual_locks: list[bool]
    annotation_starts: list[float | None]


@dataclass(frozen=True)
class RenderSegment:
    """Faixa visual independente, com uma cena de guarda na fronteira.

    A cena ``handoff_index`` aparece no fim do segmento anterior durante os
    dez quadros de xfade. O próximo segmento a reprocessa, mas descarta esses
    mesmos quadros iniciais. Assim o concat final não cria lacuna nem duplica
    a transição.
    """

    start_index: int
    end_index: int
    handoff_index: int | None
    trim_start: float
    output_duration: float


def background_animation_filter(animation: str, frame_offset: int = 0) -> str:
    """Aplica deslocamento interpolado: sem os saltos de um crop por pixel."""
    frame = f"(on+{max(0, frame_offset)})"
    if animation == "none":
        return "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080"
    if animation == "movimento_sutil":
        # perspective com interpolação cúbica aceita coordenadas fracionárias.
        # A oscilação percorre a grade devagar e sem reamostrar por degraus.
        return (
            "scale=2048:1152:force_original_aspect_ratio=increase,crop=2048:1152,"
            "perspective="
            f"x0='64+14*sin(0.20*{frame}/{BACKGROUND_FPS})':y0='36+8*cos(0.17*{frame}/{BACKGROUND_FPS})':"
            f"x1='1984+14*sin(0.20*{frame}/{BACKGROUND_FPS})':y1='36+8*cos(0.17*{frame}/{BACKGROUND_FPS})':"
            f"x2='64+14*sin(0.20*{frame}/{BACKGROUND_FPS})':y2='1116+8*cos(0.17*{frame}/{BACKGROUND_FPS})':"
            f"x3='1984+14*sin(0.20*{frame}/{BACKGROUND_FPS})':y3='1116+8*cos(0.17*{frame}/{BACKGROUND_FPS})':"
            "interpolation=cubic:eval=frame,"
            "crop=1920:1080"
        )
    if animation == "movimento_lateral":
        # A translação acontece antes do crop por perspectiva; assim o fundo
        # desliza em coordenadas subpixel, sem travar em saltos de 1 pixel.
        return (
            "scale=2048:1152:force_original_aspect_ratio=increase,crop=2048:1152,"
            "perspective="
            f"x0='64+28*sin(0.13*{frame}/{BACKGROUND_FPS})':y0='36':"
            f"x1='1984+28*sin(0.13*{frame}/{BACKGROUND_FPS})':y1='36':"
            f"x2='64+28*sin(0.13*{frame}/{BACKGROUND_FPS})':y2='1116':"
            f"x3='1984+28*sin(0.13*{frame}/{BACKGROUND_FPS})':y3='1116':"
            "interpolation=cubic:eval=frame,crop=1920:1080"
        )
    if animation == "pulsacao":
        # Fecha e abre a janela de origem em torno do centro, criando um pulso
        # geométrico contínuo — não apenas alteração de brilho.
        return (
            "scale=2048:1152:force_original_aspect_ratio=increase,crop=2048:1152,"
            "perspective="
            f"x0='64+11*sin(0.45*{frame}/{BACKGROUND_FPS})':y0='36+6*sin(0.45*{frame}/{BACKGROUND_FPS})':"
            f"x1='1984-11*sin(0.45*{frame}/{BACKGROUND_FPS})':y1='36+6*sin(0.45*{frame}/{BACKGROUND_FPS})':"
            f"x2='64+11*sin(0.45*{frame}/{BACKGROUND_FPS})':y2='1116-6*sin(0.45*{frame}/{BACKGROUND_FPS})':"
            f"x3='1984-11*sin(0.45*{frame}/{BACKGROUND_FPS})':y3='1116-6*sin(0.45*{frame}/{BACKGROUND_FPS})':"
            "interpolation=cubic:eval=frame,crop=1920:1080"
        )
    choices = ", ".join(BACKGROUND_ANIMATIONS)
    raise ValueError(f"Animação de fundo inválida: {animation}. Use: {choices}.")


def background_light_filter(animation: str, time_offset: float = 0.0) -> str:
    """Anima luz e cor ao longo do tempo, sem mover nem reamostrar a grade."""
    time = f"(t+{max(0.0, time_offset):.6f})"
    if animation == "none":
        return "eq=contrast=1.07:brightness=-0.035:saturation=0.76"
    if animation == "movimento_sutil":
        return (
            f"eq=contrast='1.075+0.008*sin(0.18*{time})':"
            f"brightness='-0.035+0.008*sin(0.42*{time})':"
            f"saturation='0.76+0.018*sin(0.17*{time})':eval=frame"
        )
    if animation == "movimento_lateral":
        return (
            f"eq=contrast='1.07+0.010*sin(0.24*{time})':"
            f"brightness='-0.035+0.010*sin(0.31*{time}+0.8)':"
            f"saturation='0.77+0.020*sin(0.20*{time})':eval=frame"
        )
    if animation == "pulsacao":
        return (
            f"eq=contrast='1.075+0.015*sin(0.55*{time})':"
            f"brightness='-0.035+0.012*sin(0.55*{time})':"
            f"saturation='0.76+0.025*sin(0.55*{time})':eval=frame"
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


def annotation_window(
    start: float,
    duration: float,
    moment: str,
    lines: list[str],
    emoji: str | None = None,
    end_limit: float | None = None,
    scheduled_start: float | None = None,
) -> tuple[float, float]:
    """Calcula a janela da anotação, respeitando uma trava visual quando houver.

    A CTA inicial/intermediária não pode atravessar o primeiro quadro da
    próxima fala. Do contrário, a transição revela outra imagem enquanto o
    pedido de like/inscrição ainda está na tela. Na última cena não há limite:
    a imagem final permanece até a CTA terminar.
    """
    display_duration = annotation_duration(lines, emoji)
    if scheduled_start is not None:
        event_start = scheduled_start
    else:
        event_start = moment_in_scene(start, duration, moment)
        if moment == "middle":
            event_start -= display_duration / 2
        elif moment == "end":
            event_start -= display_duration
    event_start = max(start, event_start)
    event_end = event_start + display_duration
    if end_limit is not None:
        event_end = min(event_end, end_limit)
    return event_start, max(event_start, event_end)


def time_window(start: float, end: float) -> str:
    """Faixa semiaberta: no quadro de troca a anotação já saiu da tela."""
    return f"gte(t,{start:.3f})*lt(t,{end:.3f})"


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
                f"enable='{time_window(cursor, next_cursor)}'{output}"
            )
            current = output
            cursor = next_cursor
        output = f"[annotation_{annotation_index}_line_{line_index}]"
        filters.append(
            f"{current}drawtext=fontfile='{escape_filter_path(ANNOTATION_FONT)}':"
            f"text='{escape_drawtext(line)}':fontcolor=0xFFD429:fontsize=102:"
            "borderw=5:bordercolor=black@0.96:"
            f"x=(w-text_w)/2:y={y_center}-text_h/2:enable='{time_window(cursor, annotation_end)}'{output}"
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
            f"x='w/2+300':y='{emoji_y}':enable='{time_window(cursor, annotation_end)}'{output}"
        )
        current = output
    return current


def execute(command: list[str]) -> None:
    # O progresso normal do FFmpeg pode ter milhares de linhas em um vídeo
    # longo. Não o acumulamos em RAM: guardamos apenas erros, que são o único
    # texto necessário para diagnosticar uma falha deste subprocesso.
    result = subprocess.run(
        [command[0], "-hide_banner", "-loglevel", "error", *command[1:]],
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise RuntimeError((result.stderr or "erro desconhecido do FFmpeg")[-4000:])


def media_duration(path: Path) -> float:
    result = subprocess.run(
        [str(FFPROBE), "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        check=True, text=True, capture_output=True,
    )
    return float(result.stdout.strip())


def _timing_number(entry: dict, field: str, index: int) -> float:
    """Lê um número finito do contrato de timings, sem coerções silenciosas."""
    value = entry.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"timings.scenes[{index}].{field} deve ser um número finito em segundos.")
    return float(value)


def load_scene_timing_plan(
    timings_path: Path,
    scene_specs: list[dict],
    narration_duration: float,
) -> SceneTimingPlan:
    """Carrega o mapa acústico opcional usado para iniciar cada cena na fala.

    Contrato::

        {
          "scene_count": 2,
          "scenes": [
            {"id": "scene_01", "start": 0.0, "duration": 4.2},
            {"id": "scene_02", "start": 4.2, "duration": 5.1}
          ]
        }

    ``scene_count`` é opcional, mas quando fornecido protege contra o uso de um
    mapa de outro roteiro. O início de uma transição é o ``start`` da próxima
    cena; por isso o clipe anterior é estendido somente pelo tempo visual da
    transição, sem deslocar a entrada acústica do próximo bloco.
    """
    if not timings_path.is_file():
        raise FileNotFoundError(f"Arquivo de timings não encontrado: {timings_path}")
    try:
        payload = json.loads(timings_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Arquivo de timings contém JSON inválido: {timings_path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("O arquivo de timings deve ser um objeto JSON com a lista 'scenes'.")

    declared_count = payload.get("scene_count")
    if declared_count is not None:
        if isinstance(declared_count, bool) or not isinstance(declared_count, int):
            raise ValueError("timings.scene_count deve ser um inteiro quando informado.")
        if declared_count != len(scene_specs):
            raise ValueError(
                f"timings.scene_count ({declared_count}) não corresponde às {len(scene_specs)} cenas do roteiro."
            )

    entries = payload.get("scenes")
    if not isinstance(entries, list):
        raise ValueError("O arquivo de timings deve conter uma lista 'scenes'.")
    if len(entries) != len(scene_specs):
        raise ValueError(
            f"O arquivo de timings possui {len(entries)} cenas, mas o roteiro possui {len(scene_specs)}."
        )

    starts: list[float] = []
    speech_durations: list[float] = []
    visual_locks: list[bool] = []
    annotation_starts: list[float | None] = []
    for index, (entry, scene) in enumerate(zip(entries, scene_specs, strict=True)):
        if not isinstance(entry, dict):
            raise ValueError(f"timings.scenes[{index}] deve ser um objeto.")
        expected_id = scene.get("id")
        if entry.get("id") != expected_id:
            raise ValueError(
                f"timings.scenes[{index}].id deve ser {expected_id!r}, recebido {entry.get('id')!r}."
            )
        start = _timing_number(entry, "start", index)
        duration = _timing_number(entry, "duration", index)
        if start < 0:
            raise ValueError(f"timings.scenes[{index}].start não pode ser negativo.")
        if duration <= 0:
            raise ValueError(f"timings.scenes[{index}].duration deve ser maior que zero.")
        lock_visual = entry.get("lock_visual", False)
        if not isinstance(lock_visual, bool):
            raise ValueError(f"timings.scenes[{index}].lock_visual deve ser booleano quando informado.")
        annotation_start = entry.get("annotation_start")
        if annotation_start is not None:
            if isinstance(annotation_start, bool) or not isinstance(annotation_start, (int, float)) or not math.isfinite(float(annotation_start)):
                raise ValueError(f"timings.scenes[{index}].annotation_start deve ser um número finito quando informado.")
            annotation_start = float(annotation_start)
            if annotation_start < start - TIMING_TOLERANCE or annotation_start > start + duration + TIMING_TOLERANCE:
                raise ValueError(f"timings.scenes[{index}].annotation_start deve ficar dentro da fala da cena.")
        starts.append(start)
        speech_durations.append(duration)
        visual_locks.append(lock_visual)
        annotation_starts.append(annotation_start)

    if starts and starts[0] > TIMING_TOLERANCE:
        raise ValueError(
            "timings.scenes[0].start deve ser 0 (com tolerância de um quadro) para alinhar a primeira cena à narração."
        )
    for index in range(1, len(starts)):
        previous_start = starts[index - 1]
        previous_end = previous_start + speech_durations[index - 1]
        if starts[index] <= previous_start + TIMING_TOLERANCE:
            raise ValueError("Os starts do arquivo de timings devem ser estritamente crescentes por cena.")
        if previous_end > starts[index] + TIMING_TOLERANCE:
            raise ValueError(
                f"timings.scenes[{index - 1}] sobrepõe timings.scenes[{index}]; uma cena não pode começar antes da fala anterior terminar."
            )

    final_end = starts[-1] + speech_durations[-1]
    if final_end > narration_duration + TIMING_TOLERANCE:
        raise ValueError(
            f"O último timing termina em {final_end:.3f}s, depois da narração ({narration_duration:.3f}s)."
        )

    clip_durations = [
        starts[index + 1] - starts[index] + TRANSITION_DURATION
        for index in range(len(starts) - 1)
    ]
    clip_durations.append(narration_duration - starts[-1])
    if any(duration <= TIMING_TOLERANCE for duration in clip_durations):
        raise ValueError("Os timings deixam uma cena curta demais para a composição em 24 fps.")

    return SceneTimingPlan(starts, speech_durations, clip_durations, visual_locks, annotation_starts)


def _frames_for_duration(seconds: float) -> int:
    """Arredonda para cima sem criar um quadro extra por erro de ponto flutuante."""
    return max(1, math.ceil(seconds * FPS - 1e-9))


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
    for directory in (clips, output_dir / "cenas_prontas", output_dir / "segmentos"):
        if directory.is_dir():
            shutil.rmtree(directory)
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
    selected_voice = payload.get("voice", "pt-BR-AntonioNeural")
    if not isinstance(selected_voice, str) or not selected_voice.strip():
        raise ValueError("O roteiro deve declarar uma voz neural válida em 'voice'.")
    execute([
        "edge-tts", "--voice", selected_voice.strip(), "--rate=-10%",
        "--text", narration, "--write-media", str(voice),
    ])


def build_render_segments(
    scene_starts: list[float],
    narration_duration: float,
    tail_duration: float,
) -> list[RenderSegment]:
    """Divide a timeline em fragmentos de custo fixo.

    O fragmento que termina antes de uma cena ``k`` inclui ``k`` como guarda
    até o fim do xfade. O fragmento seguinte começa em ``k`` mas descarta os
    mesmos dez frames. O resultado concatenado conserva cada transição uma
    única vez, sem manter o vídeo inteiro no mesmo filter graph.
    """
    if not scene_starts:
        raise ValueError("O roteiro não contém cenas para segmentar.")

    total_duration = narration_duration + tail_duration
    segments: list[RenderSegment] = []
    start_index = 0
    while start_index < len(scene_starts):
        end_index = start_index
        while end_index + 1 < len(scene_starts):
            candidate = end_index + 1
            count = candidate - start_index + 1
            elapsed = scene_starts[candidate] - scene_starts[start_index]
            if count > MAX_SCENES_PER_SEGMENT or elapsed > MAX_SEGMENT_SECONDS:
                break
            end_index = candidate

        handoff_index = end_index + 1 if end_index + 1 < len(scene_starts) else None
        trim_start = 0.0 if start_index == 0 else TRANSITION_DURATION
        if handoff_index is None:
            relative_end = total_duration - scene_starts[start_index]
        else:
            relative_end = (
                scene_starts[handoff_index]
                - scene_starts[start_index]
                + TRANSITION_DURATION
            )
        output_duration = relative_end - trim_start
        if output_duration <= TIMING_TOLERANCE:
            raise ValueError("A segmentação visual gerou um fragmento sem duração útil.")
        segments.append(RenderSegment(
            start_index=start_index,
            end_index=end_index,
            handoff_index=handoff_index,
            trim_start=trim_start,
            output_duration=output_duration,
        ))
        start_index = handoff_index if handoff_index is not None else len(scene_starts)
    return segments


def _scene_canvas_path(canvas_dir: Path, index: int) -> Path:
    return canvas_dir / f"cena_{index + 1:03d}.mp4"


def render_scene_canvas(
    index: int,
    rendered_clips: list[Path],
    canvas_dir: Path,
    background: Path,
    background_animation: str,
    modes: list[str],
    exits: list[str],
    scene_starts: list[float],
    transition_starts: list[float],
    clip_durations: list[float],
    tail_duration: float,
) -> Path:
    """Materializa uma cena 1920x1080 antes de entrar no xfade segmentado.

    Cartões recebem fundo, sombra e movimentos neste passo isolado. Assim o
    grafo de cada fragmento trabalha apenas com telas completas e não precisa
    dividir uma única imagem de fundo para todas as cenas do roteiro.
    """
    source = rendered_clips[index]
    scene_tail = tail_duration if index == len(rendered_clips) - 1 else 0.0
    if modes[index] == "fullscreen" and not scene_tail:
        return source

    canvas_dir.mkdir(parents=True, exist_ok=True)
    output = _scene_canvas_path(canvas_dir, index)
    if output.is_file():
        return output

    duration = clip_durations[index] + scene_tail
    frames = _frames_for_duration(duration)
    padding = f",tpad=stop_mode=clone:stop_duration={scene_tail:.6f}" if scene_tail else ""
    if modes[index] == "fullscreen":
        filter_graph = (
            f"[0:v]settb=1/{FPS},setpts=PTS-STARTPTS{padding},"
            f"trim=duration={duration:.6f},format=yuv420p[out]"
        )
        execute([
            str(FFMPEG), "-y", "-i", str(source),
            "-filter_complex_threads", "1", "-filter_threads", "1",
            "-filter_complex", filter_graph, "-map", "[out]", "-an",
            "-frames:v", str(frames), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
            "-pix_fmt", "yuv420p", "-r", str(FPS), str(output),
        ])
        return output

    centered_x = "(main_w-overlay_w)/2"
    entry = centered_x
    if index and modes[index - 1] == "fullscreen":
        if exits[index - 1] == "to_left":
            entry = f"main_w-(main_w-{centered_x})*t/{TRANSITION_DURATION:.3f}"
        else:
            entry = f"-overlay_w+({centered_x}+overlay_w)*t/{TRANSITION_DURATION:.3f}"
    transition_start = transition_starts[index] if index < len(rendered_clips) - 1 else clip_durations[index]
    exiting = centered_x
    if index < len(rendered_clips) - 1 and modes[index + 1] == "fullscreen":
        if exits[index] == "to_left":
            exiting = f"{centered_x}-({centered_x}+overlay_w)*(t-{transition_start:.3f})/{TRANSITION_DURATION:.3f}"
        elif exits[index] == "to_right":
            exiting = f"{centered_x}+(main_w-{centered_x})*(t-{transition_start:.3f})/{TRANSITION_DURATION:.3f}"
    card_x = (
        f"if(lt(t,{TRANSITION_DURATION:.3f}),{entry},"
        f"if(lt(t,{transition_start:.3f}),{centered_x},{exiting}))"
    )
    frame_offset = round(scene_starts[index] * BACKGROUND_FPS)
    background_graph = (
        f"[1:v]{background_animation_filter(background_animation, frame_offset)},"
        "format=rgb24,lutrgb=r='min(255,val*5)':g='min(255,val*5)':b='min(255,val*5)',"
        f"{background_light_filter(background_animation, scene_starts[index])},vignette=PI/4:eval=frame,"
        f"fps={BACKGROUND_FPS},settb=1/{FPS},setsar=1,trim=duration={duration:.6f},setpts=PTS-STARTPTS[background]"
    )
    filter_graph = ";".join([
        background_graph,
        f"[0:v]settb=1/{FPS},setpts=PTS-STARTPTS{padding},split=2[card][shadow_source]",
        "[shadow_source]format=rgba,colorchannelmixer=rr=0:gg=0:bb=0:aa=0.42,boxblur=18:2[shadow]",
        f"[background][shadow]overlay=x='({card_x})+18':y='(main_h-overlay_h)/2+22':format=auto[shadow_layer]",
        f"[shadow_layer][card]overlay=x='{card_x}':y='(main_h-overlay_h)/2':format=auto,"
        f"trim=duration={duration:.6f},format=yuv420p[out]",
    ])
    execute([
        str(FFMPEG), "-y", "-i", str(source), "-loop", "1", "-framerate", str(BACKGROUND_FPS),
        "-t", f"{duration:.6f}", "-i", str(background),
        "-filter_complex_threads", "1", "-filter_threads", "1",
        "-filter_complex", filter_graph, "-map", "[out]", "-an",
        "-frames:v", str(frames), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
        "-pix_fmt", "yuv420p", "-r", str(FPS), str(output),
    ])
    return output


def render_visual_segment(
    segment: RenderSegment,
    segment_index: int,
    segment_dir: Path,
    scene_paths: list[Path],
    scene_starts: list[float],
    modes: list[str],
    exits: list[str],
) -> Path:
    """Compõe somente uma janela pequena de xfade e devolve um MP4 sem áudio."""
    render_end = segment.handoff_index if segment.handoff_index is not None else segment.end_index
    indices = list(range(segment.start_index, render_end + 1))
    if len(indices) > MAX_SCENES_PER_SEGMENT + 1:
        raise RuntimeError("O fragmento excedeu o orçamento de cenas do compositor.")
    segment_dir.mkdir(parents=True, exist_ok=True)
    output = segment_dir / f"segmento_{segment_index + 1:03d}.mp4"
    inputs: list[str] = []
    filters: list[str] = []
    base_start = scene_starts[segment.start_index]
    labels: dict[int, str] = {}
    for input_index, scene_index in enumerate(indices):
        inputs.extend(["-i", str(scene_paths[scene_index])])
        label = f"[scene_{scene_index}]"
        relative_start = scene_starts[scene_index] - base_start
        filters.append(
            f"[{input_index}:v]settb=1/{FPS},setpts=PTS-STARTPTS+{relative_start:.6f}/TB{label}"
        )
        labels[scene_index] = label

    video_label = labels[segment.start_index]
    for scene_index in indices[1:]:
        output_label = f"[transition_{scene_index}]"
        offset = scene_starts[scene_index] - base_start
        previous_mode = modes[scene_index - 1]
        current_mode = modes[scene_index]
        if previous_mode == "card" and current_mode == "card":
            filters.append(
                f"{video_label}{labels[scene_index]}xfade=transition=fade:duration={TRANSITION_DURATION:.6f}:offset={offset:.6f}{output_label}"
            )
        elif exits[scene_index - 1] == "to_left":
            filters.append(
                f"{video_label}{labels[scene_index]}xfade=transition=smoothleft:duration={TRANSITION_DURATION:.6f}:offset={offset:.6f}{output_label}"
            )
        elif exits[scene_index - 1] == "to_right":
            filters.extend([
                f"{video_label}hflip[flip_current_{scene_index}]",
                f"{labels[scene_index]}hflip[flip_next_{scene_index}]",
                f"[flip_current_{scene_index}][flip_next_{scene_index}]xfade=transition=smoothleft:duration={TRANSITION_DURATION:.6f}:offset={offset:.6f}[flip_mix_{scene_index}]",
                f"[flip_mix_{scene_index}]hflip{output_label}",
            ])
        else:
            filters.append(
                f"{video_label}{labels[scene_index]}xfade=transition=fade:duration={TRANSITION_DURATION:.6f}:offset={offset:.6f}{output_label}"
            )
        video_label = output_label

    filters.append(
        f"{video_label}trim=start={segment.trim_start:.6f}:duration={segment.output_duration:.6f},"
        "setpts=PTS-STARTPTS,format=yuv420p[out]"
    )
    execute([
        str(FFMPEG), "-y", *inputs,
        "-filter_complex_threads", "1", "-filter_threads", "1",
        "-filter_buffered_frames", str(MAX_FILTER_BUFFERED_FRAMES),
        "-filter_complex", ";".join(filters), "-map", "[out]", "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
        "-r", str(BACKGROUND_FPS), "-movflags", "+faststart", str(output),
    ])
    return output


def concatenate_visual_segments(segment_paths: list[Path], output_dir: Path) -> Path:
    """Une segmentos H.264 idênticos sem abrir todas as cenas novamente."""
    if not segment_paths:
        raise RuntimeError("Nenhum fragmento visual foi produzido.")
    concat_file = output_dir / "segmentos.ffconcat"
    lines = ["ffconcat version 1.0"]
    for path in segment_paths:
        escaped = path.resolve().as_posix().replace("'", r"'\\''")
        lines.append(f"file '{escaped}'")
    concat_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    output = output_dir / "video_sem_audio.mp4"
    execute([
        str(FFMPEG), "-y", "-safe", "0", "-f", "concat", "-i", str(concat_file),
        "-c", "copy", "-movflags", "+faststart", str(output),
    ])
    return output


def render_final_audio_and_annotations(
    visual: Path,
    narration: Path,
    music: Path,
    sound_events: list[tuple[str, float]],
    annotations: list[tuple[list[str], float, float, str | None]],
    narration_duration: float,
    tail_duration: float,
    output: Path,
    filter_script: Path,
    use_vintage_effect: bool = False,
) -> None:
    """Aplica textos e mixagem em um único vídeo já concatenado.

    Não há mais dezenas de entradas de vídeo nesta etapa. A cadeia editorial
    continua fiel ao compositor aprovado, porém trabalha sobre apenas uma
    fonte visual, o que limita drasticamente as filas de frames.
    """
    filters: list[str] = []
    video_label = "[0:v]"
    for index, (lines, annotation_start, annotation_end, emoji) in enumerate(annotations):
        base = f"[annotation_base{index}]"
        blur = f"[annotation_blur{index}]"
        blurred = f"[annotation_layer{index}]"
        enabled = time_window(annotation_start, annotation_end)
        filters.append(f"{video_label}split=2{base}[annotation_source{index}]")
        filters.append(f"[annotation_source{index}]boxblur=22:6:enable='{enabled}'{blur}")
        filters.append(f"{base}{blur}overlay=0:0:enable='{enabled}'{blurred}")
        video_label = typing_annotation_filters(filters, blurred, index, lines, annotation_start, annotation_end, emoji)
    if use_vintage_effect:
        vintage_output = "[video_vintage]"
        filters.append(f"{video_label}noise=alls=4:allf=t+u,eq=contrast=1.03:saturation=0.90{vintage_output}")
        video_label = vintage_output
    filters.append(
        f"{video_label}trim=duration={narration_duration + tail_duration:.6f},format=yuv420p[video]"
    )

    voice_index = 1
    music_index = 2
    effect_input_start = 3
    events_by_effect: dict[str, list[float]] = {}
    for effect, event_time in sound_events:
        events_by_effect.setdefault(effect, []).append(event_time)
    sound_input_indices = {
        effect: effect_input_start + index
        for index, effect in enumerate(events_by_effect)
    }
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
    filter_script.write_text(";".join(filters), encoding="utf-8")
    inputs: list[str] = ["-i", str(visual), "-i", str(narration), "-stream_loop", "-1", "-i", str(music)]
    for effect in events_by_effect:
        inputs.extend(["-i", str(SOUND_EFFECTS[effect])])
    execute([
        str(FFMPEG), "-y", *inputs,
        "-filter_complex_threads", "1", "-filter_threads", "1",
        "-filter_buffered_frames", str(MAX_FILTER_BUFFERED_FRAMES),
        "-filter_complex_script", str(filter_script), "-map", "[video]", "-map", "[audio]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
        "-r", str(BACKGROUND_FPS), "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", "-shortest", str(output),
    ])


def discard_consumed_scene_sources(
    rendered_clips: list[Path],
    canvas_paths: dict[int, Path],
    before_index: int,
) -> None:
    """Libera intermediários que nenhum fragmento futuro pode reutilizar."""
    for index in range(before_index):
        for path in {rendered_clips[index], canvas_paths.get(index)}:
            if path is not None and path.is_file():
                path.unlink()


def main(
    use_vintage_effect: bool = False,
    keep_intermediates: bool = False,
    background_animation: str | None = None,
    music_name: str | None = None,
    script_path: Path | None = None,
    output_dir_override: Path | None = None,
    background_path: Path | None = None,
    image_directory: Path | None = None,
    timings_path: Path | None = None,
    narration_path: Path | None = None,
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
    voice = (narration_path or output_dir / "narracao.mp3").resolve()
    final = music_output_path(selected_music, custom_music=music_name is not None, output_dir=output_dir, output_stem=output_stem)
    clips = output_dir / "cenas_com_movimento"
    selected_background = (background_path or BACKGROUND).resolve()
    selected_images = (image_directory or IMAGES).resolve()
    if not selected_background.is_file():
        raise FileNotFoundError(f"Fundo ausente para a renderização: {selected_background}")
    if not selected_images.is_dir():
        raise FileNotFoundError(f"Diretório de imagens ausente para a renderização: {selected_images}")
    if not ANNOTATION_FONT.is_file():
        raise FileNotFoundError(f"Fonte de anotação ausente: {ANNOTATION_FONT}")
    if not EMOJI_FONT.is_file():
        raise FileNotFoundError(f"Fonte de emoji ausente: {EMOJI_FONT}")
    clips.mkdir(parents=True, exist_ok=True)

    # A opção explícita da linha de comando permite testar alternativas sem
    # editar o roteiro. Na ausência dela, usa-se a escolha salva no JSON.
    selected_background_animation = background_animation or payload.get("background_animation", "movimento_sutil")
    if len(scene_specs) != len(image_order):
        raise ValueError("O roteiro e a lista de imagens precisam ter a mesma quantidade de cenas.")
    missing_images = [name for name in image_order if not (selected_images / name).is_file()]
    if missing_images:
        raise FileNotFoundError("Imagens-fonte ausentes:\n" + "\n".join(missing_images))
    # O roteiro decide quais cenas são fullscreen e em que direção cada uma
    # sai. Assim, o render final preserva a dinâmica aprovada no preview.
    modes = layout_modes_from_script(scene_specs)
    exits = transition_exits_from_script(scene_specs)
    if narration_path is None:
        create_narration_if_needed(voice, payload)
    elif not voice.is_file():
        raise FileNotFoundError(f"Narração sincronizada não encontrada: {voice}")

    # Sem mapa acústico, mantém-se exatamente a distribuição uniforme legada.
    # Com --timings, cada troca começa no start da próxima fala: a sobreposição
    # ganha duração visual extra no clipe anterior, nunca desloca a cena seguinte.
    narration_duration = media_duration(voice)
    if timings_path is None:
        target_scene_duration = (narration_duration + TRANSITION_DURATION * (len(image_order) - 1)) / len(image_order)
        frames = math.ceil(target_scene_duration * FPS)
        scene_duration = frames / FPS
        scene_interval = scene_duration - TRANSITION_DURATION
        scene_starts = [index * scene_interval for index in range(len(scene_specs))]
        speech_durations = [scene_duration] * len(scene_specs)
        clip_durations = [scene_duration] * len(scene_specs)
        scene_frames = [frames] * len(scene_specs)
        # Compatibilidade para a execução legada sem mapa acústico. As CTAs
        # canônicas continuam protegidas mesmo nesse modo manual.
        visual_locks = [
            isinstance(scene.get("annotation"), dict)
            and scene["annotation"].get("emoji") in {"👍", "🔔"}
            for scene in scene_specs
        ]
        annotation_starts = [None] * len(scene_specs)
    else:
        timing_plan = load_scene_timing_plan(timings_path, scene_specs, narration_duration)
        scene_starts = timing_plan.starts
        speech_durations = timing_plan.speech_durations
        visual_locks = timing_plan.visual_locks
        annotation_starts = timing_plan.annotation_starts
        scene_frames = [_frames_for_duration(duration) for duration in timing_plan.clip_durations]
        # Os arquivos de entrada precisam acabar em um quadro inteiro, mas os
        # offsets do xfade continuam sendo os timestamps acústicos originais.
        clip_durations = [frames / FPS for frames in scene_frames]
    transition_starts = [
        scene_starts[index + 1] - scene_starts[index] if index < len(scene_specs) - 1 else 0.0
        for index in range(len(scene_specs))
    ]

    # Todos os efeitos são declarados por intenção no JSON. Não existe fallback
    # por índice, porcentagem ou repetição previsível.
    sound_events: list[tuple[str, float]] = []
    annotations: list[tuple[list[str], float, float, str | None]] = []
    for index, scene in enumerate(scene_specs):
        scene_start = scene_starts[index]
        speech_duration = speech_durations[index]
        sounds = scene.get("sounds", {})
        if not isinstance(sounds, dict):
            raise ValueError(f"{scene.get('id', f'cena {index + 1}')}.sounds deve ser um objeto.")
        if index < len(scene_specs) - 1:
            for effect in effect_ids(sounds.get("transition", []), f"{scene.get('id', index)}.sounds.transition"):
                sound_events.append((effect, scene_starts[index + 1]))
        context = sounds.get("context")
        if context is not None:
            if not isinstance(context, dict):
                raise ValueError(f"{scene.get('id', index)}.sounds.context deve ser um objeto.")
            context_effects = effect_ids(context.get("type"), f"{scene.get('id', index)}.sounds.context.type")
            if len(context_effects) != 1:
                raise ValueError("sounds.context.type deve identificar exatamente um efeito.")
            sound_events.append((context_effects[0], moment_in_scene(scene_start, speech_duration, context.get("at", "middle"))))
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
            # Uma CTA só pode aparecer sobre a própria cena enquanto ainda há
            # outra fala a seguir. A última cena fica livre para completar sua
            # leitura, mantendo a imagem final em vez de introduzir um corte.
            next_scene_start = scene_starts[index + 1] if index < len(scene_specs) - 1 else None
            end_limit = next_scene_start if visual_locks[index] else None
            annotation_start, annotation_end = annotation_window(
                scene_start,
                speech_duration,
                annotation.get("at", "start"),
                [line.strip() for line in lines],
                emoji,
                end_limit=end_limit,
                scheduled_start=annotation_starts[index],
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
        frames = scene_frames[index]
        clip = clips / f"cena_{index + 1:02d}.mp4"
        # Cartões preservam o desenho atualizado. A tela cheia usa um zoom
        # contínuo com foco editorial, sem os degraus do zoompan.
        if modes[index] == "fullscreen":
            filter_graph = fullscreen_scene_filter(frames, fullscreen_index)
            fullscreen_index += 1
        else:
            filter_graph = scene_filter(frames)
        execute([
            str(FFMPEG), "-y", "-loop", "1", "-framerate", str(FPS), "-i", str(selected_images / image_name),
            "-filter_complex", filter_graph, "-map", "[out]", "-an",
            "-frames:v", str(frames), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22", "-pix_fmt", "yuv420p", "-r", str(FPS), str(clip),
        ])
        rendered_clips.append(clip)

    # Cada fragmento abre no máximo treze entradas (doze cenas e a cena de
    # guarda da transição). Isso substitui o antigo filter graph monolítico,
    # que abria todas as cenas do vídeo e acabava esgotando a memória.
    segments = build_render_segments(scene_starts, narration_duration, tail_duration)
    canvas_dir = output_dir / "cenas_prontas"
    segment_dir = output_dir / "segmentos"
    canvas_paths: dict[int, Path] = {}
    scene_paths = list(rendered_clips)
    segment_paths: list[Path] = []
    for segment_index, segment in enumerate(segments):
        render_end = segment.handoff_index if segment.handoff_index is not None else segment.end_index
        for index in range(segment.start_index, render_end + 1):
            canvas_paths[index] = render_scene_canvas(
                index,
                rendered_clips,
                canvas_dir,
                selected_background,
                selected_background_animation,
                modes,
                exits,
                scene_starts,
                transition_starts,
                clip_durations,
                tail_duration,
            )
            scene_paths[index] = canvas_paths[index]
        segment_paths.append(render_visual_segment(
            segment,
            segment_index,
            segment_dir,
            scene_paths,
            scene_starts,
            modes,
            exits,
        ))
        next_required = segment.handoff_index if segment.handoff_index is not None else len(rendered_clips)
        discard_consumed_scene_sources(rendered_clips, canvas_paths, next_required)

    visual = concatenate_visual_segments(segment_paths, output_dir)
    expected_visual_duration = narration_duration + tail_duration
    visual_duration = media_duration(visual)
    if abs(visual_duration - expected_visual_duration) > 3 / FPS:
        raise RuntimeError(
            "A concatenação dos fragmentos ficou fora da duração acústica esperada "
            f"({visual_duration:.3f}s em vez de {expected_visual_duration:.3f}s)."
        )
    filter_script = output_dir / "filtros_renderizacao.ffscript"
    render_final_audio_and_annotations(
        visual,
        voice,
        selected_music,
        sound_events,
        annotations,
        narration_duration,
        tail_duration,
        final,
        filter_script,
        use_vintage_effect,
    )
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
    parser.add_argument(
        "--diretorio-imagens",
        type=Path,
        help="diretório isolado com os arquivos de cena, preservando os nomes declarados no roteiro",
    )
    parser.add_argument(
        "--timings",
        type=Path,
        help=(
            "JSON acústico por cena: {'scenes': [{'id': 'scene_01', 'start': 0.0, 'duration': 4.2}]}; "
            "quando omitido, preserva a divisão uniforme legada"
        ),
    )
    parser.add_argument(
        "--narracao",
        type=Path,
        help="faixa de narração já sincronizada pelo orquestrador, incluindo pausas de CTA",
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
        image_directory=args.diretorio_imagens,
        timings_path=args.timings,
        narration_path=args.narracao,
    )
