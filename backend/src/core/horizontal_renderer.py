"""Renderizador horizontal com cartões e cenas fullscreen intercaladas."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import re
import shutil
import subprocess
import sys
import unicodedata
from collections.abc import Callable, Mapping
from logging import Logger
from pathlib import Path
from tempfile import mkdtemp

from ..config import FINAL_OUTPUT_DIR, FFMPEG, FFPROBE, IMAGE_DIR, ROOT
from ..models import Script
from ..services import missing_scene_images, resolve_scene_image_sources
from .tts_neural import TTSNeuralEngine, WordBoundary

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
# A cadência-alvo é curta, mas pequenas variações da voz neural são normais.
# Até 10,5 s a mesma arte permanece em tela; acima disso o bloco ainda precisa
# ser dividido para preservar a retenção e o ritmo documental.
MAX_SCENE_ACOUSTIC_SECONDS = 10.5
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

ProgressCallback = Callable[[int, str], None]


class AcousticAlignmentError(ValueError):
    """O Edge TTS gerou time-codes que não podem ser associados ao roteiro."""


def _aligned_acoustic_words(script: Script, boundaries: list[WordBoundary]) -> list[tuple[WordBoundary, list[str]]]:
    """Valida o contrato entre o texto oficial e os time-codes do Edge TTS.

    Esta checagem acontece antes de qualquer acesso posicional. Assim uma
    resposta parcial do serviço de TTS nunca se transforma em ``IndexError``
    sem contexto no painel.
    """
    acoustic_words = [
        (boundary, _normalized_words(boundary.text))
        for boundary in boundaries
        if _normalized_words(boundary.text)
    ]
    expected_words = [word for block in script.blocks for word in _normalized_words(block.text)]
    received_words = [word for _, words in acoustic_words for word in words]
    if received_words == expected_words:
        return acoustic_words

    mismatch = next(
        (
            index
            for index, (received, expected) in enumerate(zip(received_words, expected_words), start=1)
            if received != expected
        ),
        min(len(received_words), len(expected_words)) + 1,
    )
    received = received_words[mismatch - 1] if mismatch <= len(received_words) else "<fim>"
    expected = expected_words[mismatch - 1] if mismatch <= len(expected_words) else "<fim>"
    raise AcousticAlignmentError(
        "Os time-codes retornados pelo Edge TTS não correspondem ao roteiro "
        f"na palavra {mismatch} (áudio: {received!r}; roteiro: {expected!r}; "
        f"recebidas: {len(received_words)}; esperadas: {len(expected_words)}). "
        "Tente renderizar novamente; se persistir, simplifique a pontuação ou divida o bloco indicado."
    )


def _block_acoustic_word_timings(script: Script, boundaries: list[WordBoundary]) -> list[list[WordBoundary]]:
    """Agrupa os time-codes por bloco sem supor um evento por palavra."""
    acoustic_events = _aligned_acoustic_words(script, boundaries)
    timed_words = [boundary for boundary, words in acoustic_events for _ in words]
    cursor = 0
    result: list[list[WordBoundary]] = []
    for block in script.blocks:
        block_words = _normalized_words(block.text)
        if not block_words:
            raise ValueError(f"O bloco {block.id} não contém palavras para narrar.")
        block_end = cursor + len(block_words)
        if block_end > len(timed_words):
            raise AcousticAlignmentError(
                f"O bloco {block.id} exige {len(block_words)} palavras, mas os time-codes terminaram antes dele."
            )
        result.append(timed_words[cursor:block_end])
        cursor = block_end
    return result


def _block_acoustic_ranges(script: Script, boundaries: list[WordBoundary]) -> list[tuple[float, float]]:
    """Retorna os intervalos acústicos dos blocos, mesmo se o TTS agrupar palavras."""
    return [(words[0].start, words[-1].end) for words in _block_acoustic_word_timings(script, boundaries)]


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
    """Cria um nome de entrega estável a partir do título do roteiro."""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    result = re.sub(r"[^a-z0-9]+", "_", ascii_value).strip("_")
    return result or "video"


def _published_output_path(title: str) -> Path:
    """Retorna o único local público do MP4 final para um roteiro."""
    FINAL_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return FINAL_OUTPUT_DIR / f"{_slug(title)}.mp4"


def _compositor_output(job_dir: Path) -> Path:
    """Localiza o único MP4 que resta após a limpeza do compositor."""
    outputs = sorted(path for path in job_dir.glob("*.mp4") if path.is_file())
    if len(outputs) != 1:
        found = ", ".join(path.name for path in outputs) or "nenhum arquivo"
        raise RuntimeError(
            "O compositor deveria deixar exatamente um MP4 final no lote; "
            f"foram encontrados: {found}."
        )
    return outputs[0]


def _publish_output(source: Path, title: str) -> Path:
    """Move o MP4 pronto para a área de entregas sem tocar nos assets-fonte.

    ``source`` e o destino pertencem ao mesmo workspace. ``replace`` portanto
    publica a nova versão de modo atômico e só substitui um vídeo final com o
    mesmo título depois que a renderização terminou com sucesso.
    """
    destination = _published_output_path(title)
    try:
        source.replace(destination)
    except OSError as exc:
        raise RuntimeError(
            f"Não foi possível publicar o vídeo final em {destination.name}."
        ) from exc
    return destination


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
        return (
            "scale=2048:1152:force_original_aspect_ratio=increase,crop=2048:1152,"
            "perspective="
            f"x0='64+28*sin(0.13*on/{FPS})':y0='36':"
            f"x1='1984+28*sin(0.13*on/{FPS})':y1='36':"
            f"x2='64+28*sin(0.13*on/{FPS})':y2='1116':"
            f"x3='1984+28*sin(0.13*on/{FPS})':y3='1116':"
            "interpolation=cubic:eval=frame,crop=1920:1080"
        )
    if animation == "pulsacao":
        return (
            "scale=2048:1152:force_original_aspect_ratio=increase,crop=2048:1152,"
            "perspective="
            f"x0='64+11*sin(0.45*on/{FPS})':y0='36+6*sin(0.45*on/{FPS})':"
            f"x1='1984-11*sin(0.45*on/{FPS})':y1='36+6*sin(0.45*on/{FPS})':"
            f"x2='64+11*sin(0.45*on/{FPS})':y2='1116-6*sin(0.45*on/{FPS})':"
            f"x3='1984-11*sin(0.45*on/{FPS})':y3='1116-6*sin(0.45*on/{FPS})':"
            "interpolation=cubic:eval=frame,crop=1920:1080,"
            "eq=brightness='0.015*sin(0.55*t)':eval=frame"
        )
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


def _normalized_words(value: str) -> list[str]:
    """Normaliza palavras para comparar roteiro oficial e marcação acústica."""
    raw_words = re.findall(r"[^\W_]+(?:['’-][^\W_]+)*", value, flags=re.UNICODE)
    words: list[str] = []
    for raw in raw_words:
        normalized = "".join(
            character
            for character in unicodedata.normalize("NFKD", raw).casefold()
            if character.isalnum()
        )
        if normalized:
            words.append(normalized)
    return words


CTA_WORDS = frozenset({
    "like", "curta", "curtir", "inscreva", "inscrevase", "inscricao", "canal",
    "subscribe", "subscribed", "subscribing", "suscribete", "suscribirse",
    "suscripcion", "abonnieren", "abonniere", "abonnieren", "lajk", "pretplati",
    "pretplatite", "subskrybuj", "subskrypcja",
})
# Termos que iniciam a fala de uma CTA. Eles determinam quando a anotação
# entra na tela, sem confundir palavras contextuais como "canal" com um cue.
CTA_CUE_WORDS = frozenset({
    "deixe", "like", "curta", "curtir", "inscreva", "inscrevase",
    "subscribe", "subscribed", "subscribing", "suscribete", "suscribirse",
    "abonnieren", "abonniere", "lajk", "pretplati", "pretplatite", "subskrybuj",
})
CTA_TYPING_DELAY = 0.25
CTA_TYPING_STEP = 0.035
CTA_LINE_GAP = 0.08
CTA_POST_TYPING_HOLD = 4.2
# A anotação editorial usa a mesma pausa antes da primeira letra no compositor
# aprovado. Ao agendá-la por uma palavra falada, compensamos essa pausa para a
# primeira letra aparecer no próprio cue acústico, não um quarto de segundo
# depois dele.
ANNOTATION_TYPING_DELAY = 0.25

# As anotações de ranking são geradas como "5º CENTRALIA", enquanto a
# narração costuma dizer "em quinto lugar". Estes vocábulos cobrem os idiomas
# aceitos pela esteira horizontal e são comparados já sem acentos.
RANK_CUE_WORDS: dict[int, frozenset[str]] = {
    1: frozenset({"primeiro", "primeira", "first", "primero", "primera", "erste", "erster", "ersten", "erstem", "pierwszy", "pierwsza", "pierwszym", "prvi", "prva", "prvom"}),
    2: frozenset({"segundo", "segunda", "second", "zweite", "zweiter", "zweiten", "zweitem", "drugi", "druga", "drugim", "drugom"}),
    3: frozenset({"terceiro", "terceira", "third", "tercero", "tercera", "dritte", "dritter", "dritten", "drittem", "trzeci", "trzecia", "trzecim", "treci", "treca", "trecem"}),
    4: frozenset({"quarto", "quarta", "fourth", "cuarto", "cuarta", "vierte", "vierter", "vierten", "viertem", "czwarty", "czwarta", "czwartym", "cetvrti", "cetvrta", "cetvrtom"}),
    5: frozenset({"quinto", "quinta", "fifth", "funfte", "funfter", "funften", "funftem", "piaty", "piata", "piatym", "peti", "peta", "petom"}),
}
RANK_ANNOTATION = re.compile(r"^\s*(\d{1,2})\s*(?:º|°|ª|st|nd|rd|th)(?=\s|$)", re.IGNORECASE)


def _is_subscription_cta(block: object) -> bool:
    """Identifica a chamada de inscrição que precisa manter a mesma tela.

    O emoji é o sinal canônico das CTAs geradas pelo prompt. As palavras são
    uma salvaguarda para roteiros editados manualmente que preservam a fala,
    mas omitem o emoji. O compositor recebe essa decisão no arquivo acústico,
    sem precisar inferir texto fora do contrato editorial.
    """
    scene = block.scenes[0]
    annotation = scene.annotation
    if annotation is not None and annotation.emoji in {"👍", "🔔"}:
        return True
    annotation_words = " ".join(annotation.lines) if annotation is not None else ""
    return bool(CTA_WORDS.intersection(_normalized_words(f"{block.text} {annotation_words}")))


def _cta_display_seconds(block: object) -> float:
    """Duração mínima para a CTA terminar de digitar e poder ser lida."""
    annotation = block.scenes[0].annotation
    if annotation is None or not _is_subscription_cta(block):
        return 0.0
    return (
        CTA_TYPING_DELAY
        + sum(len(line) for line in annotation.lines) * CTA_TYPING_STEP
        + max(0, len(annotation.lines) - 1) * CTA_LINE_GAP
        + CTA_POST_TYPING_HOLD
    )


def _cta_cue_word_index(block: object) -> int | None:
    """Localiza a primeira palavra falada que aciona a CTA no próprio bloco."""
    return next(
        (index for index, word in enumerate(_normalized_words(block.text)) if word in CTA_CUE_WORDS),
        None,
    )


def _rank_annotation_cue_word_index(block: object) -> int | None:
    """Encontra a palavra ordinal que anuncia uma anotação de ranking.

    ``annotation.at`` continua sendo a decisão editorial para notas comuns.
    Já um cartão como ``5º CENTRALIA`` é uma legenda da fala de ranking e não
    deve esperar o meio/final da cena: ele entra no instante de "quinto".
    """
    annotation = block.scenes[0].annotation
    if annotation is None or _is_subscription_cta(block):
        return None
    match = RANK_ANNOTATION.match(annotation.lines[0])
    if match is None:
        return None
    rank = int(match.group(1))
    cue_words = RANK_CUE_WORDS.get(rank)
    if cue_words is None:
        return None
    return next(
        (index for index, word in enumerate(_normalized_words(block.text)) if word in cue_words),
        None,
    )


def _cta_pause_plan(script: Script, boundaries: list[WordBoundary]) -> list[tuple[float, float]]:
    """Planeja silêncio real entre blocos para a CTA não acelerar o roteiro."""
    block_word_timings = _block_acoustic_word_timings(script, boundaries)

    pauses: list[tuple[float, float]] = []
    for index, block in enumerate(script.blocks[:-1]):
        display_seconds = _cta_display_seconds(block)
        if not display_seconds:
            continue
        cue_index = _cta_cue_word_index(block)
        # Roteiros antigos sem termo reconhecível mantêm o início do bloco;
        # para CTAs normais, a digitação começa junto do convite falado.
        annotation_start = block_word_timings[index][cue_index].start if cue_index is not None else block_word_timings[index][0].start
        next_start = block_word_timings[index + 1][0].start
        pause_seconds = max(0.0, annotation_start + display_seconds - next_start)
        if pause_seconds > 0.005:
            pauses.append((next_start, pause_seconds))
    return pauses


def _shift_boundaries_for_pauses(
    boundaries: list[WordBoundary],
    pauses: list[tuple[float, float]],
) -> list[WordBoundary]:
    shifted: list[WordBoundary] = []
    for boundary in boundaries:
        delay = sum(seconds for at, seconds in pauses if boundary.start >= at - 1e-6)
        shifted.append(WordBoundary(boundary.text, boundary.start + delay, boundary.end + delay))
    return shifted


def _insert_narration_pauses(source: Path, destination: Path, pauses: list[tuple[float, float]]) -> None:
    """Insere silêncio PCM no áudio, sem deslocar apenas o vídeo."""
    if not pauses:
        shutil.copy2(source, destination)
        return
    graph: list[str] = []
    labels: list[str] = []
    previous = 0.0
    for index, (at, seconds) in enumerate(pauses):
        audio_label = f"[audio_part_{index}]"
        silence_label = f"[silence_{index}]"
        graph.append(f"[0:a]atrim=start={previous:.6f}:end={at:.6f},asetpts=PTS-STARTPTS{audio_label}")
        graph.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={seconds:.6f}{silence_label}")
        labels.extend((audio_label, silence_label))
        previous = at
    final_label = f"[audio_part_{len(pauses)}]"
    graph.append(f"[0:a]atrim=start={previous:.6f},asetpts=PTS-STARTPTS{final_label}")
    labels.append(final_label)
    graph.append(f"{''.join(labels)}concat=n={len(labels)}:v=0:a=1[audio]")
    _run([
        str(FFMPEG), "-y", "-i", str(source), "-filter_complex", ";".join(graph),
        "-map", "[audio]", "-c:a", "pcm_s16le", str(destination),
    ])


def _scene_timing_payload(
    script: Script,
    boundaries: list[WordBoundary],
    narration_seconds: float,
) -> dict[str, object]:
    """Cria a linha do tempo visual a partir do áudio efetivamente sintetizado.

    Cada bloco representa uma unidade narrativa e, para não inventar uma
    associação entre fala e imagem, precisa declarar exatamente uma cena. A
    primeira imagem começa no zero da faixa (inclusive a respiração inicial) e
    as seguintes entram na primeira palavra de seus respectivos blocos.
    """
    ambiguous_blocks = [block.id for block in script.blocks if len(block.scenes) != 1]
    if ambiguous_blocks:
        names = ", ".join(ambiguous_blocks)
        raise ValueError(
            "Sincronismo acústico exato exige uma cena por bloco. "
            f"Divida estes blocos antes de renderizar: {names}."
        )

    block_word_timings = _block_acoustic_word_timings(script, boundaries)
    block_word_ranges = [(words[0].start, words[-1].end) for words in block_word_timings]

    starts = [0.0, *(start for start, _ in block_word_ranges[1:])]
    if narration_seconds < starts[-1]:
        raise RuntimeError("A duração medida da narração é menor que o último time-code acústico.")

    timing_scenes: list[dict[str, object]] = []
    for index, (block, start) in enumerate(zip(script.blocks, starts, strict=True)):
        # A duração acústica termina na última palavra do bloco. A eventual
        # pausa até o próximo bloco continua visualmente na mesma imagem, mas
        # não infla a duração de fala usada pelo limite de 9 s.
        last_word_end = block_word_ranges[index][1]
        duration = last_word_end - start
        if duration <= 0:
            raise RuntimeError(f"O bloco {block.id} recebeu uma duração acústica inválida ({duration:.3f}s).")
        if duration > MAX_SCENE_ACOUSTIC_SECONDS:
            scene_id = block.scenes[0].id
            raise ValueError(
                f"A cena {scene_id} dura {duration:.2f}s na narração e ultrapassa o limite flexível de "
                f"{MAX_SCENE_ACOUSTIC_SECONDS:.1f}s. "
                "Divida o texto em cenas menores antes do FFmpeg."
            )
        annotation_start: float | None = None
        if _is_subscription_cta(block):
            cue_index = _cta_cue_word_index(block)
            if cue_index is not None:
                annotation_start = block_word_timings[index][cue_index].start
        else:
            rank_cue_index = _rank_annotation_cue_word_index(block)
            if rank_cue_index is not None:
                cue_start = block_word_timings[index][rank_cue_index].start
                annotation_start = max(start, cue_start - ANNOTATION_TYPING_DELAY)
        timing_scenes.append({
            "id": block.scenes[0].id,
            "start": round(start, 6),
            "duration": round(duration, 6),
            # Durante uma CTA a arte e sua anotação formam uma única unidade:
            # a próxima imagem só pode aparecer quando a fala da CTA acabar.
            "lock_visual": _is_subscription_cta(block),
            # CTAs e rankings acompanham seus cues acústicos, não a posição
            # editorial aproximada dentro do parágrafo.
            **({"annotation_start": round(annotation_start, 6)} if annotation_start is not None else {}),
        })

    return {
        "scene_count": len(timing_scenes),
        "narration_duration": round(narration_seconds, 6),
        "scenes": timing_scenes,
    }


def _write_scene_timing(
    path: Path,
    script: Script,
    boundaries: list[WordBoundary],
    narration_seconds: float,
) -> dict[str, object]:
    payload = _scene_timing_payload(script, boundaries, narration_seconds)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _report(progress_callback: ProgressCallback | None, percent: int, stage: str) -> None:
    if progress_callback is not None:
        progress_callback(percent, stage)


def _materialize_scene_assets(
    script: Script,
    image_bindings: Mapping[str, str] | None,
    job_dir: Path,
) -> tuple[Path, dict[str, str]]:
    """Cria aliases privados das imagens sem tocar no JSON nem no acervo.

    O compositor legado lê ``scene.image`` do roteiro. Em vez de alterar esse
    arquivo editorial, cada fonte enviada é copiada para um diretório efêmero
    do job sob o nome esperado pelo JSON. Assim dois jobs podem usar vínculos
    diferentes para ``cena_01.png`` ao mesmo tempo, sem sobrescrever nada em
    ``assets/images``.
    """
    resolved_sources = resolve_scene_image_sources(script, image_bindings)
    missing = missing_scene_images(resolved_sources)
    if missing:
        raise FileNotFoundError("Imagens de cena ausentes: " + ", ".join(missing))

    asset_dir = Path(mkdtemp(prefix=".assets_cenas_", dir=job_dir))
    try:
        for expected_name, source_name in resolved_sources.items():
            shutil.copy2(IMAGE_DIR / source_name, asset_dir / expected_name)
    except Exception:
        shutil.rmtree(asset_dir, ignore_errors=True)
        raise
    return asset_dir, resolved_sources


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


def _render_lite(script: Script, background: Path, job_dir: Path) -> Path:
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
    TTSNeuralEngine().synthesize_sync(text, script.language, narration, script.voice)
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


def render(
    script: Script,
    background: Path,
    job_dir: Path,
    music_name: str | None = None,
    image_bindings: Mapping[str, str] | None = None,
    progress_callback: ProgressCallback | None = None,
    job_logger: Logger | None = None,
) -> Path:
    """Renderiza pelo compositor aprovado de cartões, anotações e sound design.

    A voz vem exclusivamente de ``script.voice``, validada junto do JSON. A
    composição final é delegada ao mesmo motor que gerou a preview aprovada:
    clipes pré-renderizados, transições dos cartões, CTA digitada, trilha com
    sidechain e todos os SFX declarados no roteiro. ``music_name`` é opcional
    para que o painel escolha uma trilha do catálogo sem mudar o nome público
    do vídeo entregue.
    """
    log = job_logger or logging.getLogger(__name__)
    if not background.is_file():
        raise FileNotFoundError(f"Imagem de fundo não encontrada: {background.name}")

    scenes = [scene for block in script.blocks for scene in block.scenes]
    compositor = ROOT / "scripts" / "legado" / "renderizar_animais_com_transicoes.py"
    if not compositor.is_file():
        raise FileNotFoundError("Compositor horizontal aprovado não encontrado.")

    job_dir.mkdir(parents=True, exist_ok=True)
    log.info("Iniciando renderização horizontal: %s cenas, voz %s.", len(scenes), script.voice)
    scene_asset_dir, resolved_sources = _materialize_scene_assets(script, image_bindings, job_dir)
    try:
        log.info("Assets de cena materializados: %s arquivo(s).", len(resolved_sources))
        narration = job_dir / "narracao.mp3"
        narration_text = " ".join(block.text.strip() for block in script.blocks)
        _report(progress_callback, 12, "Sintetizando narração e time-codes acústicos")
        raw_boundaries = TTSNeuralEngine().synthesize_with_word_boundaries_sync(
            narration_text, script.language, narration, script.voice
        )
        log.info("Narração criada com %s time-code(s) de palavra.", len(raw_boundaries))
        pauses = _cta_pause_plan(script, raw_boundaries)
        timeline_narration = narration
        boundaries = raw_boundaries
        if pauses:
            timeline_narration = job_dir / "narracao_com_pausas.wav"
            _insert_narration_pauses(narration, timeline_narration, pauses)
            boundaries = _shift_boundaries_for_pauses(raw_boundaries, pauses)
        narration_seconds = _duration(timeline_narration)
        timing_file = job_dir / "timings_cenas.json"
        timing_payload = _write_scene_timing(timing_file, script, boundaries, narration_seconds)
        log.info("Time-codes de %s cena(s) validados; narração: %.3fs.", timing_payload["scene_count"], narration_seconds)
        _report(progress_callback, 24, "Fala alinhada às cenas; preparando composição visual")

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
            "--diretorio-imagens",
            str(scene_asset_dir.resolve()),
            "--timings",
            str(timing_file),
            "--narracao",
            str(timeline_narration),
        ]
        if music_name is not None:
            if Path(music_name).name != music_name:
                raise ValueError("Informe somente o nome do arquivo de música do catálogo.")
            command.extend(["--musica", music_name])
        _report(progress_callback, 30, "Renderizando cenas, trilha e transições")
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            detail = (result.stderr or result.stdout or "erro desconhecido do compositor aprovado").strip()
            log.error("Compositor retornou código %s:\n%s", result.returncode, detail[-4000:])
            raise RuntimeError(f"Compositor horizontal não conseguiu renderizar o vídeo:\n{detail[-1800:]}")

        if timing_file.is_file():
            timing_file.unlink()
        _report(progress_callback, 92, "Publicando o vídeo final")
        output = _publish_output(_compositor_output(job_dir), script.title)
        log.info("Vídeo final publicado: %s", output)

        (job_dir / "metadata.json").write_text(
            json.dumps({
                "title": script.title,
                "duration_seconds": _duration(output),
                "output": str(output),
                "background": background.name,
                "music": music_name,
                "image_bindings": resolved_sources,
                "scene_timing": "time-codes acústicos da narração",
                "narration_duration_seconds": timing_payload["narration_duration"],
                "cta_pause_seconds": round(sum(seconds for _, seconds in pauses), 6),
                "layout_modes": _layout_modes(scenes),
                "transition_directions": _transition_directions(scenes),
                "renderer": "approved_card_sound_compositor",
                "status": "complete",
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _report(progress_callback, 98, "Entrega final organizada")
        return output
    finally:
        # O vídeo final e o manifesto preservam a decisão de vínculo. As
        # cópias de trabalho não ficam misturadas aos assets nem ao resultado.
        shutil.rmtree(scene_asset_dir, ignore_errors=True)
