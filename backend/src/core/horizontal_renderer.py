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
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from logging import Logger
from pathlib import Path
from tempfile import mkdtemp

from ..config import FINAL_OUTPUT_DIR, FFMPEG, FFPROBE, IMAGE_DIR, MUSIC_DIR, SOUND_DIR
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
MAX_SCENE_ACOUSTIC_SECONDS = 9.0
# O produto foi dimensionado para narrativas de até vinte minutos. Acima disso
# a fila, o espaço temporário e a revisão humana deixam de ter a mesma
# previsibilidade; o operador deve dividir o roteiro em episódios.
MAX_HORIZONTAL_NARRATION_SECONDS = 20 * 60
# O backend limita cada execução pesada a uma janela curta. A duração total do
# vídeo não é limitada por essas constantes; apenas o tamanho de cada grafo.
MAX_SCENES_PER_SEGMENT = 12
MAX_SEGMENT_SECONDS = 90.0
MAX_FILTER_BUFFERED_FRAMES = 128
# SFX também são montados por janelas: nunca existe um ``asplit`` global com
# centenas de sons aguardando offsets de muitos minutos.
SFX_CHUNK_SECONDS = 30.0
MAX_SFX_EVENTS_PER_CHUNK = 24
# A trilha não pode reiniciar em corte seco. A sobreposição é longa o bastante
# para mascarar a troca sem transformar duas músicas em uma massa sonora.
MUSIC_LOOP_CROSSFADE_SECONDS = 1.2
# Cadeia editorial aprovada na amostra "forte documental". Ela atua antes do
# ducking, portanto funciona de forma idêntica para todas as vozes e idiomas
# aceitos pelo TTS sem alterar os time-codes da narração.
VOICE_MASTERING_FILTER = (
    "highpass=f=70,"
    "equalizer=f=2800:t=q:w=1.0:g=2.8,"
    "acompressor=threshold=-22dB:ratio=2.8:attack=12:release=160:makeup=3.2,"
    "volume=1.41"
)
FINAL_AUDIO_LIMIT = 0.89
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


@dataclass(frozen=True)
class RenderSegment:
    """Faixa visual com uma cena de guarda para a transição de fronteira."""

    start_index: int
    end_index: int
    handoff_index: int | None
    trim_start_frames: int
    output_frames: int

    @property
    def trim_start(self) -> float:
        return self.trim_start_frames / FPS

    @property
    def output_duration(self) -> float:
        return self.output_frames / FPS


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


def _published_output_path(title: str, delivery_id: str) -> Path:
    """Retorna uma entrega pública única, inclusive para títulos repetidos."""
    FINAL_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_delivery_id = re.sub(r"[^a-zA-Z0-9_-]+", "", delivery_id)
    if not safe_delivery_id:
        raise ValueError("A entrega precisa ter um identificador seguro.")
    return FINAL_OUTPUT_DIR / f"{_slug(title)}_{safe_delivery_id}.mp4"


def _publish_output(source: Path, title: str, delivery_id: str) -> Path:
    """Move o MP4 pronto para a área de entregas sem tocar nos assets-fonte.

    ``source`` e o destino pertencem ao mesmo workspace. ``replace`` portanto
    publica a nova versão de modo atômico sem permitir que outra renderização
    de título igual substitua esta entrega.
    """
    destination = _published_output_path(title, delivery_id)
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
# A CTA precisa ser lida, mas não pode interromper o ritmo da narração.
# O hold anterior de 4,2s fazia o planejador inserir silêncios artificiais.
CTA_POST_TYPING_HOLD = 1.0
MAX_CTA_NARRATION_PAUSE = 0.35
# A tela de anotação é um elemento editorial próprio: mantém a mesma fonte,
# digitação amarela, blur de fundo e posição que já estavam aprovados.
ANNOTATION_FONT = Path(r"C:/Windows/Fonts/impact.ttf")
# Emojis nunca são desenhados pela fonte do sistema: ela produz ícones sem a
# linguagem visual do canal. Cada emoji usado na esteira horizontal precisa de
# um sticker PNG curado neste diretório persistente.
EMOJI_STICKER_DIR = Path(__file__).resolve().parents[3] / "workspace" / "assets" / "horizontal" / "overlays" / "emoji_stickers"
EMOJI_STICKERS = {
    "👍": EMOJI_STICKER_DIR / "like_3d.png",
    "🔔": EMOJI_STICKER_DIR / "bell_3d.png",
    "🏆": EMOJI_STICKER_DIR / "trophy_3d.png",
}
EMOJI_STICKER_HEIGHT = 250
EMOJI_STICKER_X = "main_w/2+270"
# A anotação editorial usa a mesma pausa antes da primeira letra no compositor
# aprovado. Ao agendá-la por uma palavra falada, compensamos essa pausa para a
# primeira letra aparecer no próprio cue acústico, não um quarto de segundo
# depois dele.
ANNOTATION_TYPING_DELAY = 0.25
ANNOTATION_TYPING_STEP = 0.045
ANNOTATION_LINE_GAP = 0.08
ANNOTATION_POST_TYPING_HOLD = 1.45

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
        pause_seconds = min(
            MAX_CTA_NARRATION_PAUSE,
            max(0.0, annotation_start + display_seconds - next_start),
        )
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
    *,
    enforce_limit: bool = True,
) -> dict[str, object]:
    """Cria a linha do tempo visual a partir do áudio efetivamente sintetizado.

    Cada bloco representa uma unidade narrativa e, para não inventar uma
    associação entre fala e imagem, precisa declarar exatamente uma cena. A
    primeira imagem começa no zero da faixa (inclusive a respiração inicial) e
    as seguintes entram na primeira palavra de seus respectivos blocos.
    """
    if narration_seconds > MAX_HORIZONTAL_NARRATION_SECONDS:
        raise ValueError(
            "A narração mede "
            f"{narration_seconds / 60:.2f} minutos e ultrapassa o limite de "
            f"{MAX_HORIZONTAL_NARRATION_SECONDS // 60} minutos da esteira horizontal. "
            "Divida o roteiro em episódios antes de renderizar."
        )

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
        if duration > MAX_SCENE_ACOUSTIC_SECONDS and enforce_limit:
            scene_id = block.scenes[0].id
            raise ValueError(
                f"A cena {scene_id} dura {duration:.2f}s na narração e ultrapassa o limite de "
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
        entry: dict[str, object] = {
            "id": block.scenes[0].id,
            "start": round(start, 6),
            "duration": round(duration, 6),
            # Durante uma CTA a arte e sua anotação formam uma única unidade:
            # a próxima imagem só pode aparecer quando a fala da CTA acabar.
            "lock_visual": _is_subscription_cta(block),
            # CTAs e rankings acompanham seus cues acústicos, não a posição
            # editorial aproximada dentro do parágrafo.
            **({"annotation_start": round(annotation_start, 6)} if annotation_start is not None else {}),
        }
        if duration > MAX_SCENE_ACOUSTIC_SECONDS:
            raw_words = re.findall(r"\S+", block.text)
            if len(raw_words) == len(block_word_timings[index]) and len(raw_words) > 1:
                middle = start + duration / 2
                cut = min(
                    range(1, len(raw_words)),
                    key=lambda position: abs(block_word_timings[index][position - 1].end - middle),
                )
                entry["suggested_split"] = {
                    "first_text": " ".join(raw_words[:cut]),
                    "second_text": " ".join(raw_words[cut:]),
                }
        timing_scenes.append(entry)

    return {
        "scene_count": len(timing_scenes),
        "narration_duration": round(narration_seconds, 6),
        "scenes": timing_scenes,
    }


def preview_scene_timing(
    script: Script, boundaries: list[WordBoundary], narration_seconds: float,
) -> dict[str, object]:
    """Mede cenas com a voz real sem bloquear o painel por duração longa."""
    return _scene_timing_payload(script, boundaries, narration_seconds, enforce_limit=False)


def narration_duration(path: Path) -> float:
    """Expõe a duração real de uma faixa sintetizada para a pré-validação."""
    return _duration(path)


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

    Em vez de alterar o arquivo editorial, cada fonte enviada é copiada para
    um diretório efêmero do job sob o nome esperado pelo JSON. Assim dois jobs
    podem usar vínculos diferentes para ``cena_01.png`` ao mesmo tempo, sem
    sobrescrever nada em ``assets/images``.
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


def _frames_for_duration(seconds: float) -> int:
    """Arredonda para um número inteiro de quadros sem acrescentar um a mais."""
    return max(1, math.ceil(seconds * FPS - 1e-9))


def _nearest_frame(seconds: float) -> int:
    """Converte um time-code acústico para o quadro visual mais próximo."""
    return max(0, math.floor(seconds * FPS + 0.5))


def _run_compositor(command: list[str]) -> None:
    """Executa FFmpeg sem acumular o progresso de vídeos longos na memória."""
    result = subprocess.run(
        [command[0], "-hide_banner", "-loglevel", "error", *command[1:]],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode:
        detail = (result.stderr or "erro desconhecido do FFmpeg").strip()
        if "buffered frames" in detail.lower():
            detail = (
                "O compositor atingiu o limite seguro de frames em memória. "
                "O trabalho foi interrompido antes de esgotar a RAM.\n" + detail
            )
        raise RuntimeError(
            "Compositor horizontal não conseguiu renderizar o vídeo:\n"
            + detail[-4000:]
        )


def _native_card_filter() -> str:
    """Prepara o cartão estável; o movimento pertence somente ao fullscreen."""
    return (
        "[0:v]"
        f"scale={CARD_W}:{CARD_H}:force_original_aspect_ratio=increase,crop={CARD_W}:{CARD_H},"
        f"fps={FPS},setsar=1,fade=t=in:st=0:d=0.16,"
        "drawbox=x=3:y=3:w=iw-6:h=ih-6:color=white@0.18:t=3[out]"
    )


def _native_background_filter(animation: str, frame_offset: int) -> str:
    frame = f"(on+{max(0, frame_offset)})"
    if animation == "none":
        return "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080"
    if animation == "movimento_sutil":
        return (
            "scale=2048:1152:force_original_aspect_ratio=increase,crop=2048:1152,"
            "perspective="
            f"x0='64+14*sin(0.20*{frame}/{FPS})':y0='36+8*cos(0.17*{frame}/{FPS})':"
            f"x1='1984+14*sin(0.20*{frame}/{FPS})':y1='36+8*cos(0.17*{frame}/{FPS})':"
            f"x2='64+14*sin(0.20*{frame}/{FPS})':y2='1116+8*cos(0.17*{frame}/{FPS})':"
            f"x3='1984+14*sin(0.20*{frame}/{FPS})':y3='1116+8*cos(0.17*{frame}/{FPS})':"
            "interpolation=cubic:eval=frame,crop=1920:1080"
        )
    if animation == "movimento_lateral":
        return (
            "scale=2048:1152:force_original_aspect_ratio=increase,crop=2048:1152,"
            "perspective="
            f"x0='64+28*sin(0.13*{frame}/{FPS})':y0='36':"
            f"x1='1984+28*sin(0.13*{frame}/{FPS})':y1='36':"
            f"x2='64+28*sin(0.13*{frame}/{FPS})':y2='1116':"
            f"x3='1984+28*sin(0.13*{frame}/{FPS})':y3='1116':"
            "interpolation=cubic:eval=frame,crop=1920:1080"
        )
    if animation == "pulsacao":
        return (
            "scale=2048:1152:force_original_aspect_ratio=increase,crop=2048:1152,"
            "perspective="
            f"x0='64+11*sin(0.45*{frame}/{FPS})':y0='36+6*sin(0.45*{frame}/{FPS})':"
            f"x1='1984-11*sin(0.45*{frame}/{FPS})':y1='36+6*sin(0.45*{frame}/{FPS})':"
            f"x2='64+11*sin(0.45*{frame}/{FPS})':y2='1116-6*sin(0.45*{frame}/{FPS})':"
            f"x3='1984-11*sin(0.45*{frame}/{FPS})':y3='1116-6*sin(0.45*{frame}/{FPS})':"
            "interpolation=cubic:eval=frame,crop=1920:1080"
        )
    raise ValueError(f"Animação de fundo inválida: {animation}.")


def _native_background_light(animation: str, time_offset: float) -> str:
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
    raise ValueError(f"Animação de fundo inválida: {animation}.")


def _build_render_segments(scene_start_frames: list[int], visual_total_frames: int) -> list[RenderSegment]:
    """Divide uma linha do tempo já quantizada para a grade de vídeo.

    O áudio continua com os time-codes exatos do TTS, mas os cortes visuais
    precisam existir em quadros inteiros. Fazer a quantização uma única vez
    impede que pequenas frações se acumulem a cada concatenação de segmento.
    """
    if not scene_start_frames:
        raise ValueError("O roteiro não contém cenas para compor.")
    if scene_start_frames[0] != 0:
        raise ValueError("A linha do tempo visual precisa começar no quadro zero.")
    if any(next_frame <= frame for frame, next_frame in zip(scene_start_frames, scene_start_frames[1:])):
        raise ValueError("Os cortes visuais precisam ocupar quadros estritamente crescentes.")
    if visual_total_frames <= scene_start_frames[-1]:
        raise ValueError("A duração visual não comporta a última cena.")

    result: list[RenderSegment] = []
    start = 0
    max_segment_frames = _frames_for_duration(MAX_SEGMENT_SECONDS)
    while start < len(scene_start_frames):
        end = start
        while end + 1 < len(scene_start_frames):
            candidate = end + 1
            if candidate - start + 1 > MAX_SCENES_PER_SEGMENT:
                break
            if scene_start_frames[candidate] - scene_start_frames[start] > max_segment_frames:
                break
            end = candidate
        handoff = end + 1 if end + 1 < len(scene_start_frames) else None
        trim_start_frames = 0 if start == 0 else TRANSITION_FRAMES
        end_frame = (
            scene_start_frames[handoff] - scene_start_frames[start] + TRANSITION_FRAMES
            if handoff is not None
            else visual_total_frames - scene_start_frames[start]
        )
        output_frames = end_frame - trim_start_frames
        if output_frames <= 0:
            raise RuntimeError("A segmentação visual gerou um fragmento inválido.")
        result.append(RenderSegment(start, end, handoff, trim_start_frames, output_frames))
        start = handoff if handoff is not None else len(scene_start_frames)
    return result


def _native_render_scene_clips(
    scenes: list[object],
    scene_dir: Path,
    source_dir: Path,
    modes: list[str],
    clip_frames: list[int],
) -> list[Path]:
    scene_dir.mkdir(parents=True, exist_ok=True)
    clips: list[Path] = []
    fullscreen_index = 0
    for index, (scene, frames) in enumerate(zip(scenes, clip_frames, strict=True)):
        output = scene_dir / f"cena_{index + 1:03d}.mp4"
        source = source_dir / scene.image
        if modes[index] == "fullscreen":
            filter_graph = f"[0:v]{_fullscreen_filter(fullscreen_index, frames / FPS)}[out]"
            fullscreen_index += 1
        else:
            filter_graph = _native_card_filter()
        _run_compositor([
            str(FFMPEG), "-y", "-loop", "1", "-framerate", str(FPS), "-i", str(source),
            "-filter_complex_threads", "1", "-filter_threads", "1",
            "-filter_complex", filter_graph, "-map", "[out]", "-an", "-frames:v", str(frames),
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22", "-pix_fmt", "yuv420p",
            "-r", str(FPS), str(output),
        ])
        clips.append(output)
    return clips


def _native_canvas_scene(
    index: int,
    scenes: list[object],
    clips: list[Path],
    canvas_dir: Path,
    background: Path,
    modes: list[str],
    exits: list[str],
    scene_starts: list[float],
    transition_starts: list[float],
    clip_durations: list[float],
    animation: str,
    tail_seconds: float,
) -> Path:
    source = clips[index]
    scene_tail = tail_seconds if index == len(clips) - 1 else 0.0
    if modes[index] == "fullscreen" and not scene_tail:
        return source
    canvas_dir.mkdir(parents=True, exist_ok=True)
    output = canvas_dir / f"cena_{index + 1:03d}.mp4"
    if output.is_file():
        return output
    duration = clip_durations[index] + scene_tail
    padding = f",tpad=stop_mode=clone:stop_duration={scene_tail:.6f}" if scene_tail else ""
    if modes[index] == "fullscreen":
        graph = f"[0:v]settb=1/{FPS},setpts=PTS-STARTPTS{padding},trim=duration={duration:.6f},format=yuv420p[out]"
        command = [
            str(FFMPEG), "-y", "-i", str(source),
            "-filter_complex_threads", "1", "-filter_threads", "1", "-filter_complex", graph,
        ]
    else:
        centered = "(main_w-overlay_w)/2"
        entry = centered
        if index and modes[index - 1] == "fullscreen":
            entry = (
                f"main_w-(main_w-{centered})*t/{TRANSITION_SECONDS:.6f}"
                if exits[index - 1] == "to_left"
                else f"-overlay_w+({centered}+overlay_w)*t/{TRANSITION_SECONDS:.6f}"
            )
        exit_start = transition_starts[index] if index < len(clips) - 1 else clip_durations[index]
        exiting = centered
        if index < len(clips) - 1 and modes[index + 1] == "fullscreen":
            if exits[index] == "to_left":
                exiting = f"{centered}-({centered}+overlay_w)*(t-{exit_start:.6f})/{TRANSITION_SECONDS:.6f}"
            elif exits[index] == "to_right":
                exiting = f"{centered}+(main_w-{centered})*(t-{exit_start:.6f})/{TRANSITION_SECONDS:.6f}"
        card_x = f"if(lt(t,{TRANSITION_SECONDS:.6f}),{entry},if(lt(t,{exit_start:.6f}),{centered},{exiting}))"
        background_graph = (
            f"[1:v]{_native_background_filter(animation, round(scene_starts[index] * FPS))},"
            # O fundo escolhido é um asset editorial: preservamos suas cores
            # e contraste. Somente o movimento solicitado pelo painel altera
            # a imagem; o tratamento antigo escurecia fundos claros.
            f"fps={FPS},settb=1/{FPS},setsar=1,trim=duration={duration:.6f},setpts=PTS-STARTPTS[bg]"
        )
        graph = ";".join([
            background_graph,
            f"[0:v]settb=1/{FPS},setpts=PTS-STARTPTS{padding},split=2[card][shadow_source]",
            "[shadow_source]format=rgba,colorchannelmixer=rr=0:gg=0:bb=0:aa=0.42,boxblur=18:2[shadow]",
            f"[bg][shadow]overlay=x='({card_x})+18':y='(main_h-overlay_h)/2+22':format=auto[shadow_layer]",
            f"[shadow_layer][card]overlay=x='{card_x}':y='(main_h-overlay_h)/2':format=auto,trim=duration={duration:.6f},format=yuv420p[out]",
        ])
        command = [
            str(FFMPEG), "-y", "-i", str(source), "-loop", "1", "-framerate", str(FPS),
            "-t", f"{duration:.6f}", "-i", str(background),
            "-filter_complex_threads", "1", "-filter_threads", "1", "-filter_complex", graph,
        ]
    _run_compositor([
        *command, "-map", "[out]", "-an",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22", "-pix_fmt", "yuv420p",
        "-r", str(FPS), str(output),
    ])
    return output


def _native_render_segment(
    segment: RenderSegment,
    number: int,
    segment_dir: Path,
    scene_paths: list[Path],
    scene_starts: list[float],
    modes: list[str],
    exits: list[str],
) -> Path:
    """Aplica xfade a no máximo doze cenas e uma cena de guarda."""
    render_end = segment.handoff_index if segment.handoff_index is not None else segment.end_index
    indices = list(range(segment.start_index, render_end + 1))
    if len(indices) > MAX_SCENES_PER_SEGMENT + 1:
        raise RuntimeError("Fragmento visual excedeu o limite de cenas.")
    segment_dir.mkdir(parents=True, exist_ok=True)
    output = segment_dir / f"segmento_{number:03d}.mp4"
    inputs: list[str] = []
    filters: list[str] = []
    labels: dict[int, str] = {}
    first_start = scene_starts[segment.start_index]
    for input_index, scene_index in enumerate(indices):
        inputs.extend(["-i", str(scene_paths[scene_index])])
        label = f"[scene_{scene_index}]"
        relative_start = scene_starts[scene_index] - first_start
        filters.append(f"[{input_index}:v]settb=1/{FPS},setpts=PTS-STARTPTS+{relative_start:.6f}/TB{label}")
        labels[scene_index] = label

    video = labels[segment.start_index]
    for scene_index in indices[1:]:
        output_label = f"[transition_{scene_index}]"
        offset = scene_starts[scene_index] - first_start
        if exits[scene_index - 1] == "to_left":
            filters.append(f"{video}{labels[scene_index]}xfade=transition=smoothleft:duration={TRANSITION_SECONDS:.6f}:offset={offset:.6f}{output_label}")
        elif exits[scene_index - 1] == "to_right":
            filters.append(f"{video}{labels[scene_index]}xfade=transition=smoothright:duration={TRANSITION_SECONDS:.6f}:offset={offset:.6f}{output_label}")
        else:
            filters.append(f"{video}{labels[scene_index]}xfade=transition=fade:duration={TRANSITION_SECONDS:.6f}:offset={offset:.6f}{output_label}")
        video = output_label
    end_frame = segment.trim_start_frames + segment.output_frames
    filters.append(
        f"{video}trim=start_frame={segment.trim_start_frames}:end_frame={end_frame},"
        "setpts=PTS-STARTPTS,format=yuv420p[out]"
    )
    _run_compositor([
        str(FFMPEG), "-y", *inputs,
        "-filter_complex_threads", "1", "-filter_threads", "1",
        "-filter_buffered_frames", str(MAX_FILTER_BUFFERED_FRAMES),
        "-filter_complex", ";".join(filters), "-map", "[out]", "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
        "-r", str(FPS), "-movflags", "+faststart", str(output),
    ])
    return output


def _native_segment_manifest(paths: list[Path], job_dir: Path) -> Path:
    """Escreve o manifesto que o FFmpeg lerá diretamente na finalização.

    Não geramos um MP4 concatenado provisório: isso evitaria uma cópia inteira
    extra do vídeo no disco justamente nos roteiros de vinte minutos.
    """
    if not paths:
        raise RuntimeError("O compositor não produziu segmentos visuais.")
    manifest = job_dir / "segmentos.ffconcat"
    lines = ["ffconcat version 1.0"]
    for path in paths:
        escaped = path.resolve().as_posix().replace("'", r"'\\''")
        lines.append(f"file '{escaped}'")
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest


_NATIVE_SOUND_EFFECTS = {
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
_NATIVE_SOUND_SECONDS = {"typing": 1.15, "click": 0.18, "bottle_cork": 0.75, "new_idea": 0.75, "whoosh_fast": 0.55, "whoosh_cinematic": 0.75, "whoosh_soft": 0.55, "celebration": 1.8}
_NATIVE_SOUND_VOLUMES = {"typing": 0.72, "click": 0.42, "bottle_cork": 0.48, "new_idea": 0.46, "whoosh_fast": 0.48, "whoosh_cinematic": 0.44, "whoosh_soft": 0.42, "celebration": 0.40}
_NATIVE_SOUND_LEAD = {"bottle_cork": 0.272, "new_idea": 0.024}
_NATIVE_SOUND_FADE_OUT = {"celebration": 0.65}
# O aplauso começa antes do fim da cena para terminar antes da próxima fala.
_NATIVE_SOUND_END_LEAD = {"celebration": 1.8}


def _native_music_path(music_name: str | None) -> Path:
    if music_name:
        if Path(music_name).name != music_name:
            raise ValueError("Informe somente o nome do arquivo de música do catálogo.")
        music = MUSIC_DIR / music_name
        if music.is_file():
            return music
        raise FileNotFoundError(f"Música não encontrada: {music.name}")
    preferred = MUSIC_DIR / "fundo_documentario.mp3"
    if preferred.is_file():
        return preferred
    if not MUSIC_DIR.is_dir():
        raise FileNotFoundError("A pasta de trilhas horizontais não está disponível.")
    available = sorted(path for path in MUSIC_DIR.iterdir() if path.is_file() and path.suffix.lower() in {".mp3", ".wav", ".m4a"})
    if not available:
        raise FileNotFoundError("Nenhuma trilha horizontal está disponível.")
    return available[0]


def _native_looped_music_bed(music: Path, duration: float, directory: Path) -> Path:
    """Cria uma trilha de fundo contínua, sem reaproveitar silêncio de cauda.

    ``-stream_loop`` repetia o MP3 inteiro, inclusive o silêncio já presente
    no fim de algumas faixas, e a nova volta entrava de forma seca. Primeiro
    normalizamos um ciclo sem esse silêncio final; depois encadeamos somente os
    ciclos necessários com ``acrossfade``.
    """
    if duration <= 0:
        raise ValueError("A duração da trilha de fundo precisa ser positiva.")
    directory.mkdir(parents=True, exist_ok=True)
    cycle = directory / "trilha_ciclo.m4a"
    _run_compositor([
        str(FFMPEG), "-y", "-i", str(music),
        # Remove as duas pontas silenciosas antes de repetir a faixa. Sem a
        # remoção da abertura, uma música com introdução vazia ainda deixaria
        # uma lacuna perceptível a cada volta, mesmo com crossfade.
        "-af", (
            "aresample=48000,"
            "silenceremove=start_periods=1:start_duration=0.15:start_threshold=-45dB:"
            "stop_periods=1:stop_duration=0.15:stop_threshold=-45dB,"
            "asetpts=PTS-STARTPTS"
        ),
        "-c:a", "aac", "-b:a", "192k", str(cycle),
    ])
    cycle_duration = _duration(cycle)
    crossfade = min(MUSIC_LOOP_CROSSFADE_SECONDS, cycle_duration / 4)
    if cycle_duration <= crossfade:
        raise ValueError(f"A trilha {music.name} é curta demais para formar um loop suave.")
    if duration <= cycle_duration:
        return cycle

    effective_cycle = cycle_duration - crossfade
    copies = math.ceil((duration - cycle_duration) / effective_cycle) + 1
    bed = directory / "trilha_continua.m4a"
    inputs = [part for _ in range(copies) for part in ("-i", str(cycle))]
    graph = [f"[{index}:a]aresample=48000,asetpts=PTS-STARTPTS[music_{index}]" for index in range(copies)]
    mixed = "[music_0]"
    for index in range(1, copies):
        output = f"[music_mix_{index}]"
        graph.append(f"{mixed}[music_{index}]acrossfade=d={crossfade:.3f}:c1=tri:c2=tri{output}")
        mixed = output
    graph.append(f"{mixed}atrim=duration={duration:.6f},asetpts=PTS-STARTPTS[audio]")
    _run_compositor([
        str(FFMPEG), "-y", *inputs,
        "-filter_complex_threads", "1", "-filter_threads", "1",
        "-filter_complex", ";".join(graph), "-map", "[audio]",
        "-c:a", "aac", "-b:a", "192k", str(bed),
    ])
    return bed


def _native_annotation_plan(script: Script, timing: list[dict[str, object]]) -> tuple[list[tuple[list[str], float, float, str | None]], list[tuple[str, float]]]:
    annotations: list[tuple[list[str], float, float, str | None]] = []
    events: list[tuple[str, float]] = []
    scenes = [scene for block in script.blocks for scene in block.scenes]
    for index, (scene, entry) in enumerate(zip(scenes, timing, strict=True)):
        start = float(entry["start"])
        duration = float(entry["duration"])
        if index < len(scenes) - 1:
            for effect in scene.sounds.transition:
                events.append((effect, float(timing[index + 1]["start"])))
        if scene.sounds.context is not None:
            at = scene.sounds.context.at
            effect = scene.sounds.context.type
            if at == "start":
                event_time = start
            elif at == "middle":
                event_time = start + duration / 2
            else:
                lead = _NATIVE_SOUND_END_LEAD.get(effect, 0.18)
                event_time = start + max(0.0, duration - lead)
            events.append((effect, event_time))
        if scene.annotation is None:
            continue
        scheduled_start = float(entry["annotation_start"]) if "annotation_start" in entry else None
        end_limit = float(timing[index + 1]["start"]) if index < len(scenes) - 1 and bool(entry.get("lock_visual")) else None
        annotation_start, annotation_end = _native_annotation_window(
            start,
            duration,
            scene.annotation.at,
            scene.annotation.lines,
            scene.annotation.emoji,
            end_limit=end_limit,
            scheduled_start=scheduled_start,
        )
        annotations.append((scene.annotation.lines, annotation_start, annotation_end, scene.annotation.emoji))
        events.append(("typing", annotation_start + ANNOTATION_TYPING_DELAY))
        if scene.annotation.emoji == "👍":
            events.append(("bottle_cork", _native_annotation_emoji_time(annotation_start, scene.annotation.lines)))
        elif scene.annotation.emoji == "🔔":
            events.append(("new_idea", _native_annotation_emoji_time(annotation_start, scene.annotation.lines)))
    missing = sorted({effect for effect, _ in events if effect not in _NATIVE_SOUND_EFFECTS or not _NATIVE_SOUND_EFFECTS[effect].is_file()})
    if missing:
        raise FileNotFoundError("Efeitos sonoros ausentes: " + ", ".join(missing))
    return annotations, events


def _native_annotation_timing(emoji: str | None) -> tuple[float, float]:
    if emoji in {"👍", "🔔"}:
        return CTA_TYPING_STEP, CTA_POST_TYPING_HOLD
    return ANNOTATION_TYPING_STEP, ANNOTATION_POST_TYPING_HOLD


def _native_annotation_duration(lines: list[str], emoji: str | None) -> float:
    typing_step, hold = _native_annotation_timing(emoji)
    return (
        ANNOTATION_TYPING_DELAY
        + sum(len(line) for line in lines) * typing_step
        + max(0, len(lines) - 1) * ANNOTATION_LINE_GAP
        + hold
    )


def _native_annotation_emoji_time(annotation_start: float, lines: list[str]) -> float:
    return (
        annotation_start
        + ANNOTATION_TYPING_DELAY
        + sum(len(line) for line in lines) * CTA_TYPING_STEP
        + len(lines) * ANNOTATION_LINE_GAP
    )


def _native_annotation_window(
    start: float,
    duration: float,
    moment: str,
    lines: list[str],
    emoji: str | None,
    *,
    end_limit: float | None,
    scheduled_start: float | None,
) -> tuple[float, float]:
    display_duration = _native_annotation_duration(lines, emoji)
    if scheduled_start is not None:
        annotation_start = scheduled_start
    elif moment == "middle":
        annotation_start = start + duration / 2 - display_duration / 2
    elif moment == "end":
        annotation_start = start + duration - display_duration
    else:
        annotation_start = start
    annotation_start = max(start, annotation_start)
    annotation_end = annotation_start + display_duration
    if end_limit is not None:
        annotation_end = min(annotation_end, end_limit)
    return annotation_start, max(annotation_start, annotation_end)


def _native_time_window(start: float, end: float) -> str:
    return f"gte(t,{start:.3f})*lt(t,{end:.3f})"


def _native_filter_path(path: Path) -> str:
    return path.as_posix().replace(":", r"\:").replace("'", r"\'")


def _native_typing_annotation_filters(
    graph: list[str],
    source: str,
    annotation_index: int,
    lines: list[str],
    annotation_start: float,
    annotation_end: float,
    emoji: str | None,
    emoji_input_index: int | None,
) -> str:
    """Restaura o layout aprovado: blur do quadro e digitação amarela Impact."""
    current = source
    cursor = annotation_start + ANNOTATION_TYPING_DELAY
    typing_step, _ = _native_annotation_timing(emoji)
    line_centers = ("h/2-52", "h/2+52") if len(lines) == 2 else ("h/2",)
    for line_index, (line, y_center) in enumerate(zip(lines, line_centers, strict=True)):
        for char_count in range(1, len(line) + 1):
            next_cursor = cursor + typing_step
            output = f"[annotation_{annotation_index}_typed_{line_index}_{char_count}]"
            graph.append(
                f"{current}drawtext=fontfile='{_native_filter_path(ANNOTATION_FONT)}':"
                f"text='{_escape_drawtext(line[:char_count])}':fontcolor=0xFFD429:fontsize=102:"
                "borderw=5:bordercolor=black@0.96:"
                f"x=(w-text_w)/2:y={y_center}-text_h/2:"
                f"enable='{_native_time_window(cursor, next_cursor)}'{output}"
            )
            current = output
            cursor = next_cursor
        output = f"[annotation_{annotation_index}_line_{line_index}]"
        graph.append(
            f"{current}drawtext=fontfile='{_native_filter_path(ANNOTATION_FONT)}':"
            f"text='{_escape_drawtext(line)}':fontcolor=0xFFD429:fontsize=102:"
            "borderw=5:bordercolor=black@0.96:"
            f"x=(w-text_w)/2:y={y_center}-text_h/2:"
            f"enable='{_native_time_window(cursor, annotation_end)}'{output}"
        )
        current = output
        cursor += ANNOTATION_LINE_GAP
    if emoji:
        if emoji_input_index is None:
            raise ValueError(f"O emoji {emoji!r} não possui um sticker visual configurado.")
        sticker = f"[annotation_{annotation_index}_sticker]"
        output = f"[annotation_{annotation_index}_emoji]"
        graph.append(
            f"[{emoji_input_index}:v]format=rgba,scale=-1:{EMOJI_STICKER_HEIGHT}{sticker}"
        )
        graph.append(
            f"{current}{sticker}overlay=x='{EMOJI_STICKER_X}':y='(main_h-overlay_h)/2':format=auto:"
            f"enable='{_native_time_window(cursor, annotation_end)}'{output}"
        )
        current = output
    return current


def _native_required_stickers(annotations: list[tuple[list[str], float, float, str | None]]) -> dict[str, Path]:
    """Valida o catálogo visual antes de o FFmpeg começar a renderizar."""
    emojis = sorted({emoji for _, _, _, emoji in annotations if emoji})
    unsupported = [emoji for emoji in emojis if emoji not in EMOJI_STICKERS]
    if unsupported:
        raise ValueError(
            "Emoji sem sticker 3D aprovado: " + ", ".join(unsupported)
            + ". Adicione um PNG em workspace/assets/horizontal/overlays/emoji_stickers antes de renderizar."
        )
    missing = [emoji for emoji in emojis if not EMOJI_STICKERS[emoji].is_file()]
    if missing:
        raise FileNotFoundError("Sticker de emoji ausente: " + ", ".join(missing))
    return {emoji: EMOJI_STICKERS[emoji] for emoji in emojis}


def _native_render_sfx_tracks(
    events: list[tuple[str, float]],
    visual_duration: float,
    directory: Path,
) -> list[tuple[Path, float]]:
    """Cria uma única faixa SFX contínua sem grafo áudio global gigante.

    Cada bloco de trinta segundos é misturado isoladamente e os blocos são
    concatenados. Isso preserva o segundo editorial exato de cada efeito sem
    depender de ``-itsoffset``/PTS, que o ``amix`` normaliza internamente.
    """
    buckets: dict[int, list[tuple[str, float]]] = {}
    for effect, at in events:
        if at >= visual_duration:
            continue
        safe_at = max(0.0, at)
        buckets.setdefault(int(safe_at // SFX_CHUNK_SECONDS), []).append((effect, safe_at))
    if not buckets:
        return []

    directory.mkdir(parents=True, exist_ok=True)
    chunk_paths: list[Path] = []
    chunk_count = math.ceil(visual_duration / SFX_CHUNK_SECONDS)
    for bucket in range(chunk_count):
        offset = bucket * SFX_CHUNK_SECONDS
        chunk_duration = min(SFX_CHUNK_SECONDS, visual_duration - offset)
        grouped_events = buckets.get(bucket, [])
        if not grouped_events:
            output = directory / f"sfx_{bucket:03d}.flac"
            _run_compositor([
                str(FFMPEG), "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                "-filter_complex_threads", "1", "-filter_threads", "1",
                "-af", f"atrim=duration={chunk_duration:.6f}", "-c:a", "flac", "-compression_level", "5", str(output),
            ])
            chunk_paths.append(output)
            continue

        batch_paths: list[Path] = []
        batches = (grouped_events[index:index + MAX_SFX_EVENTS_PER_CHUNK] for index in range(0, len(grouped_events), MAX_SFX_EVENTS_PER_CHUNK))
        for batch_index, batch in enumerate(batches, start=1):
            output = directory / f"sfx_{bucket:03d}_batch_{batch_index:02d}.flac"
            inputs: list[str] = []
            graph: list[str] = []
            labels: list[str] = []
            for input_index, (effect, at) in enumerate(batch):
                source = _NATIVE_SOUND_EFFECTS[effect]
                lead = _NATIVE_SOUND_LEAD.get(effect, 0.0)
                duration = _NATIVE_SOUND_SECONDS.get(effect, 0.9)
                volume = _NATIVE_SOUND_VOLUMES.get(effect, 0.46)
                fade_seconds = min(_NATIVE_SOUND_FADE_OUT.get(effect, 0.0), duration)
                fade = (
                    f",afade=t=out:st={duration - fade_seconds:.3f}:d={fade_seconds:.3f}"
                    if fade_seconds > 0 else ""
                )
                local_start = at - offset
                label = f"[sfx_{bucket}_{batch_index}_{input_index}]"
                inputs.extend(["-i", str(source)])
                graph.append(
                    f"[{input_index}:a]aresample=48000,atrim=start={lead:.3f}:end={lead + duration:.3f},"
                    f"volume={volume:.2f}{fade},adelay={round(local_start * 1000)}:all=1{label}"
                )
                labels.append(label)
            graph.append(
                "".join(labels)
                + f"amix=inputs={len(labels)}:duration=longest:normalize=0,apad,atrim=duration={chunk_duration:.6f},"
                "aformat=sample_fmts=s16:sample_rates=48000:channel_layouts=stereo[audio]"
            )
            _run_compositor([
                str(FFMPEG), "-y", *inputs,
                "-filter_complex_threads", "1", "-filter_threads", "1",
                "-filter_complex", ";".join(graph), "-map", "[audio]",
                "-c:a", "flac", "-compression_level", "5", str(output),
            ])
            batch_paths.append(output)

        chunk = directory / f"sfx_{bucket:03d}.flac"
        if len(batch_paths) == 1:
            batch_paths[0].replace(chunk)
        else:
            inputs = [part for path in batch_paths for part in ("-i", str(path))]
            labels = "".join(f"[{index}:a]" for index in range(len(batch_paths)))
            _run_compositor([
                str(FFMPEG), "-y", *inputs,
                "-filter_complex_threads", "1", "-filter_threads", "1",
                "-filter_complex", f"{labels}amix=inputs={len(batch_paths)}:duration=first:normalize=0,atrim=duration={chunk_duration:.6f},"
                "aformat=sample_fmts=s16:sample_rates=48000:channel_layouts=stereo[audio]",
                "-map", "[audio]", "-c:a", "flac", "-compression_level", "5", str(chunk),
            ])
            for path in batch_paths:
                path.unlink(missing_ok=True)
        chunk_paths.append(chunk)

    manifest = directory / "sfx_timeline.ffconcat"
    manifest.write_text(
        "ffconcat version 1.0\n" + "".join(
            f"file '{path.resolve().as_posix().replace("'", r"'\\\\''")}'\n" for path in chunk_paths
        ),
        encoding="utf-8",
    )
    timeline = directory / "sfx_timeline.flac"
    _run_compositor([
        str(FFMPEG), "-y", "-safe", "0", "-f", "concat", "-i", str(manifest),
        "-c:a", "flac", "-compression_level", "5", str(timeline),
    ])
    return [(timeline, 0.0)]


def _native_finalize(
    visual_manifest: Path,
    narration: Path,
    music: Path,
    annotations: list[tuple[list[str], float, float, str | None]],
    sfx_tracks: list[tuple[Path, float]],
    narration_seconds: float,
    visual_duration: float,
    output: Path,
    filter_script: Path,
) -> None:
    """Mistura voz, trilha e SFX pelo compositor nativo do backend."""
    if visual_duration < narration_seconds:
        raise ValueError("A linha do tempo visual não pode terminar antes da narração.")
    audio_padding = visual_duration - narration_seconds
    graph: list[str] = []
    video = "[0:v]"
    stickers = _native_required_stickers(annotations)
    sticker_input_indices = {
        emoji: 3 + len(sfx_tracks) + index
        for index, emoji in enumerate(stickers)
    }
    if annotations:
        if not ANNOTATION_FONT.is_file():
            raise FileNotFoundError("A fonte Impact para as anotações não está disponível.")
        for index, (lines, start, end, emoji) in enumerate(annotations):
            base = f"[annotation_base_{index}]"
            blur = f"[annotation_blur_{index}]"
            blurred = f"[annotation_layer_{index}]"
            enabled = _native_time_window(start, end)
            graph.append(f"{video}split=2{base}[annotation_source_{index}]")
            graph.append(f"[annotation_source_{index}]boxblur=22:6:enable='{enabled}'{blur}")
            graph.append(f"{base}{blur}overlay=0:0:enable='{enabled}'{blurred}")
            video = _native_typing_annotation_filters(
                graph, blurred, index, lines, start, end, emoji,
                sticker_input_indices.get(emoji),
            )
    graph.append(f"{video}trim=duration={visual_duration:.6f},format=yuv420p[video]")

    if sfx_tracks:
        labels: list[str] = []
        for index, (_, offset) in enumerate(sfx_tracks, start=3):
            label = f"[sfx_track_{index}]"
            # ``-itsoffset`` é normalizado pelo demuxer quando a entrada passa
            # por filtros. O deslocamento precisa viver no próprio grafo para
            # cada bloco de SFX continuar no segundo editorial correto.
            graph.append(f"[{index}:a]aresample=48000,asetpts=PTS+{offset:.6f}/TB{label}")
            labels.append(label)
        graph.append("".join(labels) + f"amix=inputs={len(labels)}:duration=longest:normalize=0[sfx]")
    else:
        graph.append("anullsrc=r=48000:cl=stereo,atrim=0:0[sfx]")
    graph.extend([
        f"[1:a]aresample=48000,{VOICE_MASTERING_FILTER},apad=pad_dur={audio_padding:.6f},asplit=2[voice][voice_key]",
        "[2:a]aresample=48000,volume=0.15[music]",
        "[music][voice_key]sidechaincompress=threshold=0.035:ratio=8:attack=20:release=250[ducked]",
        f"[voice][ducked][sfx]amix=inputs=3:duration=first:normalize=0[mix];[mix]alimiter=limit={FINAL_AUDIO_LIMIT:.2f}[audio]",
    ])
    filter_script.write_text(";".join(graph), encoding="utf-8")
    inputs = ["-safe", "0", "-f", "concat", "-i", str(visual_manifest), "-i", str(narration), "-i", str(music)]
    for track, _ in sfx_tracks:
        inputs.extend(["-i", str(track)])
    for sticker in stickers.values():
        inputs.extend(["-loop", "1", "-framerate", str(FPS), "-i", str(sticker)])
    _run_compositor([
        str(FFMPEG), "-y", *inputs,
        "-filter_complex_threads", "1", "-filter_threads", "1",
        "-filter_buffered_frames", str(MAX_FILTER_BUFFERED_FRAMES),
        "-filter_complex_script", str(filter_script), "-map", "[video]", "-map", "[audio]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
        "-r", str(FPS), "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", str(output),
    ])


def _native_composite(
    script: Script,
    background: Path,
    job_dir: Path,
    scene_assets: Path,
    narration: Path,
    timing_payload: dict[str, object],
    music_name: str | None,
) -> Path:
    """Fluxo normal horizontal: compositor segmentado inteiramente no backend."""
    scenes = [scene for block in script.blocks for scene in block.scenes]
    raw_timing = timing_payload.get("scenes")
    if not isinstance(raw_timing, list) or len(raw_timing) != len(scenes):
        raise ValueError("Time-codes de cena inválidos para o compositor horizontal.")
    timing = [entry for entry in raw_timing if isinstance(entry, dict)]
    if len(timing) != len(scenes):
        raise ValueError("Time-codes de cena inválidos para o compositor horizontal.")
    starts = [float(entry["start"]) for entry in timing]
    speech = [float(entry["duration"]) for entry in timing]
    narration_seconds = float(timing_payload["narration_duration"])
    if narration_seconds <= 0 or narration_seconds > MAX_HORIZONTAL_NARRATION_SECONDS:
        raise ValueError("A duração da narração está fora do limite seguro da esteira horizontal.")
    if starts[0] < 0 or any(next_start <= start for start, next_start in zip(starts, starts[1:])):
        raise ValueError("Os time-codes de cena precisam ser estritamente crescentes.")
    if any(duration <= 0 for duration in speech) or starts[-1] >= narration_seconds:
        raise ValueError("Os time-codes de cena não cabem na duração da narração.")
    if any(start + duration > narration_seconds + 1 / FPS for start, duration in zip(starts, speech)):
        raise ValueError("A duração acústica de uma cena ultrapassa a narração medida.")
    annotations, events = _native_annotation_plan(script, timing)
    # Valida antes de iniciar os segmentos 1080p: um emoji sem asset não pode
    # desperdiçar minutos de renderização para só falhar na finalização.
    _native_required_stickers(annotations)
    annotation_end = max((end for _, _, end, _ in annotations), default=narration_seconds)
    sound_end = max(
        (at + _NATIVE_SOUND_SECONDS.get(effect, 0.9) for effect, at in events),
        default=narration_seconds,
    )
    # A última arte também segura o fim de uma anotação ou efeito sonoro;
    # assim ``-shortest`` nunca corta uma campainha/typing no quadro final.
    tail_seconds = max(0.0, annotation_end - narration_seconds, sound_end - narration_seconds)
    visual_start_frames = [_nearest_frame(start) for start in starts]
    visual_start_frames[0] = 0
    if any(next_frame <= frame for frame, next_frame in zip(visual_start_frames, visual_start_frames[1:])):
        raise ValueError("Os time-codes acústicos ficaram curtos demais para a grade de 24 fps.")
    narration_frames = _frames_for_duration(narration_seconds)
    visual_total_frames = _frames_for_duration(narration_seconds + tail_seconds)
    if narration_frames <= visual_start_frames[-1]:
        raise ValueError("A última cena não cabe na grade visual da narração.")
    visual_tail_frames = visual_total_frames - narration_frames
    clip_frames = [
        max(
            _frames_for_duration(speech[index] + TRANSITION_SECONDS),
            visual_start_frames[index + 1] - visual_start_frames[index] + TRANSITION_FRAMES,
        )
        if index < len(scenes) - 1
        else narration_frames - visual_start_frames[index]
        for index in range(len(scenes))
    ]
    if any(frames <= 0 for frames in clip_frames):
        raise ValueError("Uma cena ficou curta demais para a composição horizontal.")
    clip_durations = [frames / FPS for frames in clip_frames]
    visual_starts = [frame / FPS for frame in visual_start_frames]
    transition_starts = [
        (visual_start_frames[index + 1] - visual_start_frames[index]) / FPS
        if index < len(scenes) - 1
        else 0.0
        for index in range(len(scenes))
    ]
    visual_tail_seconds = visual_tail_frames / FPS
    visual_duration = visual_total_frames / FPS
    modes = _layout_modes(scenes)
    exits = _transition_directions(scenes)
    clips = _native_render_scene_clips(scenes, job_dir / "cenas_base", scene_assets, modes, clip_frames)
    scene_paths = list(clips)
    canvas_paths: dict[int, Path] = {}
    segment_paths: list[Path] = []
    for number, segment in enumerate(_build_render_segments(visual_start_frames, visual_total_frames), start=1):
        render_end = segment.handoff_index if segment.handoff_index is not None else segment.end_index
        for index in range(segment.start_index, render_end + 1):
            canvas_paths[index] = _native_canvas_scene(
                index, scenes, clips, job_dir / "cenas_prontas", background, modes, exits,
                visual_starts, transition_starts, clip_durations, script.background_animation, visual_tail_seconds,
            )
            scene_paths[index] = canvas_paths[index]
        segment_paths.append(_native_render_segment(segment, number, job_dir / "segmentos", scene_paths, visual_starts, modes, exits))
        keep_from = segment.handoff_index if segment.handoff_index is not None else len(clips)
        for index in range(keep_from):
            for path in {clips[index], canvas_paths.get(index)}:
                if path is not None and path.is_file():
                    path.unlink()

    visual_manifest = _native_segment_manifest(segment_paths, job_dir)
    final = job_dir / f"{_slug(script.title)}.mp4"
    sfx_directory = job_dir / "sfx"
    music_directory = job_dir / "trilha"
    sfx_tracks = _native_render_sfx_tracks(events, visual_duration, sfx_directory)
    music_bed = _native_looped_music_bed(_native_music_path(music_name), visual_duration, music_directory)
    _native_finalize(
        visual_manifest, narration, music_bed, annotations, sfx_tracks,
        narration_seconds, visual_duration, final, job_dir / "filtros_renderizacao.ffscript",
    )
    if abs(_duration(final) - visual_duration) > 2 / FPS:
        raise RuntimeError("A entrega final não respeitou a duração da linha do tempo visual.")
    for path in [*segment_paths, visual_manifest]:
        if path is None:
            continue
        if path.is_file():
            path.unlink()
    for directory in (job_dir / "segmentos", job_dir / "cenas_base", job_dir / "cenas_prontas", sfx_directory, music_directory):
        if directory.is_dir():
            shutil.rmtree(directory)
    return final


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
    progress = f"(on/{max(1, _frames_for_duration(seconds) - 1)})"
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
        f"scale={WIDTH}:{HEIGHT}:flags=lanczos,fps={FPS},settb=1/{FPS},setpts=PTS-STARTPTS"
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
    """Renderiza pelo compositor horizontal nativo do backend.

    A voz vem exclusivamente de ``script.voice``, validada junto do JSON. A
    composição segmenta as cenas para manter o uso de memória estável, usa
    trilha com sidechain e mantém os efeitos declarados no roteiro. A
    composição é executada diretamente por este módulo do backend.
    """
    log = job_logger or logging.getLogger(__name__)
    if not background.is_file():
        raise FileNotFoundError(f"Imagem de fundo não encontrada: {background.name}")

    scenes = [scene for block in script.blocks for scene in block.scenes]
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

        _report(progress_callback, 30, "Renderizando cenas, trilha e transições em fragmentos")
        composed = _native_composite(
            script,
            background,
            job_dir,
            scene_asset_dir,
            timeline_narration,
            timing_payload,
            music_name,
        )

        if timing_file.is_file():
            timing_file.unlink()
        _report(progress_callback, 92, "Publicando o vídeo final")
        output = _publish_output(composed, script.title, job_dir.name)
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
                "renderer": "backend_segmented_horizontal_compositor",
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
