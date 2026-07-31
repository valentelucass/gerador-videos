"""Renderizador horizontal com cartões e cenas fullscreen intercaladas."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
import re
import shutil
import subprocess
import unicodedata
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import ContextVar, copy_context
from dataclasses import dataclass
from logging import Logger
from pathlib import Path
from tempfile import mkdtemp

from ..config import FINAL_OUTPUT_DIR, FFMPEG, FFPROBE, IMAGE_DIR, MUSIC_DIR, RENDER_CACHE_DIR, SOUND_DIR
from ..models import Script
from ..services import VIDEO_EXTENSIONS, missing_scene_images, resolve_scene_image_sources, scene_asset_path
from .tts_neural import TTSNeuralEngine, WordBoundary

# A cadência de 30 fps é importante para o zoom do cartão: a 24 fps a borda
# percorria quatro ou mais pixels por amostra e aparentava vibrar, mesmo sem
# frames descartados. Os time-codes continuam quantizados uma única vez.
FPS = 30
WIDTH, HEIGHT = 1920, 1080
CARD_W, CARD_H = 1500, 844
CARD_RADIUS = 48
# A sombra continua sendo a mesma sombra discreta deslocada para baixo/direita,
# mas agora ganha área transparente ao redor. Antes o blur era calculado dentro
# da própria caixa do cartão e era cortado exatamente nas bordas inferior e
# lateral, criando a linha dura que aparecia em alguns frames.
CARD_SHADOW_PADDING = 32
CARD_SHADOW_OFFSET_X = 14
CARD_SHADOW_OFFSET_Y = 17
# O cartão cresce inteiro antes de sair de cena. A transição parte exatamente
# deste tamanho para não haver o "pulo" de escala no primeiro quadro. A
# composição aplica essa escala por transformação subpixel, não por degraus de
# largura/altura inteiras.
CARD_FOCUS_ZOOM = 1.120
# O cartão primeiro repousa, então aproxima em uma única rampa contínua e
# permanece focado antes da próxima passagem. Nunca há dois zooms concorrendo.
CARD_FOCUS_DELAY_SECONDS = 0.72
CARD_FOCUS_SECONDS = 1.72
CARD_FOCUS_HOLD_SECONDS = 0.62
CARD_FOCUS_PROBABILITY = 0.56
CARD_BLUR_AT_REST = 0.00
CARD_BLUR_AT_FOCUS = 0.84
FULLSCREEN_RATIO = 0.40
MAX_FULLSCREEN_RUN = 2
MAX_CARD_RUN = 3
# Mantemos as durações editoriais anteriores em uma grade de 30 fps inteira.
TRANSITION_FRAMES = 12
TRANSITION_SECONDS = TRANSITION_FRAMES / FPS
# Cartões não usam xfade: o anterior é sugado e o próximo ocupa o espaço
# liberado. A janela um pouco maior dá tempo para ambos se moverem sem que as
# caixas se encontrem, mantendo o mesmo ritmo aprovado no preview.
CARD_TRANSITION_FRAMES = 22
CARD_TRANSITION_SECONDS = CARD_TRANSITION_FRAMES / FPS
# No fim da troca cartão→cartão, a caixa atual é absorvida pelo lado de saída.
# A escala final menor deixa a passagem mais decidida sem encurtar a janela.
CARD_EXIT_ZOOM = 0.580
# A cadência-alvo é curta, mas pequenas variações da voz neural são normais.
# Até 10,5 s a mesma arte permanece em tela; acima disso o bloco ainda precisa
# ser dividido para preservar a retenção e o ritmo documental.
MAX_SCENE_ACOUSTIC_SECONDS = 9.0
# Um B-roll pode ser desacelerado para acompanhar a fala, mas nunca pode ser
# prolongado clonando o último quadro. O limite de 2,2× acomoda clipes curtos
# na prévia aprovada, inclusive quando a janela de transição amplia a cena.
# Se ainda for curto, a curadoria humana precisa fornecer outra mídia.
MAX_BROLL_SLOWDOWN = 2.20
# O produto foi dimensionado para narrativas de até vinte minutos. Acima disso
# a fila, o espaço temporário e a revisão humana deixam de ter a mesma
# previsibilidade; o operador deve dividir o roteiro em episódios.
MAX_HORIZONTAL_NARRATION_SECONDS = 20 * 60
# O backend limita cada execução pesada a uma janela curta. A duração total do
# vídeo não é limitada por essas constantes; apenas o tamanho de cada grafo.
# As telas já carregam as entradas/saídas dos cartões. A composição final do
# trecho só concatena MP4s finitos, portanto seis cenas mantêm baixa a
# sobrecarga de processos sem reintroduzir a cadeia cumulativa de xfade.
MAX_SCENES_PER_SEGMENT = 6
MAX_SEGMENT_SECONDS = 45.0
MAX_FILTER_BUFFERED_FRAMES = 128
# SFX também são montados por janelas: nunca existe um ``asplit`` global com
# centenas de sons aguardando offsets de muitos minutos.
SFX_CHUNK_SECONDS = 30.0
MAX_SFX_EVENTS_PER_CHUNK = 24
# A trilha não pode reiniciar em corte seco. A sobreposição é longa o bastante
# para mascarar a troca sem transformar duas músicas em uma massa sonora.
MUSIC_LOOP_CROSSFADE_SECONDS = 1.2
# Uma recodificação AAC pode variar alguns milissegundos por atraso de codec,
# mas jamais pode encurtar a música de forma material. Esta trava impede que
# uma futura alteração volte a descartar pausas internas de faixas importadas.
MUSIC_CYCLE_DURATION_TOLERANCE_SECONDS = 0.25
# O Windows impõe um limite para o tamanho da linha de comando. Um vídeo longo
# com uma faixa curta não pode passar uma entrada FFmpeg por repetição de uma
# só vez; a trilha é reduzida em árvores de blocos deste tamanho.
MUSIC_LOOP_MAX_INPUTS_PER_COMMAND = 12
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
# A trilha precisa continuar perceptível durante a fala. A combinação anterior
# (ganho 0.26, threshold muito baixo e razão 8:1) reduzia uma música normal a
# um nível praticamente inaudível em narrativas sem pausas. A amostra aprovada
# ainda deixava a voz dominante demais; aumentamos a cama só o necessário e
# deixamos o ducking atuar apenas quando a voz realmente sobe.
MUSIC_BED_VOLUME = 0.42
MUSIC_DUCKING_THRESHOLD = 0.13
MUSIC_DUCKING_RATIO = 1.7
MUSIC_DUCKING_ATTACK_MS = 25
MUSIC_DUCKING_RELEASE_MS = 320
# Os efeitos precisam aparecer um pouco mais à frente sem disparar o limiter
# com a voz. Aplicar o mesmo ganho a todos preserva as diferenças editoriais
# entre os volumes individuais abaixo.
SFX_VOLUME_BOOST = 1.12
# A RX 7600 disponível nesta estação possui AMF validado pelo FFmpeg. O encoder
# tira a codificação H.264 da CPU, enquanto os filtros continuam no CPU. Quatro
# threads de filtro deixam espaço para o sistema e evitam grafos 1080p
# excessivamente paralelos em renderizações longas.
VIDEO_FILTER_THREADS = max(2, min(4, os.cpu_count() or 4))
# Os filtros ``perspective``/alpha dos cartões são majoritariamente CPU e cada
# processo FFmpeg usa pouco mais de um núcleo. Dois processos independentes
# aproveitam melhor o Ryzen sem abrir sessões AMF demais nem dobrar a memória
# de trabalho para um nível arriscado. A fila é deliberadamente limitada: a
# estabilidade visual e de driver vale mais que paralelismo irrestrito.
SCENE_RENDER_WORKERS = 2
# Segmentos só dependem das cenas já prontas e escrevem arquivos distintos.
# Reaproveitamos o mesmo teto para não criar mais de duas sessões AMF em
# paralelo em nenhuma etapa do job.
SEGMENT_RENDER_WORKERS = SCENE_RENDER_WORKERS
VIDEO_ENCODER_ARGS = (
    "-c:v", "h264_amf", "-usage", "transcoding", "-quality", "speed",
    "-rc", "cqp", "-qp_i", "20", "-qp_p", "22", "-qp_b", "24",
    "-pix_fmt", "yuv420p",
)
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
_COMPOSITOR_LOGGER: ContextVar[Logger | None] = ContextVar("horizontal_compositor_logger", default=None)


@dataclass(frozen=True)
class AnnotationTextStyle:
    """Família tipográfica aprovada para annotations do vídeo horizontal."""

    font_path: Path
    ass_font_name: str
    font_size: int
    bold: int
    font_color: str
    outline_color: str
    outline_width: int
    shadow: int


@dataclass(frozen=True)
class RenderSegment:
    """Faixa visual contígua para concatenação de telas finalizadas."""

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


@dataclass(frozen=True)
class RenderPart:
    """Fonte finita e, opcionalmente, a janela de quadros a aproveitar.

    Os corpos das cenas já existem como MP4s normalizados. Recodificá-los em
    outro MP4 apenas para aplicar ``trim`` e depois recodificá-los outra vez no
    concat desperdiçava uma passagem 1080p completa. A janela permanece no
    grafo do concat, que já precisa recodificar para normalizar o PTS entre
    trechos AMF.
    """

    source: Path
    start_frame: int = 0
    end_frame: int | None = None


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


def _transition_directions(scenes: list[object], *, seed_context: str = "") -> list[str]:
    """Sorteia de forma estável as saídas entre cartões consecutivos.

    A direção precisa variar de vídeo para vídeo, mas não pode mudar se um job
    for refeito. Por isso o sorteio usa IDs das cenas como semente. Transições
    envolvendo fullscreen continuam obedecendo ao contrato explícito do JSON.
    """
    modes = _layout_modes(scenes)
    seed_material = seed_context or "|".join(scene.id for scene in scenes)
    generator = random.Random(_seed(seed_material))
    directions: list[str] = []
    for index, scene in enumerate(scenes):
        card_to_card = (
            index < len(scenes) - 1
            and modes[index] == "card"
            and modes[index + 1] == "card"
        )
        directions.append(
            generator.choice(("to_left", "to_right"))
            if card_to_card
            else scene.transition.out
        )
    return directions


def _card_focus_plan(
    scenes: list[object], modes: list[str], *, seed_context: str = "",
) -> list[bool]:
    """Sorteia blocos editoriais de zoom, sem alternância mecânica 1 a 1."""
    material = seed_context or "|".join(scene.id for scene in scenes)
    generator = random.Random(_seed("card-focus|" + material))
    result = [False] * len(scenes)
    card_indices = [index for index, mode in enumerate(modes) if mode == "card"]
    focused = generator.random() < CARD_FOCUS_PROBABILITY
    position = 0
    while position < len(card_indices):
        # Dois e três cartões são os padrões mais comuns; um e quatro entram
        # ocasionalmente para evitar uma cadência reconhecível.
        run_length = generator.choices((1, 2, 3, 4), weights=(0.16, 0.38, 0.31, 0.15))[0]
        for index in card_indices[position:position + run_length]:
            result[index] = focused
        position += run_length
        focused = not focused
    return result


def _transition_frames(left_mode: str, right_mode: str) -> int:
    """Retorna a sobreposição em frames da fronteira visual."""
    return CARD_TRANSITION_FRAMES if left_mode == right_mode == "card" else TRANSITION_FRAMES


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
# A CTA precisa continuar legível depois da última letra. Um segundo deixava
# apenas ~0,65s de texto completo porque os 0,35s finais pertencem à rampa de
# saída do blur. 1,8s ainda cabe no intervalo acústico normal sem pausas.
CTA_POST_TYPING_HOLD = 1.80
# O convite já tem uma cena e anotação próprias; uma pausa longa depois dele
# soa como narração cortada. Mantemos apenas uma respiração editorial curta.
MAX_CTA_NARRATION_PAUSE = 0.20
# A tela de anotação é um elemento editorial próprio: mantém a digitação
# amarela, blur de fundo e posição aprovados, mas a família vem da escolha do
# projeto. Os arquivos são fontes presentes na instalação Windows suportada.
ANNOTATION_TEXT_STYLES: dict[str, AnnotationTextStyle] = {
    "impact": AnnotationTextStyle(Path(r"C:/Windows/Fonts/impact.ttf"), "Impact", 102, 0, "FFD429", "000000", 5, 0),
    "serif_vintage": AnnotationTextStyle(Path(r"C:/Windows/Fonts/georgia.ttf"), "Georgia", 94, 1, "F4D98A", "2C1D06", 3, 1),
    "minimalista": AnnotationTextStyle(Path(r"C:/Windows/Fonts/arial.ttf"), "Arial", 98, 1, "BFF7FF", "000000", 0, 0),
    # O FFmpeg desenha a chamada com uma serifada dourada legível; o aspecto
    # de constelação propriamente dito é instruído no lote do Flow, onde ele
    # faz parte da arte, não uma sobreposição posterior.
    "constelacao_dourada": AnnotationTextStyle(Path(r"C:/Windows/Fonts/georgia.ttf"), "Georgia", 94, 1, "FFD429", "3A2800", 1, 1),
    "impact_sem_borda": AnnotationTextStyle(Path(r"C:/Windows/Fonts/impact.ttf"), "Impact", 102, 0, "FF3DE8", "000000", 0, 0),
    "branco_limpo": AnnotationTextStyle(Path(r"C:/Windows/Fonts/arial.ttf"), "Arial", 96, 1, "F5F7FA", "000000", 0, 0),
    "neon_violeta": AnnotationTextStyle(Path(r"C:/Windows/Fonts/arial.ttf"), "Arial", 98, 1, "B56BFF", "130722", 3, 2),
    "coral_contorno": AnnotationTextStyle(Path(r"C:/Windows/Fonts/impact.ttf"), "Impact", 102, 0, "FF6B5F", "000000", 5, 0),
    "ouro_sem_contorno": AnnotationTextStyle(Path(r"C:/Windows/Fonts/georgia.ttf"), "Georgia", 94, 1, "FFE7A3", "000000", 0, 0),
    "prata_azul": AnnotationTextStyle(Path(r"C:/Windows/Fonts/segoeuib.ttf"), "Segoe UI", 96, 1, "DDEBFF", "1F3C66", 2, 1),
    "verde_lima": AnnotationTextStyle(Path(r"C:/Windows/Fonts/verdanab.ttf"), "Verdana", 92, 1, "A9FF58", "102800", 4, 0),
    "azul_eletrico": AnnotationTextStyle(Path(r"C:/Windows/Fonts/arialbd.ttf"), "Arial", 98, 1, "46B8FF", "001C35", 4, 1),
    "vermelho_alerta": AnnotationTextStyle(Path(r"C:/Windows/Fonts/impact.ttf"), "Impact", 102, 0, "FF3B30", "2B0000", 4, 0),
    "rosa_chiclete": AnnotationTextStyle(Path(r"C:/Windows/Fonts/arialbd.ttf"), "Arial", 96, 1, "FF8FCC", "3A0827", 3, 1),
    "laranja_energia": AnnotationTextStyle(Path(r"C:/Windows/Fonts/trebucbd.ttf"), "Trebuchet MS", 94, 1, "FFAA32", "3D1900", 4, 0),
    "cinza_aco": AnnotationTextStyle(Path(r"C:/Windows/Fonts/consolab.ttf"), "Consolas", 88, 1, "D7E1E8", "1C2A33", 3, 1),
    "azul_marinho": AnnotationTextStyle(Path(r"C:/Windows/Fonts/georgiab.ttf"), "Georgia", 94, 1, "94C7FF", "001B3D", 3, 1),
    "roxo_real": AnnotationTextStyle(Path(r"C:/Windows/Fonts/georgiab.ttf"), "Georgia", 94, 1, "D7B4FF", "2A0C4A", 3, 1),
    "verde_menta": AnnotationTextStyle(Path(r"C:/Windows/Fonts/segoeuib.ttf"), "Segoe UI", 96, 1, "7FFFD4", "00382B", 2, 1),
    "amarelo_retro": AnnotationTextStyle(Path(r"C:/Windows/Fonts/verdanab.ttf"), "Verdana", 91, 1, "F7E65D", "543F00", 3, 0),
}
_ANNOTATION_TEXT_STYLE: ContextVar[str] = ContextVar("horizontal_annotation_text_style", default="impact")


def _annotation_text_style() -> AnnotationTextStyle:
    key = _ANNOTATION_TEXT_STYLE.get()
    try:
        return ANNOTATION_TEXT_STYLES[key]
    except KeyError as exc:
        raise ValueError(f"Estilo de fonte de annotation inválido: {key!r}.") from exc


def _require_annotation_font() -> AnnotationTextStyle:
    style = _annotation_text_style()
    if not style.font_path.is_file():
        raise FileNotFoundError(f"A fonte selecionada para as anotações não está disponível: {style.font_path.name}.")
    return style
# Quando o roteiro usa um emoji que ainda não possui arte 3D curada, o
# renderizador preserva o conteúdo usando o emoji nativo do Windows em vez de
# cancelar um job longo. Os stickers aprovados abaixo continuam prioritários.
SYSTEM_EMOJI_FONT = Path(r"C:/Windows/Fonts/seguiemj.ttf")
# Stickers 3D preservam a linguagem visual do canal; a fonte do sistema é
# somente um fallback seguro para emojis ainda não curados neste diretório.
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
# O texto termina antes do efeito visual: isso permite que o fundo volte ao
# foco sem alongar a narração, a cena ou a duração final do vídeo.
ANNOTATION_BLUR_RAMP_SECONDS = 0.35
# Vinhetas editoriais aplicadas somente na imagem final; não deslocam voz,
# efeitos, cortes de cena ou a duração total da entrega.
OPENING_FADE_SECONDS = 1.10
CLOSING_FADE_SECONDS = 1.20

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
        cue_start = block_word_timings[index][cue_index].start if cue_index is not None else block_word_timings[index][0].start
        annotation_start = max(block_word_timings[index][0].start, cue_start - ANNOTATION_TYPING_DELAY)
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
                # O blur pode preparar a CTA um quarto de segundo antes, mas
                # a primeira letra aparece exatamente no cue acústico.
                annotation_start = max(start, block_word_timings[index][cue_index].start - ANNOTATION_TYPING_DELAY)
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
    """Cria cópias privadas das mídias sem tocar no JSON nem no acervo.

    Em vez de alterar o arquivo editorial, cada fonte enviada é copiada para
    um diretório efêmero do job preservando a extensão física. Assim dois jobs
    podem usar vínculos diferentes para ``cena_01.png`` ao mesmo tempo, sem
    sobrescrever os acervos de imagem ou vídeo.
    """
    resolved_sources = resolve_scene_image_sources(script, image_bindings)
    missing = missing_scene_images(script, resolved_sources)
    if missing:
        raise FileNotFoundError("Imagens de cena ausentes: " + ", ".join(missing))

    asset_dir = Path(mkdtemp(prefix=".assets_cenas_", dir=job_dir))
    try:
        for block in script.blocks:
            for scene in block.scenes:
                source_name = resolved_sources[scene.image]
                shutil.copy2(scene_asset_path(scene, source_name), asset_dir / source_name)
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
        # Em especial para ENOMEM, o stderr do FFmpeg não informa quais
        # inputs estavam em loop nem o filter_complex efetivo. Registrar a
        # linha completa somente na falha preserva o render.log como fonte de
        # diagnóstico sem despejar milhares de caracteres em jobs saudáveis.
        (_COMPOSITOR_LOGGER.get() or logging.getLogger(__name__)).error(
            "FFmpeg falhou (exit=%s). Comando efetivo:\n%s",
            result.returncode,
            subprocess.list2cmdline([command[0], "-hide_banner", "-loglevel", "error", *command[1:]]),
        )
        if "buffered frames" in detail.lower():
            detail = (
                "O compositor atingiu o limite seguro de frames em memória. "
                "O trabalho foi interrompido antes de esgotar a RAM.\n" + detail
            )
        raise RuntimeError(
            "Compositor horizontal não conseguiu renderizar o vídeo:\n"
            + detail[-4000:]
        )


PSYCHOLOGY_LITHOGRAPH_MARKER = "litografia cosmica vintage"
PSYCHOLOGY_FRAME_EDGE_TRIM = 0.90


def _scene_needs_psychology_frame_cleanup(scene: object) -> bool:
    """Reconhece a direção que exige arte full-bleed, sem um novo campo JSON."""
    visual = getattr(scene, "visual", None)
    details = getattr(visual, "details", "") if visual is not None else ""
    normalized = "".join(
        character
        for character in unicodedata.normalize("NFKD", str(details)).casefold()
        if not unicodedata.combining(character)
    )
    return PSYCHOLOGY_LITHOGRAPH_MARKER in normalized


def _native_psychology_frame_trim_filter() -> str:
    """Remove a moldura que uma IA ocasionalmente desenha dentro da litografia.

    O crop é aplicado só em cenas marcadas como litografia psicológica. Assim
    não muda fotos/documentários normais e também corrige os assets já gerados.
    """
    retained = PSYCHOLOGY_FRAME_EDGE_TRIM
    return (
        f"crop=w='trunc(iw*{retained:.3f}/2)*2':h='trunc(ih*{retained:.3f}/2)*2':"
        "x='(iw-ow)/2':y='(ih-oh)/2',"
    )


def _native_card_filter_chain(*, animate_image: bool = False, trim_outer_edges: bool = False) -> str:
    """Prepara o cartão; B-roll mantém seu movimento e fotos ficam estáveis."""
    motion = (
        # O zoom editorial já é aplicado ao cartão completo por ``perspective``
        # subpixel. A antiga rampa interna de 0,3% fazia o crop central andar
        # em pixels inteiros e a foto tremia dentro de uma caixa fluida.
        # Mantemos o zoompan central exigido para imagens físicas, porém em
        # escala neutra: não há um segundo movimento concorrente.
        "zoompan=z='1':"
        "x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:"
        f"s={CARD_W}x{CARD_H}:fps={FPS},"
        if animate_image
        else ""
    )
    edge_trim = _native_psychology_frame_trim_filter() if trim_outer_edges else ""
    return (
        f"{edge_trim}scale={CARD_W}:{CARD_H}:force_original_aspect_ratio=increase,crop={CARD_W}:{CARD_H},"
        # A entrada agora acontece pelo deslocamento físico do cartão. O fade
        # inicial escurecia seus primeiros quadros e parecia um piscar quando
        # o próximo cartão já vinha ocupando o lado livre.
        f"fps={FPS},{motion}setsar=1"
    )


def _native_card_filter(*, animate_image: bool = False, trim_outer_edges: bool = False) -> str:
    """Compatibilidade do filtro de cartão isolado usado em diagnósticos."""
    return f"[0:v]{_native_card_filter_chain(animate_image=animate_image, trim_outer_edges=trim_outer_edges)}[out]"


def _native_card_round_mask_expression(*, offset_x: int = 0, offset_y: int = 0) -> str:
    """Expressão alfa para os quatro cantos arredondados do cartão.

    ``offset_*`` permite desenhar o mesmo cartão em um canvas maior e
    transparente, necessário para que a penumbra da sombra não seja cortada
    nas quatro extremidades.
    """
    left = offset_x
    top = offset_y
    right_edge = offset_x + CARD_W - 1
    bottom_edge = offset_y + CARD_H - 1
    right = offset_x + CARD_W - CARD_RADIUS - 1
    bottom = offset_y + CARD_H - CARD_RADIUS - 1
    left_curve = offset_x + CARD_RADIUS
    top_curve = offset_y + CARD_RADIUS
    radius_squared = CARD_RADIUS * CARD_RADIUS
    rounded_rectangle = (
        f"if(lt(X\\,{left_curve})*lt(Y\\,{top_curve})\\,"
        f"if(lte((X-{left_curve})*(X-{left_curve})+(Y-{top_curve})*(Y-{top_curve})\\,{radius_squared})\\,255\\,0)\\,"
        f"if(gt(X\\,{right})*lt(Y\\,{top_curve})\\,"
        f"if(lte((X-{right})*(X-{right})+(Y-{top_curve})*(Y-{top_curve})\\,{radius_squared})\\,255\\,0)\\,"
        f"if(lt(X\\,{left_curve})*gt(Y\\,{bottom})\\,"
        f"if(lte((X-{left_curve})*(X-{left_curve})+(Y-{bottom})*(Y-{bottom})\\,{radius_squared})\\,255\\,0)\\,"
        f"if(gt(X\\,{right})*gt(Y\\,{bottom})\\,"
        f"if(lte((X-{right})*(X-{right})+(Y-{bottom})*(Y-{bottom})\\,{radius_squared})\\,255\\,0)\\,255))))"
    )
    bounds = f"gte(X\\,{left})*lte(X\\,{right_edge})*gte(Y\\,{top})*lte(Y\\,{bottom_edge})"
    return f"if({bounds}\\,{rounded_rectangle}\\,0)"


def _native_card_mask_asset(job_dir: Path) -> Path:
    """Gera uma máscara cinza reutilizável para arredondar cartões sem recodificá-los."""
    job_dir.mkdir(parents=True, exist_ok=True)
    output = job_dir / f"mascara_cartao_arredondada_{CARD_W}x{CARD_H}.png"
    if output.is_file():
        return output
    _run_compositor([
        str(FFMPEG), "-y", "-f", "lavfi", "-i", f"color=c=white:s={CARD_W}x{CARD_H}:r=1",
        "-vf", f"format=gray,geq=lum='{_native_card_round_mask_expression()}'",
        "-frames:v", "1", str(output),
    ])
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("Não foi possível preparar a máscara arredondada dos cartões.")
    return output


def _native_card_shadow_asset(
    job_dir: Path,
    *,
    padding: int = CARD_SHADOW_PADDING,
    offset_x: int = CARD_SHADOW_OFFSET_X,
    offset_y: int = CARD_SHADOW_OFFSET_Y,
) -> Path:
    """Materializa uma única vez a sombra arredondada dos cartões.

    O cartão sempre ocupa a mesma caixa opaca de 1500x844. Desfocar a própria
    mídia a cada quadro era, portanto, trabalho repetido: o resultado é
    invariavelmente a mesma sombra preta com bordas suaves. Um PNG com alpha
    preserva exatamente esse aspecto e elimina o ``boxblur`` de todos os
    segmentos.
    """
    job_dir.mkdir(parents=True, exist_ok=True)
    if padding < 18:
        raise ValueError("A sombra do cartão precisa de ao menos 18 px de margem para o blur.")
    shadow_w = CARD_W + 2 * padding
    shadow_h = CARD_H + 2 * padding
    output = job_dir / f"sombra_cartao_{shadow_w}x{shadow_h}_p{padding}_x{offset_x}_y{offset_y}.png"
    if output.is_file():
        return output
    _run_compositor([
        str(FFMPEG), "-y", "-f", "lavfi", "-i", f"color=c=black:s={shadow_w}x{shadow_h}:r=1",
        "-vf", (
            "format=rgba,"
            f"geq=r='0':g='0':b='0':a='{_native_card_round_mask_expression(offset_x=padding, offset_y=padding)}',"
            "colorchannelmixer=aa=0.42,boxblur=18:2"
        ),
        "-frames:v", "1", str(output),
    ])
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("Não foi possível preparar a sombra estática dos cartões.")
    return output


def _native_card_background_blur_asset(background: Path, job_dir: Path) -> Path:
    """Prepara o fundo discreto que fica fora do cartão.

    Não usamos o fundo nítido nessa área: grades, linhas retas e outros
    detalhes de fundos decorativos ficavam visíveis até encostar na borda
    arredondada do cartão, parecendo um contorno involuntário da própria arte.
    """
    job_dir.mkdir(parents=True, exist_ok=True)
    output = job_dir / "fundo_cartoes_borrado.png"
    if output.is_file():
        return output
    _run_compositor([
        str(FFMPEG), "-y", "-i", str(background),
        "-vf", (
            f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},"
            "boxblur=52:8,eq=brightness=-0.14:saturation=0.58"
        ),
        "-frames:v", "1", str(output),
    ])
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("Não foi possível preparar o fundo borrado dos cartões.")
    return output


def _native_card_focus_window(visible_seconds: float, entry_seconds: float) -> tuple[float, float] | None:
    """Retorna a janela repouso → zoom → repouso, ou ``None`` se for curta."""
    start = entry_seconds + CARD_FOCUS_DELAY_SECONDS
    end = start + CARD_FOCUS_SECONDS
    if end + CARD_FOCUS_HOLD_SECONDS > visible_seconds:
        return None
    return start, end


def _native_card_focus_progress(focus_start: float, focus_end: float, clock: str) -> str:
    """Progresso com easing cossenoidal para o zoom externo do cartão.

    A curva tem velocidade zero nos dois extremos: o cartão pode repousar e
    depois sair sem o tranco que uma rampa linear causava no primeiro quadro.
    """
    focus_seconds = focus_end - focus_start
    if focus_seconds <= 0:
        raise ValueError("Janela de zoom do cartão inválida.")

    phase = f"({clock}-{focus_start:.6f})/{focus_seconds:.6f}"
    return (
        f"if(lt({clock}\\,{focus_start:.6f})\\,0\\,"
        f"if(lt({clock}\\,{focus_end:.6f})\\,"
        f"0.5-0.5*cos(PI*({phase}))\\,1))"
    )


def _native_card_focus_expression(
    focus_start: float, focus_end: float, *, clock: str = "t",
) -> str:
    """Expressão de escala suave, com início e fim em quadros exatos."""
    zoom = f"1+{CARD_FOCUS_ZOOM - 1:.6f}*({_native_card_focus_progress(focus_start, focus_end, clock)})"
    return zoom


def _native_effective_card_focuses(
    planned: list[bool], modes: list[str], scene_durations: list[float],
) -> list[bool]:
    """Não agenda zoom quando a cena não comporta as três fases visuais."""
    effective = list(planned)
    for index, focused in enumerate(effective):
        if not focused or modes[index] != "card":
            continue
        entry_seconds = (
            TRANSITION_SECONDS if index and modes[index - 1] == "fullscreen"
            else CARD_TRANSITION_SECONDS if index and modes[index - 1] == "card"
            else 0.0
        )
        if _native_card_focus_window(scene_durations[index], entry_seconds) is None:
            effective[index] = False
    return effective


def _native_video_fullscreen_filter() -> str:
    """B-roll conserva o movimento original; Ken Burns é exclusivo de imagens."""
    return (
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},"
        f"fps={FPS},settb=1/{FPS},setpts=PTS-STARTPTS"
    )


def _native_background_filter(animation: str, frame_offset: int) -> str:
    # ``perspective`` expõe ``on`` e aceita coordenadas subpixel. O crop
    # arredonda X/Y para pixels inteiros; como esta animação desloca menos de
    # um pixel por quadro, ele criava degraus visíveis (parecia 5 fps). O
    # offset deixa o movimento contínuo entre cartões consecutivos.
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
            "interpolation=linear:eval=frame,crop=1920:1080"
        )
    if animation == "movimento_lateral":
        return (
            "scale=2048:1152:force_original_aspect_ratio=increase,crop=2048:1152,"
            "perspective="
            f"x0='64+28*sin(0.13*{frame}/{FPS})':y0='36':"
            f"x1='1984+28*sin(0.13*{frame}/{FPS})':y1='36':"
            f"x2='64+28*sin(0.13*{frame}/{FPS})':y2='1116':"
            f"x3='1984+28*sin(0.13*{frame}/{FPS})':y3='1116':"
            "interpolation=linear:eval=frame,crop=1920:1080"
        )
    if animation == "pulsacao":
        return (
            "scale=2048:1152:force_original_aspect_ratio=increase,crop=2048:1152,"
            "perspective="
            f"x0='64+11*sin(0.45*{frame}/{FPS})':y0='36+6*sin(0.45*{frame}/{FPS})':"
            f"x1='1984-11*sin(0.45*{frame}/{FPS})':y1='36+6*sin(0.45*{frame}/{FPS})':"
            f"x2='64+11*sin(0.45*{frame}/{FPS})':y2='1116-6*sin(0.45*{frame}/{FPS})':"
            f"x3='1984-11*sin(0.45*{frame}/{FPS})':y3='1116-6*sin(0.45*{frame}/{FPS})':"
            "interpolation=linear:eval=frame,crop=1920:1080"
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
        end_frame = (
            scene_start_frames[handoff] - scene_start_frames[start]
            if handoff is not None
            else visual_total_frames - scene_start_frames[start]
        )
        if end_frame <= 0:
            raise RuntimeError("A segmentação visual gerou um fragmento inválido.")
        result.append(RenderSegment(start, end, None, 0, end_frame))
        start = handoff if handoff is not None else len(scene_start_frames)
    return result


def _native_render_scene_clips(
    scenes: list[object],
    scene_dir: Path,
    source_dir: Path,
    modes: list[str],
    clip_frames: list[int],
    *,
    source_names: Mapping[str, str] | None = None,
    progress_callback: ProgressCallback | None = None,
    progress_start: int = 30,
    progress_end: int = 42,
) -> list[Path]:
    scene_dir.mkdir(parents=True, exist_ok=True)
    source_names = source_names or {}
    # O ponto de foco de fullscreen é editorial e precisa continuar estável
    # mesmo quando as cenas terminam fora de ordem na fila paralela.
    fullscreen_image_indices: dict[int, int] = {}
    next_fullscreen_image = 0
    for index, scene in enumerate(scenes):
        source_name = source_names.get(scene.image, scene.image)
        if (
            modes[index] == "fullscreen"
            and scene.tipo_midia != "video_generico"
            and Path(source_name).suffix.lower() not in VIDEO_EXTENSIONS
        ):
            fullscreen_image_indices[index] = next_fullscreen_image
            next_fullscreen_image += 1

    def render_one(index: int, scene: object, frames: int) -> Path:
        output = scene_dir / f"cena_{index + 1:03d}.mp4"
        source_name = source_names.get(scene.image, scene.image)
        source = source_dir / source_name
        target_seconds = frames / FPS
        is_video = scene.tipo_midia == "video_generico" or source.suffix.lower() in VIDEO_EXTENSIONS
        trim_outer_edges = not is_video and _scene_needs_psychology_frame_cleanup(scene)
        # Todo vídeo de cena, seja B-roll ou upload manual, é finito: nunca
        # clonamos seu último quadro. A redução pode estender o clipe até 2,2×,
        # conforme a prévia aprovada; se não bastar, o operador o substitui.
        video_time_scale = 1.0
        if is_video:
            source_seconds = _duration(source)
            if source_seconds <= 0:
                raise RuntimeError(f"B-roll inválido ou sem duração: {source.name}")
            minimum_source_seconds = target_seconds / MAX_BROLL_SLOWDOWN
            if source_seconds < minimum_source_seconds:
                raise ValueError(
                    f"Vídeo curto demais na cena {scene.id} ({source.name}): "
                    f"tem {source_seconds:.2f}s, mas precisa de ao menos "
                    f"{minimum_source_seconds:.2f}s para cobrir {target_seconds:.2f}s "
                    "sem congelar. Substitua o arquivo por um clipe mais longo."
                )
            video_time_scale = max(1.0, target_seconds / source_seconds)
        finite_tail = (
            f",trim=duration={target_seconds:.6f},setpts=PTS-STARTPTS"
            if is_video else ""
        )
        source_prefix = f"[0:v]setpts=PTS*{video_time_scale:.8f}," if is_video else "[0:v]"
        if modes[index] == "fullscreen":
            filter = (
                _native_video_fullscreen_filter()
                if is_video
                else _fullscreen_filter(fullscreen_image_indices[index], target_seconds, trim_outer_edges=trim_outer_edges)
            )
            filter_graph = f"{source_prefix}{filter}{finite_tail}[out]"
        else:
            # Fotos de cartão também precisam do Ken Burns previsto pelo
            # contrato horizontal. Fazê-lo aqui mantém cada encoder com um
            # único input finito, em vez de multiplicar frames no grafo do
            # segmento.
            filter_graph = (
                f"{source_prefix}{_native_card_filter_chain(animate_image=not is_video, trim_outer_edges=trim_outer_edges)}"
                f"{finite_tail}[out]"
            )
        input_args = ["-i", str(source)] if is_video else ["-loop", "1", "-framerate", str(FPS), "-i", str(source)]
        _run_compositor([
            str(FFMPEG), "-y", *input_args,
            "-filter_complex_threads", str(VIDEO_FILTER_THREADS), "-filter_threads", str(VIDEO_FILTER_THREADS),
            "-filter_complex", filter_graph, "-map", "[out]", "-an", "-frames:v", str(frames),
            *VIDEO_ENCODER_ARGS,
            "-r", str(FPS), str(output),
        ])
        return output

    workers = min(SCENE_RENDER_WORKERS, len(scenes))
    clips: list[Path | None] = [None] * len(scenes)
    if workers == 1:
        for index, (scene, frames) in enumerate(zip(scenes, clip_frames, strict=True)):
            clips[index] = render_one(index, scene, frames)
            _report(
                progress_callback,
                progress_start + round((progress_end - progress_start) * (index + 1) / max(1, len(scenes))),
                f"Normalizando cena {index + 1}/{len(scenes)}",
            )
    else:
        completed = 0
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="horizontal-scene") as executor:
            futures = {
                executor.submit(copy_context().run, render_one, index, scene, frames): index
                for index, (scene, frames) in enumerate(zip(scenes, clip_frames, strict=True))
            }
            try:
                for future in as_completed(futures):
                    index = futures[future]
                    clips[index] = future.result()
                    completed += 1
                    _report(
                        progress_callback,
                        progress_start + round((progress_end - progress_start) * completed / max(1, len(scenes))),
                        f"Normalizando cena {completed}/{len(scenes)}",
                    )
            except Exception:
                for pending in futures:
                    pending.cancel()
                raise
    if any(clip is None for clip in clips):
        raise RuntimeError("A fila de normalização não retornou todas as cenas.")
    return [clip for clip in clips if clip is not None]


def _native_render_scene_canvases(
    scenes: list[object],
    clips: list[Path],
    canvas_dir: Path,
    background: Path,
    card_background_blur: Path,
    card_shadow: Path,
    card_mask: Path,
    modes: list[str],
    card_focuses: list[bool],
    exits: list[str],
    scene_starts: list[float],
    scene_durations: list[float],
    clip_durations: list[float],
    animation: str,
    tail_seconds: float,
    *,
    progress_callback: ProgressCallback | None = None,
    progress_start: int = 42,
    progress_end: int = 54,
) -> list[Path]:
    """Prepara cartões em passes curtos antes da concatenação.

    Um trecho com seis cenas de cartão abria quatro fundos e quatro sombras
    em loop dentro do mesmo grafo de transições. Mesmo aparados por ``trim``,
    eles multiplicavam os buffers 1080p do framesync. Este passe mantém o
    desenho editorial de cada cartão, mas entrega ao segmento somente MP4s
    finitos de tela cheia. A sombra já é um PNG com alpha, portanto não há
    ``boxblur`` por frame nem o antigo canvas para cenas fullscreen.
    """
    canvas_dir.mkdir(parents=True, exist_ok=True)

    def render_one(scene_index: int) -> Path:
        source = clips[scene_index]
        scene_tail = tail_seconds if scene_index == len(scenes) - 1 else 0.0
        # Cada pré-clipe não final inclui a janela que se sobrepõe à
        # próxima cena. Eles são indispensáveis para reconstruir o xfade de
        # cartão/fullscreen fora do grafo cumulativo do segmento.
        duration = clip_durations[scene_index]
        padding = f",tpad=stop_mode=clone:stop_duration={scene_tail:.6f}" if scene_tail else ""
        if modes[scene_index] == "fullscreen" and not scene_tail:
            return source

        output = canvas_dir / f"cena_{scene_index + 1:03d}.mp4"
        if modes[scene_index] == "fullscreen":
            graph = (
                f"[0:v]settb=1/{FPS},setpts=PTS-STARTPTS{padding},"
                f"trim=duration={duration:.6f},format=yuv420p[out]"
            )
            command = [str(FFMPEG), "-y", "-i", str(source)]
        else:
            centered = "(main_w-overlay_w)/2"
            entry = centered
            entry_seconds = 0.0
            if scene_index and modes[scene_index - 1] == "fullscreen":
                entry_seconds = TRANSITION_SECONDS
                entry = (
                    f"main_w-(main_w-{centered})*t/{TRANSITION_SECONDS:.6f}"
                    if exits[scene_index - 1] == "to_left"
                    else f"-overlay_w+({centered}+overlay_w)*t/{TRANSITION_SECONDS:.6f}"
                )
            elif scene_index and modes[scene_index - 1] == "card":
                # O cartão entrante termina a coreografia dedicado no tamanho
                # padrão; só então começa o zoom de permanência.
                entry_seconds = CARD_TRANSITION_SECONDS
            exit_start = duration
            exiting = centered
            if scene_index < len(scenes) - 1 and modes[scene_index + 1] == "fullscreen":
                # Os últimos dez quadros pertencem à janela de xfade com o
                # fullscreen seguinte. O cartão começa a sair exatamente ali,
                # como fazia o compositor original.
                exit_start = max(TRANSITION_SECONDS, duration - TRANSITION_SECONDS)
                if exits[scene_index] == "to_left":
                    exiting = f"{centered}-({centered}+overlay_w)*(t-{exit_start:.6f})/{TRANSITION_SECONDS:.6f}"
                elif exits[scene_index] == "to_right":
                    exiting = f"{centered}+(main_w-{centered})*(t-{exit_start:.6f})/{TRANSITION_SECONDS:.6f}"
            card_x = f"if(lt(t,{TRANSITION_SECONDS:.6f}),{entry},if(lt(t,{exit_start:.6f}),{centered},{exiting}))"
            card_zoom = "1"
            # O fundo selecionado no painel precisa permanecer reconhecível no
            # vídeo. A moldura indesejada era o drawbox da própria mídia do
            # cartão (removido acima), não motivo para substituir a escolha do
            # usuário por uma cópia quase sem detalhes.
            background_graph = (
                f"[1:v]{_native_background_filter(animation, round(scene_starts[scene_index] * FPS))},"
                f"fps={FPS},settb=1/{FPS},setsar=1,trim=duration={duration:.6f},setpts=PTS-STARTPTS[bg]"
            )
            focus_window = _native_card_focus_window(scene_durations[scene_index], entry_seconds)
            if card_focuses[scene_index] and focus_window is not None:
                focus_start, focus_end = focus_window
                # ``perspective`` disponibiliza ``on`` (número do quadro),
                # não ``t``. A conversão explícita mantém a mesma rampa em
                # segundos que o restante do compositor usa.
                card_zoom = _native_card_focus_expression(
                    focus_start, focus_end, clock=f"(on/{FPS})",
                )
                # O zoom editorial sempre vem acompanhado de desfoque no
                # fundo: a arte avança e o cenário escolhido pelo operador
                # perde definição progressivamente. Fora desse estado, o
                # fundo permanece nítido e reconhecível.
                background_graph = (
                    f"[1:v]{_native_background_filter(animation, round(scene_starts[scene_index] * FPS))},"
                    f"fps={FPS},settb=1/{FPS},setsar=1,trim=duration={duration:.6f},setpts=PTS-STARTPTS[bg_sharp];"
                    f"[2:v]{_native_background_filter(animation, round(scene_starts[scene_index] * FPS))},"
                    f"fps={FPS},settb=1/{FPS},setsar=1,trim=duration={duration:.6f},setpts=PTS-STARTPTS,"
                    f"format=rgba,fade=t=in:st={focus_start:.6f}:d={focus_end - focus_start:.6f}:alpha=1,"
                    f"colorchannelmixer=aa={CARD_BLUR_AT_FOCUS:.3f}[bg_blur];"
                    "[bg_sharp][bg_blur]overlay=0:0:format=auto[bg]"
                )
            # O ``scale`` dinâmico precisava arredondar a largura de 1500 px
            # em cada quadro e recentrar a caixa já arredondada. Mesmo a 30
            # fps, isso fazia as bordas marcharem 1--2 pixels por vez. Ao
            # montar o cartão em um canvas RGBA fixo e ampliar seu viewport
            # por ``perspective``, a transformação preserva coordenadas
            # subpixel e faz a interpolação cúbica como o fullscreen.
            card_view_w = f"({WIDTH}/({card_zoom}))"
            card_view_h = f"({HEIGHT}/({card_zoom}))"
            card_view_x = f"({WIDTH}-({card_view_w}))/2"
            card_view_y = f"({HEIGHT}-({card_view_h}))/2"
            graph = ";".join([
                background_graph,
                f"[0:v]settb=1/{FPS},setpts=PTS-STARTPTS{padding},trim=duration={duration:.6f}[card_rgb]",
                f"[4:v]format=gray,settb=1/{FPS},setpts=PTS-STARTPTS,trim=duration={duration:.6f}[card_mask]",
                "[card_rgb][card_mask]alphamerge[card]",
                f"[3:v]settb=1/{FPS},setpts=PTS-STARTPTS,trim=duration={duration:.6f}[shadow_source]",
                f"color=c=black@0.0:s={WIDTH}x{HEIGHT}:r={FPS},format=rgba,"
                f"trim=duration={duration:.6f},setpts=PTS-STARTPTS[card_canvas]",
                f"[card_canvas][shadow_source]overlay=x='({card_x})-{CARD_SHADOW_PADDING}+{CARD_SHADOW_OFFSET_X}':"
                f"y='(main_h-overlay_h)/2-{CARD_SHADOW_PADDING}+{CARD_SHADOW_OFFSET_Y}':"
                "format=auto[card_shadow_layer]",
                f"[card_shadow_layer][card]overlay=x='{card_x}':y='(main_h-overlay_h)/2':format=auto,"
                "format=rgba[card_layer]",
                f"[card_layer]perspective=x0='{card_view_x}':y0='{card_view_y}':"
                f"x1='({card_view_x})+({card_view_w})':y1='{card_view_y}':"
                f"x2='{card_view_x}':y2='({card_view_y})+({card_view_h})':"
                f"x3='({card_view_x})+({card_view_w})':y3='({card_view_y})+({card_view_h})':"
                "sense=source:interpolation=cubic:eval=frame[card_zoomed]",
                "[bg][card_zoomed]overlay=0:0:format=auto,"
                f"trim=duration={duration:.6f},format=yuv420p[out]",
            ])
            command = [
                str(FFMPEG), "-y", "-i", str(source),
                "-loop", "1", "-framerate", str(FPS), "-i", str(background),
                "-loop", "1", "-framerate", str(FPS), "-i", str(card_background_blur),
                "-loop", "1", "-framerate", str(FPS), "-i", str(card_shadow),
                "-loop", "1", "-framerate", str(FPS), "-i", str(card_mask),
            ]
        _run_compositor([
            *command,
            "-filter_complex_threads", str(VIDEO_FILTER_THREADS), "-filter_threads", str(VIDEO_FILTER_THREADS),
            "-filter_complex", graph, "-map", "[out]", "-an", *VIDEO_ENCODER_ARGS,
            "-r", str(FPS), str(output),
        ])
        return output

    # Fullscreen sem cauda já é o próprio pré-clipe. Somente cartões (e a
    # rara última tela fullscreen com padding) precisam de compositor. Cada
    # tarefa lê/escreve arquivos distintos e só compartilha PNGs imutáveis,
    # portanto duas execuções são seguras e reduzem pela metade o gargalo
    # CPU que antes preparava os 67 cartões estritamente em série.
    task_indices = [
        index for index in range(len(scenes))
        if modes[index] == "card" or (index == len(scenes) - 1 and tail_seconds > 0)
    ]
    prepared: list[Path | None] = list(clips)
    workers = min(SCENE_RENDER_WORKERS, len(task_indices))
    if workers == 1:
        rendered_cards = 0
        for scene_index in task_indices:
            prepared[scene_index] = render_one(scene_index)
            if modes[scene_index] == "card":
                rendered_cards += 1
                _report(
                    progress_callback,
                    progress_start + round((progress_end - progress_start) * rendered_cards / max(1, modes.count("card"))),
                    f"Compondo cartão {rendered_cards}/{modes.count('card')}",
                )
    elif workers:
        rendered_cards = 0
        card_total = modes.count("card")
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="horizontal-card") as executor:
            futures = {
                executor.submit(copy_context().run, render_one, scene_index): scene_index
                for scene_index in task_indices
            }
            try:
                for future in as_completed(futures):
                    scene_index = futures[future]
                    prepared[scene_index] = future.result()
                    if modes[scene_index] == "card":
                        rendered_cards += 1
                        _report(
                            progress_callback,
                            progress_start + round((progress_end - progress_start) * rendered_cards / max(1, card_total)),
                            f"Compondo cartão {rendered_cards}/{card_total}",
                        )
            except Exception:
                for pending in futures:
                    pending.cancel()
                raise
    if any(path is None for path in prepared):
        raise RuntimeError("A fila de cartões não retornou todas as cenas preparadas.")
    return [path for path in prepared if path is not None]


def _native_render_card_to_card_transition(
    left_card: Path,
    right_card: Path,
    background: Path,
    card_background_blur: Path,
    card_shadow: Path,
    card_mask: Path,
    left_start_frame: int,
    animation: str,
    transition_start_frame: int,
    direction: str,
    left_focused: bool,
    output: Path,
) -> Path:
    """Compõe a passagem de cartões sem xfade nem sobreposição física.

    A saída usa os últimos 18 frames preparados do cartão atual e os primeiros
    18 do próximo. O atual reduz e é sugado para um lado; dois frames depois o
    próximo já entra pelo lado oposto no tamanho normal, preenchendo o espaço
    que ficou livre. Como o fundo nasce uma única vez no processo, ele apenas
    borra/desborra junto do zoom em vez de piscar entre dois canvases.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    exit_sign = -1 if direction != "to_right" else 1
    entry_sign = -exit_sign
    exit_frames = 17
    entry_start_frames = 3
    exit_seconds = exit_frames / FPS
    entry_start_seconds = entry_start_frames / FPS
    entry_seconds = CARD_TRANSITION_SECONDS - entry_start_seconds
    left_zoom = CARD_FOCUS_ZOOM if left_focused else 1.0
    left_blur = CARD_BLUR_AT_FOCUS if left_focused else CARD_BLUR_AT_REST
    def old_zoom(clock: str) -> str:
        return (
            f"if(lt({clock}\\,{exit_frames})\\,{left_zoom:.3f}-{left_zoom - CARD_EXIT_ZOOM:.3f}*"
            f"(0.5-0.5*cos(PI*{clock}/{exit_frames}))\\,{CARD_EXIT_ZOOM:.3f})"
        )

    # A sucção é curta e sai por completo da tela. Aqui usamos ``scale``
    # normal em vez de transformar um canvas RGBA inteiro com ``perspective``:
    # alguns drivers/versões do FFmpeg interpolam a borda transparente desse
    # canvas como uma faixa horizontal, produzindo um flash visual agressivo.
    # O zoom lento de permanência do cartão continua no caminho subpixel da
    # cena; esta escala final só trata a saída rápida para fora da tela.
    old_zoom_overlay = old_zoom("n")
    old_x = (
        f"if(lt(n\\,{exit_frames})\\,{exit_sign * 1600}*"
        f"(0.5-0.5*cos(PI*n/{exit_frames}))\\,{exit_sign * 3000})"
    )
    new_x = (
        f"if(lt(t\\,{entry_start_seconds:.6f})\\,{entry_sign * 3000}\\,"
        f"if(lt(t\\,{CARD_TRANSITION_SECONDS:.6f})\\,{entry_sign * 1700}*"
        f"(1-sin(PI*(t-{entry_start_seconds:.6f})/{2 * entry_seconds:.6f}))\\,0))"
    )
    blur_mix = (
        f"if(lt(T\\,{exit_seconds:.6f})\\,{left_blur:.3f}-"
        f"{left_blur - CARD_BLUR_AT_REST:.3f}*"
        f"(0.5-0.5*cos(PI*T/{exit_seconds:.6f}))\\,{CARD_BLUR_AT_REST:.3f})"
    )
    left_end_frame = left_start_frame + CARD_TRANSITION_FRAMES
    graph = ";".join([
        # A passagem parte do desfoque da cena que estava em foco e retorna ao
        # fundo nítido enquanto o cartão sai, preservando a mesma linguagem do
        # zoom sem alterar a escolha de fundo do projeto.
        f"[2:v]{_native_background_filter(animation, transition_start_frame)},"
        f"fps={FPS},settb=1/{FPS},setsar=1,trim=duration={CARD_TRANSITION_SECONDS:.6f},"
        "setpts=PTS-STARTPTS[card_transition_sharp]",
        f"[3:v]{_native_background_filter(animation, transition_start_frame)},"
        f"fps={FPS},settb=1/{FPS},setsar=1,trim=duration={CARD_TRANSITION_SECONDS:.6f},"
        "setpts=PTS-STARTPTS[card_transition_blur]",
        f"[card_transition_sharp][card_transition_blur]blend=all_expr='A*(1-({blur_mix}))+B*({blur_mix})'[card_transition_bg]",
        f"[0:v]trim=start_frame={left_start_frame}:end_frame={left_end_frame},setpts=PTS-STARTPTS,"
        "format=rgb24[card_transition_old_rgb]",
        f"[1:v]trim=start_frame=0:end_frame={CARD_TRANSITION_FRAMES},setpts=PTS-STARTPTS,"
        "format=rgb24[card_transition_new_rgb]",
        f"[4:v]setpts=PTS-STARTPTS,trim=duration={CARD_TRANSITION_SECONDS:.6f},"
        "split=2[card_transition_old_shadow_source][card_transition_new_shadow]",
        f"[5:v]format=gray,setpts=PTS-STARTPTS,trim=duration={CARD_TRANSITION_SECONDS:.6f},"
        "split=2[card_transition_old_mask_source][card_transition_new_mask]",
        "[card_transition_old_rgb][card_transition_old_mask_source]alphamerge[card_transition_old_alpha]",
        "[card_transition_new_rgb][card_transition_new_mask]alphamerge[card_transition_new_alpha]",
        f"[card_transition_old_shadow_source]scale=w='trunc(({CARD_W}+2*{CARD_SHADOW_PADDING})*({old_zoom_overlay}))':"
        f"h='trunc(({CARD_H}+2*{CARD_SHADOW_PADDING})*({old_zoom_overlay}))':"
        "eval=frame:flags=bicubic[card_transition_old_shadow]",
        f"[card_transition_old_alpha]scale=w='trunc({CARD_W}*({old_zoom_overlay}))':"
        f"h='trunc(({CARD_H}/{CARD_W})*{CARD_W}*({old_zoom_overlay}))':"
        "eval=frame:flags=bicubic[card_transition_old]",
        f"[card_transition_new_alpha]scale={CARD_W}:{CARD_H}:flags=bicubic[card_transition_new]",
        f"[card_transition_bg][card_transition_old_shadow]overlay=x='(W-w)/2+({old_x})+{CARD_SHADOW_OFFSET_X}*({old_zoom_overlay})':"
        f"y='(H-h)/2+{CARD_SHADOW_OFFSET_Y}*({old_zoom_overlay})':format=auto[card_transition_old_shadow_layer]",
        f"[card_transition_old_shadow_layer][card_transition_old]overlay=x='(W-w)/2+({old_x})':"
        "y='(H-h)/2':format=auto[card_transition_old_composite]",
        f"[card_transition_old_composite][card_transition_new_shadow]overlay=x='(W-w)/2+({new_x})+{CARD_SHADOW_OFFSET_X}':"
        f"y='(H-h)/2+{CARD_SHADOW_OFFSET_Y}':format=auto[card_transition_new_shadow_layer]",
        f"[card_transition_new_shadow_layer][card_transition_new]overlay=x='(W-w)/2+({new_x})':"
        "y='(H-h)/2':format=auto,trim=duration="
        f"{CARD_TRANSITION_SECONDS:.6f},format=yuv420p[out]",
    ])
    _run_compositor([
        str(FFMPEG), "-y", "-i", str(left_card), "-i", str(right_card),
        "-loop", "1", "-framerate", str(FPS), "-i", str(background),
        "-loop", "1", "-framerate", str(FPS), "-i", str(card_background_blur),
        "-loop", "1", "-framerate", str(FPS), "-i", str(card_shadow),
        "-loop", "1", "-framerate", str(FPS), "-i", str(card_mask),
        "-filter_complex_threads", str(VIDEO_FILTER_THREADS), "-filter_threads", str(VIDEO_FILTER_THREADS),
        "-filter_complex", graph, "-map", "[out]", "-an", "-frames:v", str(CARD_TRANSITION_FRAMES),
        *VIDEO_ENCODER_ARGS, "-r", str(FPS), str(output),
    ])
    return output


def _native_render_segment_transition(
    left: Path,
    right: Path,
    left_start_frame: int,
    left_mode: str,
    right_mode: str,
    direction: str,
    output: Path,
) -> Path:
    """Renderiza somente os dez quadros de transição entre dois modos.

    O xfade recebe duas janelas já recortadas e ambas começam no PTS zero. Isso
    preserva o movimento editorial de cartão/fullscreen sem o framesync ter de
    guardar segundos de B-roll até o ponto da transição.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    # As trocas cartão→cartão usam o compositor dedicado acima; aqui restam
    # somente as fronteiras que envolvem fullscreen, em janelas finitas.
    transition = (
        "smoothleft" if direction == "to_left"
        else "smoothright" if direction == "to_right"
        else "fade"
    )
    left_end_frame = left_start_frame + TRANSITION_FRAMES
    _run_compositor([
        str(FFMPEG), "-y", "-i", str(left), "-i", str(right),
        "-filter_complex_threads", str(VIDEO_FILTER_THREADS), "-filter_threads", str(VIDEO_FILTER_THREADS),
        "-filter_complex",
        f"[0:v]trim=start_frame={left_start_frame}:end_frame={left_end_frame},"
        f"setpts=PTS-STARTPTS,setsar=1[left];"
        f"[1:v]trim=start_frame=0:end_frame={TRANSITION_FRAMES},"
        "setpts=PTS-STARTPTS,setsar=1[right];"
        f"[left][right]xfade=transition={transition}:duration={TRANSITION_SECONDS:.6f}:offset=0,"
        "format=yuv420p[out]",
        "-map", "[out]", "-an", *VIDEO_ENCODER_ARGS, "-r", str(FPS), str(output),
    ])
    return output


def _native_render_segment_components(
    segment: RenderSegment,
    number: int,
    segment_dir: Path,
    scene_paths: list[Path],
    base_paths: list[Path],
    background: Path,
    card_background_blur: Path,
    card_shadow: Path,
    card_mask: Path,
    scene_starts: list[float],
    scene_durations: list[float],
    modes: list[str],
    card_focuses: list[bool],
    exits: list[str],
    animation: str,
) -> list[RenderPart]:
    """Prepara cortes lógicos e transições curtas para limites entre cenas.

    As telas de cada lado da fronteira continuam sendo produzidas uma vez. Os
    corpos são apenas janelas lógicas de seus MP4s; o único encoder do trecho
    aplica esses ``trim`` já na concatenação. Só a pequena janela compartilhada
    vira um arquivo de transição separado. Assim não há uma cadeia de xfade,
    nem um input atrasado ocupando ``filter_buffered_frames`` até a saída do
    primeiro segmento.
    """
    indices = list(range(segment.start_index, segment.end_index + 1))
    # Cada fronteira recebe seu próprio compositor curto. Cartão→cartão usa o
    # movimento físico dedicado; as demais usam xfade, sem encadear filtros.
    has_inbound_transition = segment.start_index > 0
    animated_after = set(indices[:-1])
    pieces_dir = segment_dir / f"partes_{number:03d}"
    pieces: list[RenderPart] = []
    for position, scene_index in enumerate(indices):
        visible_frames = round(scene_durations[scene_index] * FPS)
        # Dentro do segmento, a fronteira é emitida logo após o corpo da cena
        # anterior. Só o primeiro item precisa materializar a transição vinda
        # do segmento precedente. Em ambos os casos, os primeiros quadros da
        # da cena atual já foram consumidos pelo xfade e não podem reaparecer
        # no corpo dela — isso causava a sensação de a cena voltar para trás.
        emit_inbound_transition = position == 0 and has_inbound_transition
        has_preceding_transition = position > 0 or has_inbound_transition
        if emit_inbound_transition:
            previous = scene_index - 1
            previous_visible_frames = round(scene_durations[previous] * FPS)
            transition = pieces_dir / f"{len(pieces) + 1:02d}_transicao_{previous + 1:03d}.mp4"
            if modes[previous] == modes[scene_index] == "card":
                pieces.append(RenderPart(_native_render_card_to_card_transition(
                    base_paths[previous], base_paths[scene_index], background, card_background_blur, card_shadow, card_mask,
                    previous_visible_frames, animation,
                    round(scene_starts[scene_index] * FPS), exits[previous], card_focuses[previous], transition,
                )))
            else:
                pieces.append(RenderPart(_native_render_segment_transition(
                    scene_paths[previous], scene_paths[scene_index], previous_visible_frames,
                    modes[previous], modes[scene_index],
                    exits[previous], transition,
                )))
        start_frame = (
            _transition_frames(modes[scene_index - 1], modes[scene_index])
            if has_preceding_transition
            else 0
        )
        if visible_frames <= start_frame:
            raise RuntimeError("Cena curta demais para preservar a transição horizontal.")
        pieces.append(RenderPart(scene_paths[scene_index], start_frame, visible_frames))

        if scene_index in animated_after:
            transition = pieces_dir / f"{len(pieces) + 1:02d}_transicao_{scene_index + 1:03d}.mp4"
            next_index = scene_index + 1
            if modes[scene_index] == modes[next_index] == "card":
                pieces.append(RenderPart(_native_render_card_to_card_transition(
                    base_paths[scene_index], base_paths[next_index], background, card_background_blur, card_shadow, card_mask,
                    visible_frames, animation,
                    round(scene_starts[next_index] * FPS), exits[scene_index], card_focuses[scene_index], transition,
                )))
            else:
                pieces.append(RenderPart(_native_render_segment_transition(
                    scene_paths[scene_index], scene_paths[next_index], visible_frames,
                    modes[scene_index], modes[next_index],
                    exits[scene_index], transition,
                )))
    return pieces


def _native_concat_video_parts(parts: list[Path | RenderPart], output: Path) -> Path:
    """Une trechos H.264 finitos e normaliza o PTS entre eles.

    O concat demuxer com ``-c:v copy`` parecia barato, mas arquivos AMF curtos
    podem carregar DTS/PTS de B-frames que não começam no mesmo ponto. Ao
    recortar a CTA depois dessa cópia, o FFmpeg descartava quadros e encurtava
    o sufixo. Este é um único concat de entradas finitas: ele recodifica para
    tornar a linha do tempo contínua, sem ``split``, overlays atrasados ou
    qualquer input em loop. ``RenderPart`` evita materializar um MP4
    intermediário só para aparar o corpo de uma cena; ``Path`` continua aceito
    nos pequenos estágios de anotação.
    """
    if not parts:
        raise RuntimeError("Não há partes visuais para concatenar.")
    output.parent.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = []
    filters: list[str] = []
    labels: list[str] = []
    for index, raw_part in enumerate(parts):
        part = raw_part if isinstance(raw_part, RenderPart) else RenderPart(raw_part)
        if part.start_frame < 0 or (part.end_frame is not None and part.end_frame <= part.start_frame):
            raise RuntimeError("Janela de vídeo inválida durante a concatenação horizontal.")
        inputs.extend(["-i", str(part.source)])
        label = f"[concat_part_{index}]"
        trim = (
            f"trim=start_frame={part.start_frame}:end_frame={part.end_frame},"
            if part.end_frame is not None
            else f"trim=start_frame={part.start_frame},"
            if part.start_frame
            else ""
        )
        filters.append(
            f"[{index}:v]{trim}settb=1/{FPS},setsar=1,setpts=PTS-STARTPTS,format=yuv420p{label}"
        )
        labels.append(label)
    filters.append("".join(labels) + f"concat=n={len(parts)}:v=1:a=0[concat_out]")
    _run_compositor([
        str(FFMPEG), "-y", *inputs,
        "-filter_complex_threads", str(VIDEO_FILTER_THREADS), "-filter_threads", str(VIDEO_FILTER_THREADS),
        "-filter_complex", ";".join(filters), "-map", "[concat_out]", "-an",
        *VIDEO_ENCODER_ARGS, "-r", str(FPS), str(output),
    ])
    return output


def _native_ass_timestamp(seconds: float) -> str:
    """Converte segundos para o formato centesimal usado pelo libass."""
    centiseconds = max(0, round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    return f"{hours}:{minutes:02d}:{remainder // 100:02d}.{remainder % 100:02d}"


def _native_escape_ass_text(value: str) -> str:
    """Preserva texto do roteiro em eventos ASS sem permitir tags acidentais."""
    return value.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")


def _native_ass_color(rgb: str) -> str:
    """Converte RRGGBB para AABBGGRR, a ordem de cor do formato ASS."""
    value = rgb.removeprefix("#").upper()
    if not re.fullmatch(r"[0-9A-F]{6}", value):
        raise ValueError(f"Cor de annotation inválida: {rgb!r}.")
    return f"&H00{value[4:6]}{value[2:4]}{value[:2]}"


def _native_annotation_ass(
    directory: Path,
    annotation_index: int,
    lines: list[str],
    duration: float,
    emoji: str | None,
) -> Path:
    """Materializa a digitação em ASS, sem encadear ``drawtext`` entre frames.

    Em certos builds do FFmpeg, filtros ``drawtext`` consecutivos com
    ``enable`` devolvem o frame de entrada quando um estado futuro está
    desativado. Por isso a primeira linha de uma annotation de duas linhas
    desaparecia enquanto a segunda era digitada. O libass tem eventos
    independentes e sobrepostos, logo preserva as duas linhas em todo quadro.
    """
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f"annotation_{annotation_index:02d}_typing.ass"
    text_end = _native_annotation_text_end(0.0, duration, lines, emoji)
    cursor = ANNOTATION_TYPING_DELAY
    typing_step, _ = _native_annotation_timing(emoji)
    line_y = (488, 592) if len(lines) == 2 else (540,)
    events: list[str] = []

    def add_state(state_lines: list[tuple[str, int]], start: float, end: float) -> None:
        if end <= start:
            return
        for text, y in state_lines:
            events.append(
                "Dialogue: 0,"
                f"{_native_ass_timestamp(start)},{_native_ass_timestamp(end)},SynthReelCTA,,0,0,0,,"
                f"{{\\an5\\pos(960,{y})}}{_native_escape_ass_text(text)}"
            )

    complete: list[tuple[str, int]] = []
    for line_index, (line, y) in enumerate(zip(lines, line_y, strict=True)):
        for char_count in range(1, len(line) + 1):
            next_cursor = cursor + typing_step
            add_state([*complete, (line[:char_count], y)], cursor, next_cursor)
            cursor = next_cursor
        complete.append((line, y))
        if line_index < len(lines) - 1 and ANNOTATION_LINE_GAP > 0:
            gap_end = cursor + ANNOTATION_LINE_GAP
            add_state(complete, cursor, gap_end)
            cursor = gap_end
    add_state(complete, cursor, text_end)

    text_style = _require_annotation_font()
    output.write_text(
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1920\n"
        "PlayResY: 1080\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
        "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"Style: SynthReelCTA,{text_style.ass_font_name},{text_style.font_size},"
        f"{_native_ass_color(text_style.font_color)},{_native_ass_color(text_style.font_color)},"
        f"{_native_ass_color(text_style.outline_color)},&H00000000,"
        f"{text_style.bold},0,0,0,100,100,0,0,1,{text_style.outline_width},{text_style.shadow},5,10,10,10,1\n\n"
        "[Events]\n"
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
        + "\n".join(events)
        + "\n",
        encoding="utf-8-sig",
    )
    return output


def _native_render_annotation_effect(
    source: Path,
    start_frame: int,
    end_frame: int,
    annotation_index: int,
    lines: list[str],
    emoji: str | None,
    output: Path,
    *,
    encoder_args: list[str] | None = None,
) -> Path:
    """Renderiza apenas a janela finita de uma CTA com blur e digitação.

    Não há PTS deslocado nem ramo de sufixo neste processo: o ``boxblur`` e os
    ``drawtext`` por letra recebem só os poucos segundos da própria CTA.
    """
    if end_frame <= start_frame:
        raise RuntimeError("Janela de anotação vazia durante a composição horizontal.")
    output.parent.mkdir(parents=True, exist_ok=True)
    duration = (end_frame - start_frame) / FPS
    stickers = _native_required_stickers([(lines, 0.0, duration, emoji)])
    sticker = stickers.get(emoji) if emoji else None
    inputs = [str(FFMPEG), "-y", "-i", str(source)]
    sticker_input_index: int | None = None
    if sticker is not None:
        sticker_input_index = 1
        inputs.extend(["-loop", "1", "-framerate", str(FPS), "-i", str(sticker)])

    graph: list[str] = []
    sharp = f"[annotation_{annotation_index}_sharp]"
    blur_source = f"[annotation_{annotation_index}_blur_source]"
    blur = f"[annotation_{annotation_index}_blur]"
    blended = f"[annotation_{annotation_index}_blended]"
    graph.append(
        f"[0:v]trim=start_frame={start_frame}:end_frame={end_frame},"
        f"setpts=PTS-STARTPTS,setsar=1,split=2{sharp}{blur_source}"
    )
    graph.append(f"{blur_source}boxblur=10:2{blur}")
    text_end = _native_annotation_text_end(0.0, duration, lines, emoji)
    blur_mix = _native_blur_mix_expression(0.0, text_end, duration)
    graph.append(f"{sharp}{blur}blend=all_expr='A*(1-({blur_mix}))+B*({blur_mix})'{blended}")
    ass = _native_annotation_ass(output.parent, annotation_index, lines, duration, emoji)
    styled = f"[annotation_{annotation_index}_ass]"
    graph.append(f"{blended}ass=filename='{_native_filter_path(ass)}'{styled}")
    # Este é o caminho usado pelas annotations que cabem dentro de um segmento.
    # O sticker não pode nascer junto da primeira letra: ele entra no mesmo
    # cursor que agenda bottle_cork/new_idea, no fim da digitação.
    emoji_start = _native_annotation_emoji_offset(lines, emoji) if emoji else text_end
    if sticker is None and emoji:
        if not SYSTEM_EMOJI_FONT.is_file():
            raise FileNotFoundError("A fonte de emoji do Windows não está disponível para o fallback de annotations.")
        emoji_output = f"[annotation_{annotation_index}_emoji_system]"
        emoji_font = SYSTEM_EMOJI_FONT.as_posix().replace(":", r"\:")
        graph.append(
            f"{styled}drawtext=fontfile='{emoji_font}':text='{_escape_drawtext(emoji)}':"
            "fontcolor=white:fontsize=76:borderw=2:bordercolor=black@0.90:"
            "x='(w+text_w)/2+32':y='h/2-text_h/2':"
            f"enable='{_native_time_window(emoji_start, text_end)}'{emoji_output}"
        )
        styled = emoji_output
    elif sticker is not None:
        sticker_label = f"[annotation_{annotation_index}_sticker]"
        emoji_output = f"[annotation_{annotation_index}_emoji]"
        graph.append(f"[{sticker_input_index}:v]format=rgba,scale=-1:{EMOJI_STICKER_HEIGHT}{sticker_label}")
        graph.append(
            f"{styled}{sticker_label}overlay=x='{EMOJI_STICKER_X}':y='(main_h-overlay_h)/2':format=auto:"
            f"enable='{_native_time_window(emoji_start, text_end)}'{emoji_output}"
        )
        styled = emoji_output
    graph.append(
        f"{styled}trim=duration={duration:.6f},setpts=PTS-STARTPTS,"
        "setsar=1,format=yuv420p[out]"
    )
    effective_encoder_args = VIDEO_ENCODER_ARGS if encoder_args is None else encoder_args
    _run_compositor([
        *inputs,
        "-filter_complex_threads", str(VIDEO_FILTER_THREADS), "-filter_threads", str(VIDEO_FILTER_THREADS),
        "-filter_buffered_frames", str(MAX_FILTER_BUFFERED_FRAMES),
        "-filter_complex", ";".join(graph), "-map", "[out]", "-an",
        *effective_encoder_args, "-r", str(FPS), str(output),
    ])
    return output


def _native_apply_segment_annotation(
    source: Path,
    total_frames: int,
    annotation_index: int,
    lines: list[str],
    start: float,
    end: float,
    emoji: str | None,
    directory: Path,
) -> Path:
    """Aplica uma CTA com ramos finitos e concatenação de PTS normalizado.

    O efeito é materializado isoladamente; prefixo e sufixo permanecem como
    janelas independentes da fonte no concat final. Isso impede que o FFmpeg
    reutilize a última imagem de um ``split`` como sufixo — a origem do antigo
    congelamento após o blur — sem recodificar esses dois ramos antes do tempo.
    """
    start_frame = max(0, min(total_frames, _nearest_frame(start)))
    end_frame = max(start_frame + 1, min(total_frames, _nearest_frame(end)))
    if start_frame >= total_frames or end_frame <= start_frame:
        return source
    # Os ramos antes/depois da anotação não precisam virar MP4s próprios: o
    # concat já vai recodificar a linha do tempo completa. Abrir a fonte como
    # dois ``RenderPart`` preserva a separação que evita o congelamento após o
    # blur, mas elimina duas codificações AMF por anotação.
    parts: list[Path | RenderPart] = []
    if start_frame:
        parts.append(RenderPart(source, 0, start_frame))
    effect = directory / f"annotation_{annotation_index:02d}_effect.mp4"
    parts.append(_native_render_annotation_effect(
        source, start_frame, end_frame, annotation_index, lines, emoji, effect,
    ))
    if end_frame < total_frames:
        parts.append(RenderPart(source, end_frame, total_frames))
    return _native_concat_video_parts(parts, directory / f"annotation_{annotation_index:02d}_timeline.mp4")


def _native_render_segment_fades(
    source: Path,
    duration: float,
    opening_fade: float,
    closing_fade: float,
    output: Path,
) -> Path:
    """Aplica as vinhetas de borda numa única passagem finita."""
    filters: list[str] = []
    if opening_fade > 0:
        filters.append(f"fade=t=in:st=0:d={opening_fade:.3f}")
    if closing_fade > 0:
        filters.append(f"fade=t=out:st={max(0.0, duration - closing_fade):.3f}:d={closing_fade:.3f}")
    filters.extend([f"trim=duration={duration:.6f}", "setsar=1", "format=yuv420p"])
    _run_compositor([
        str(FFMPEG), "-y", "-i", str(source),
        "-filter_complex_threads", str(VIDEO_FILTER_THREADS), "-filter_threads", str(VIDEO_FILTER_THREADS),
        "-vf", ",".join(filters), "-map", "0:v", "-an", *VIDEO_ENCODER_ARGS,
        "-r", str(FPS), str(output),
    ])
    return output


def _native_render_segment(
    segment: RenderSegment,
    number: int,
    segment_dir: Path,
    scene_paths: list[Path],
    base_paths: list[Path],
    background: Path,
    card_background_blur: Path,
    card_shadow: Path,
    card_mask: Path,
    scene_starts: list[float],
    scene_durations: list[float],
    modes: list[str],
    card_focuses: list[bool],
    exits: list[str],
    animation: str,
    annotations: list[tuple[list[str], float, float, str | None]],
    *,
    opening_fade: float = 0.0,
    closing_fade: float = 0.0,
) -> Path:
    """Concatena telas/xfades finitos e aplica efeitos locais.

    Anotações são compostas no fragmento que as contém. Isso evita aplicar
    cada blur e cada letra sobre toda a duração do vídeo na etapa final.
    """
    render_end = segment.handoff_index if segment.handoff_index is not None else segment.end_index
    indices = list(range(segment.start_index, render_end + 1))
    if len(indices) > MAX_SCENES_PER_SEGMENT + 1:
        raise RuntimeError("Fragmento visual excedeu o limite de cenas.")
    segment_dir.mkdir(parents=True, exist_ok=True)
    output = segment_dir / f"segmento_{number:03d}.mp4"
    first_start = scene_starts[segment.start_index]
    # Cada fronteira vira um arquivo finito. Cartões usam sua coreografia
    # própria; fullscreen continua usando xfade curto. O grafo principal nunca
    # recebe fundo, sombra, loops ou cadeia cumulativa de framesync.
    component_paths = _native_render_segment_components(
        segment, number, segment_dir, scene_paths, base_paths, background, card_background_blur, card_shadow, card_mask,
        scene_starts, scene_durations, modes, card_focuses, exits, animation,
    )
    # Sem CTA nem vinheta, o primeiro concat já é o arquivo definitivo. Isso
    # evita uma passagem AMF extra nos muitos segmentos puramente visuais.
    base = (
        output
        if not annotations and opening_fade <= 0 and closing_fade <= 0
        else segment_dir / f"segmento_{number:03d}_base.mp4"
    )
    current = _native_concat_video_parts(component_paths, base)
    total_frames = segment.output_frames
    if annotations:
        _require_annotation_font()
        annotation_dir = segment_dir / f"anotacoes_{number:03d}"
        # Cada anotação mantém seu próprio render curto. Encadear várias
        # janelas no mesmo grafo economiza uma recodificação, mas em alguns
        # segmentos faz o FFmpeg reutilizar o último quadro do ramo anterior
        # até a próxima janela. A prioridade aqui é preservar uma linha do
        # tempo contínua; RenderPart já evita recodificar prefixo e sufixo.
        for annotation_index, (lines, start, end, emoji) in enumerate(sorted(annotations, key=lambda item: item[1])):
            current = _native_apply_segment_annotation(
                current, total_frames, annotation_index, lines,
                start - first_start, end - first_start, emoji, annotation_dir,
            )

    # As vinhetas pertencem aos fragmentos de borda. Assim a montagem final
    # pode copiar o vídeo e dedicar seu único filtro à mixagem de áudio.
    if opening_fade > 0 or closing_fade > 0:
        return _native_render_segment_fades(
            current, total_frames / FPS, opening_fade, closing_fade, output,
        )
    if current != output:
        # A linha do tempo já foi normalizada pelo concat de estágio. Copiar
        # aqui não altera PTS e evita recodificar o segmento inteiro de novo.
        shutil.copy2(current, output)
        return output
    return current


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
    """Cria uma trilha de fundo contínua preservando a faixa escolhida.

    O ciclo é normalizado e encadeado com ``acrossfade``. Não usamos
    ``silenceremove``: algumas músicas têm pausas intencionais ou uma abertura
    delicada e o filtro encerra o stream no primeiro silêncio encontrado,
    transformando a trilha inteira em um loop curto e praticamente mudo.
    """
    if duration <= 0:
        raise ValueError("A duração da trilha de fundo precisa ser positiva.")
    directory.mkdir(parents=True, exist_ok=True)
    source_duration = _duration(music)
    cycle = directory / "trilha_ciclo.m4a"
    _run_compositor([
        str(FFMPEG), "-y", "-i", str(music),
        "-af", "aresample=48000,asetpts=PTS-STARTPTS",
        "-c:a", "aac", "-b:a", "192k", str(cycle),
    ])
    cycle_duration = _duration(cycle)
    if cycle_duration + MUSIC_CYCLE_DURATION_TOLERANCE_SECONDS < source_duration:
        raise RuntimeError(
            "A preparação da trilha encurtou o áudio importado de "
            f"{source_duration:.2f}s para {cycle_duration:.2f}s. "
            "A renderização foi interrompida para não publicar um vídeo sem música."
        )
    crossfade = min(MUSIC_LOOP_CROSSFADE_SECONDS, cycle_duration / 4)
    if cycle_duration <= crossfade:
        raise ValueError(f"A trilha {music.name} é curta demais para formar um loop suave.")
    if duration <= cycle_duration:
        return cycle

    effective_cycle = cycle_duration - crossfade
    copies = math.ceil((duration - cycle_duration) / effective_cycle) + 1
    bed = directory / "trilha_continua.m4a"

    def compose_crossfaded(sources: list[Path], output: Path, *, trim_to: float | None = None) -> None:
        """Une fontes em série sem estourar o limite de argumentos do Windows."""
        inputs = [part for source in sources for part in ("-i", str(source))]
        graph = [
            f"[{index}:a]aresample=48000,asetpts=PTS-STARTPTS[music_{index}]"
            for index in range(len(sources))
        ]
        mixed = "[music_0]"
        for index in range(1, len(sources)):
            output_label = f"[music_mix_{index}]"
            graph.append(
                f"{mixed}[music_{index}]acrossfade=d={crossfade:.3f}:c1=tri:c2=tri{output_label}"
            )
            mixed = output_label
        tail = f"atrim=duration={trim_to:.6f}," if trim_to is not None else ""
        graph.append(f"{mixed}{tail}asetpts=PTS-STARTPTS[audio]")
        _run_compositor([
            str(FFMPEG), "-y", *inputs,
            "-filter_complex_threads", "1", "-filter_threads", "1",
            "-filter_complex", ";".join(graph), "-map", "[audio]",
            "-c:a", "aac", "-b:a", "192k", str(output),
        ])

    # Cada redução preserva as transições entre os ciclos. Ao juntar os
    # resultados na etapa seguinte, a transição entre blocos também recebe o
    # mesmo acrossfade; portanto a sequência continua equivalente à linear.
    sources = [cycle] * copies
    level = 0
    while len(sources) > MUSIC_LOOP_MAX_INPUTS_PER_COMMAND:
        reduced: list[Path] = []
        for index in range(0, len(sources), MUSIC_LOOP_MAX_INPUTS_PER_COMMAND):
            group = sources[index:index + MUSIC_LOOP_MAX_INPUTS_PER_COMMAND]
            if len(group) == 1:
                reduced.append(group[0])
                continue
            chunk = directory / f"trilha_bloco_{level:02d}_{index // MUSIC_LOOP_MAX_INPUTS_PER_COMMAND:03d}.m4a"
            compose_crossfaded(group, chunk)
            reduced.append(chunk)
        sources = reduced
        level += 1

    compose_crossfaded(sources, bed, trim_to=duration)
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
            events.append(("bottle_cork", _native_annotation_emoji_time(annotation_start, scene.annotation.lines, "👍")))
        elif scene.annotation.emoji == "🔔":
            events.append(("new_idea", _native_annotation_emoji_time(annotation_start, scene.annotation.lines, "🔔")))
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


def _native_annotation_emoji_time(annotation_start: float, lines: list[str], emoji: str | None) -> float:
    """Retorna o instante absoluto em que o sticker entra ao fim da digitação.

    O mesmo cursor é usado pelo compositor visual: há intervalo apenas entre
    linhas, nunca após a última. Assim o início audível de ``bottle cork.mp3``
    coincide com a aparição do polegar.
    """
    return annotation_start + _native_annotation_emoji_offset(lines, emoji)


def _native_annotation_emoji_offset(lines: list[str], emoji: str | None) -> float:
    """Cursor local compartilhado pelo sticker e pelo efeito sonoro da CTA."""
    typing_step, _ = _native_annotation_timing(emoji)
    return (
        ANNOTATION_TYPING_DELAY
        + sum(len(line) for line in lines) * typing_step
        + max(0, len(lines) - 1) * ANNOTATION_LINE_GAP
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


def _native_annotation_text_end(
    annotation_start: float,
    annotation_end: float,
    lines: list[str],
    emoji: str | None,
) -> float:
    """Reserva o fim da janela para a saída do blur, sem cortar a digitação.

    A janela acústica da anotação continua idêntica. Apenas seu hold visual é
    reduzido quando houver espaço, deixando a rampa de foco terminar antes do
    próximo corte ou do fim do vídeo.
    """
    typing_step, _ = _native_annotation_timing(emoji)
    typing_end = (
        annotation_start
        + ANNOTATION_TYPING_DELAY
        + sum(len(line) for line in lines) * typing_step
        + max(0, len(lines) - 1) * ANNOTATION_LINE_GAP
    )
    # Garante pelo menos um quadro com a anotação completa, inclusive em uma
    # janela curta que já existia no roteiro.
    earliest_safe_end = typing_end + 1 / FPS
    scheduled_end = annotation_end - ANNOTATION_BLUR_RAMP_SECONDS
    return min(annotation_end, max(earliest_safe_end, scheduled_end))


def _native_blur_mix_expression(annotation_start: float, text_end: float, blur_end: float) -> str:
    """Mistura nitidez e blur com entrada/saída graduais, sem corte seco."""
    blur_in_end = min(annotation_start + ANNOTATION_BLUR_RAMP_SECONDS, text_end)
    blur_in_seconds = max(0.001, blur_in_end - annotation_start)
    blur_out_seconds = max(0.001, blur_end - text_end)
    return (
        f"if(lt(T\\,{annotation_start:.3f})\\,0\\,"
        f"if(lt(T\\,{blur_in_end:.3f})\\,(T-{annotation_start:.3f})/{blur_in_seconds:.3f}\\,"
        f"if(lt(T\\,{text_end:.3f})\\,1\\,"
        f"if(lt(T\\,{blur_end:.3f})\\,({blur_end:.3f}-T)/{blur_out_seconds:.3f}\\,0))))"
    )


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
    text_end: float,
    emoji: str | None,
    emoji_input_index: int | None,
) -> str:
    """Desenha a CTA aprovada, revelando cada letra no seu quadro editorial.

    Esta função recebe apenas a janela curta da anotação; assim cada
    ``drawtext`` por caractere trabalha por poucos segundos, e não por todo o
    segmento de até 45 segundos.
    """
    current = source
    cursor = annotation_start + ANNOTATION_TYPING_DELAY
    typing_step, _ = _native_annotation_timing(emoji)
    line_centers = ("h/2-52", "h/2+52") if len(lines) == 2 else ("h/2",)

    def overlay_state(
        base: str,
        state_lines: list[tuple[str, str]],
        start: float,
        end: float,
        suffix: str,
    ) -> str:
        """Desenha um estado completo em camada RGBA transparente.

        ``drawtext`` desabilitado pode devolver o frame de entrada original em
        vez do resultado de um filtro anterior. Em uma cadeia de duas linhas,
        isso apagava a primeira ao iniciar a segunda. Cada estado agora nasce
        transparente, recebe somente as linhas que devem coexistir naquele
        intervalo e é sobreposto ao vídeo; uma camada inativa é transparente e
        jamais pode apagar texto já desenhado por outra camada.
        """
        layer = f"[annotation_{annotation_index}_{suffix}_layer]"
        graph.append(
            f"color=c=black@0.0:s={WIDTH}x{HEIGHT}:r={FPS},format=rgba,"
            f"trim=duration={max(1 / FPS, text_end):.6f},setpts=PTS-STARTPTS{layer}"
        )
        for line_number, (text, y_center) in enumerate(state_lines):
            output = f"[annotation_{annotation_index}_{suffix}_text_{line_number}]"
            graph.append(
                f"{layer}drawtext=fontfile='{_native_filter_path(_require_annotation_font().font_path)}':"
                f"text='{_escape_drawtext(text)}':fontcolor=0x{_annotation_text_style().font_color}:fontsize={_annotation_text_style().font_size}:"
                f"borderw={_annotation_text_style().outline_width}:bordercolor=0x{_annotation_text_style().outline_color}@0.96:"
                f"x=(w-text_w)/2:y={y_center}-text_h/2:"
                f"enable='{_native_time_window(start, end)}'{output}"
            )
            layer = output
        output = f"[annotation_{annotation_index}_{suffix}_composite]"
        graph.append(f"{base}{layer}overlay=0:0:format=auto{output}")
        return output

    completed_lines: list[tuple[str, str]] = []
    for line_index, (line, y_center) in enumerate(zip(lines, line_centers, strict=True)):
        for char_count in range(1, len(line) + 1):
            next_cursor = cursor + typing_step
            current = overlay_state(
                current,
                [*completed_lines, (line[:char_count], y_center)],
                cursor,
                next_cursor,
                f"typed_{line_index}_{char_count}",
            )
            cursor = next_cursor
        completed_lines.append((line, y_center))
        if line_index < len(lines) - 1 and ANNOTATION_LINE_GAP > 0:
            gap_end = cursor + ANNOTATION_LINE_GAP
            current = overlay_state(
                current,
                completed_lines,
                cursor,
                gap_end,
                f"line_{line_index}_gap",
            )
            cursor = gap_end

    # Mantém todas as linhas completas até o início da saída gradual do blur.
    current = overlay_state(current, completed_lines, cursor, text_end, "hold")
    if emoji:
        if emoji_input_index is None:
            if not SYSTEM_EMOJI_FONT.is_file():
                raise FileNotFoundError(
                    "A fonte de emoji do Windows não está disponível para o fallback de annotations."
                )
            output = f"[annotation_{annotation_index}_emoji_system]"
            emoji_font = SYSTEM_EMOJI_FONT.as_posix().replace(":", r"\:")
            graph.append(
                f"{current}drawtext=fontfile='{emoji_font}':"
                f"text='{_escape_drawtext(emoji)}':fontcolor=white:fontsize=76:"
                "borderw=2:bordercolor=black@0.90:"
                "x='(w+text_w)/2+32':y='h/2-text_h/2':"
                f"enable='{_native_time_window(cursor, text_end)}'{output}"
            )
            return output
        sticker = f"[annotation_{annotation_index}_sticker]"
        output = f"[annotation_{annotation_index}_emoji]"
        graph.append(
            f"[{emoji_input_index}:v]format=rgba,scale=-1:{EMOJI_STICKER_HEIGHT}{sticker}"
        )
        graph.append(
            f"{current}{sticker}overlay=x='{EMOJI_STICKER_X}':y='(main_h-overlay_h)/2':format=auto:"
            f"enable='{_native_time_window(cursor, text_end)}'{output}"
        )
        current = output
    return current


def _native_windowed_annotation_filters(
    graph: list[str],
    video: str,
    annotation_index: int,
    lines: list[str],
    start: float,
    end: float,
    emoji: str | None,
    emoji_input_index: int | None,
    *,
    label_prefix: str,
    source_duration: float | None = None,
) -> str:
    """Aplica a anotação em uma janela finita e recompõe a linha do tempo.

    O antigo retorno da janela por ``overlay`` deslocava seu PTS e fazia o
    framesync acumular todos os quadros anteriores à CTA. A solução temporária
    eliminou o efeito de digitação e as rampas de blur. Aqui preparamos
    prefixo, efeito e sufixo como ramos finitos e os concatenamos: a cadeia de
    letras e o ``blend`` só recebem os quadros da própria anotação, sem uma
    entrada atrasada aguardando no ``overlay``.
    """
    duration = end - start
    if duration <= 1 / FPS:
        return video

    window_duration = end - start
    local_text_end = _native_annotation_text_end(0.0, window_duration, lines, emoji)
    source_prefix = f"[{label_prefix}_annotation_{annotation_index}_prefix_source]"
    source_window = f"[{label_prefix}_annotation_{annotation_index}_window_source]"
    source_suffix = f"[{label_prefix}_annotation_{annotation_index}_suffix_source]"
    graph.append(f"{video}split=3{source_prefix}{source_window}{source_suffix}")

    parts: list[str] = []
    if start > 1 / FPS:
        prefix = f"[{label_prefix}_annotation_{annotation_index}_prefix]"
        graph.append(f"{source_prefix}trim=end={start:.6f},setpts=PTS-STARTPTS{prefix}")
        parts.append(prefix)

    sharp = f"[{label_prefix}_annotation_{annotation_index}_sharp]"
    blur_source = f"[{label_prefix}_annotation_{annotation_index}_blur_source]"
    blur = f"[{label_prefix}_annotation_{annotation_index}_blur]"
    blended = f"[{label_prefix}_annotation_{annotation_index}_blended]"
    graph.append(
        f"{source_window}trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS,"
        f"split=2{sharp}{blur_source}"
    )
    graph.append(f"{blur_source}boxblur=10:2{blur}")
    blur_mix = _native_blur_mix_expression(0.0, local_text_end, window_duration)
    graph.append(f"{sharp}{blur}blend=all_expr='A*(1-({blur_mix}))+B*({blur_mix})'{blended}")
    effect = _native_typing_annotation_filters(
        graph, blended, annotation_index, lines, 0.0, local_text_end, emoji, emoji_input_index,
    )
    parts.append(effect)

    if source_duration is None or end < source_duration - 1 / FPS:
        suffix = f"[{label_prefix}_annotation_{annotation_index}_suffix]"
        graph.append(f"{source_suffix}trim=start={end:.6f},setpts=PTS-STARTPTS{suffix}")
        parts.append(suffix)
    output = f"[{label_prefix}_annotation_{annotation_index}_timeline]"
    graph.append("".join(parts) + f"concat=n={len(parts)}:v=1:a=0{output}")
    return output


def _native_required_stickers(annotations: list[tuple[list[str], float, float, str | None]]) -> dict[str, Path]:
    """Retorna os stickers 3D disponíveis; os demais usam emoji do sistema."""
    emojis = sorted({emoji for _, _, _, emoji in annotations if emoji})
    if any(emoji not in EMOJI_STICKERS or not EMOJI_STICKERS[emoji].is_file() for emoji in emojis):
        if not SYSTEM_EMOJI_FONT.is_file():
            raise FileNotFoundError(
                "Há emoji sem sticker 3D e a fonte de emoji do Windows não está disponível."
            )
    return {
        emoji: EMOJI_STICKERS[emoji]
        for emoji in emojis
        if emoji in EMOJI_STICKERS and EMOJI_STICKERS[emoji].is_file()
    }


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
                volume = _NATIVE_SOUND_VOLUMES.get(effect, 0.46) * SFX_VOLUME_BOOST
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
            # ``apad`` sem limite pode manter o filtro vivo indefinidamente
            # após um ``adelay``. Uma fonte de silêncio finita preserva o
            # tamanho editorial do bloco e encerra o FFmpeg no tempo correto.
            silence = f"[sfx_{bucket}_{batch_index}_silence]"
            graph.append(
                f"anullsrc=r=48000:cl=stereo,atrim=duration={chunk_duration:.6f},asetpts=PTS-STARTPTS{silence}"
            )
            graph.append(
                "".join([*labels, silence])
                + f"amix=inputs={len(labels) + 1}:duration=longest:normalize=0,atrim=duration={chunk_duration:.6f},"
                "aformat=sample_fmts=s16:sample_rates=48000:channel_layouts=stereo[audio]"
            )
            _run_compositor([
                str(FFMPEG), "-y", *inputs,
                "-filter_complex_threads", "1", "-filter_threads", "1",
                "-filter_complex", ";".join(graph), "-map", "[audio]",
                # Trava adicional no muxer: nenhum SFX pode exceder o chunk,
                # ainda que uma mudança futura no grafo introduza uma cauda.
                "-t", f"{chunk_duration:.6f}", "-c:a", "flac", "-compression_level", "5", str(output),
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
        # FLAC em stream-copy pelo concat demuxer pode manter apenas o primeiro
        # bloco em alguns builds do FFmpeg. Esta recodificação é pequena, mas
        # preserva a linha do tempo completa dos efeitos.
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
        _require_annotation_font()
        for index, (lines, start, end, emoji) in enumerate(annotations):
            video = _native_windowed_annotation_filters(
                graph, video, index, lines, start, end, emoji,
                sticker_input_indices.get(emoji), label_prefix="final", source_duration=visual_duration,
            )
    if annotations:
        opening_fade = min(OPENING_FADE_SECONDS, visual_duration)
        closing_fade = min(CLOSING_FADE_SECONDS, visual_duration)
        closing_start = max(0.0, visual_duration - closing_fade)
        # Só há vinheta aqui quando uma anotação cruzou a fronteira de dois
        # segmentos. No caminho normal ela já foi incorporada nos extremos.
        graph.append(
            f"{video}fade=t=in:st=0:d={opening_fade:.3f},"
            f"fade=t=out:st={closing_start:.3f}:d={closing_fade:.3f},"
            f"trim=duration={visual_duration:.6f},format=yuv420p[video]"
        )

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
        f"[2:a]aresample=48000,volume={MUSIC_BED_VOLUME:.2f}[music]",
        "[music][voice_key]sidechaincompress="
        f"threshold={MUSIC_DUCKING_THRESHOLD:.3f}:ratio={MUSIC_DUCKING_RATIO:.1f}:"
        f"attack={MUSIC_DUCKING_ATTACK_MS}:release={MUSIC_DUCKING_RELEASE_MS}[ducked]",
        f"[voice][ducked][sfx]amix=inputs=3:duration=first:normalize=0[mix];[mix]alimiter=limit={FINAL_AUDIO_LIMIT:.2f}[audio]",
    ])
    filter_script.write_text(";".join(graph), encoding="utf-8")
    inputs = ["-safe", "0", "-f", "concat", "-i", str(visual_manifest), "-i", str(narration), "-i", str(music)]
    for track, _ in sfx_tracks:
        inputs.extend(["-i", str(track)])
    for sticker in stickers.values():
        inputs.extend(["-loop", "1", "-framerate", str(FPS), "-i", str(sticker)])
    video_args = (
        ["-map", "[video]", *VIDEO_ENCODER_ARGS, "-r", str(FPS)]
        if annotations
        else ["-map", "0:v", "-c:v", "copy"]
    )
    _run_compositor([
        str(FFMPEG), "-y", *inputs,
        "-filter_complex_threads", str(VIDEO_FILTER_THREADS), "-filter_threads", str(VIDEO_FILTER_THREADS),
        "-filter_buffered_frames", str(MAX_FILTER_BUFFERED_FRAMES),
        "-filter_complex_script", str(filter_script), *video_args, "-map", "[audio]",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", str(output),
    ])


def _native_composite(
    script: Script,
    background: Path,
    job_dir: Path,
    scene_assets: Path,
    scene_source_names: Mapping[str, str],
    narration: Path,
    timing_payload: dict[str, object],
    music: Path,
    progress_callback: ProgressCallback | None = None,
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
    modes = _layout_modes(scenes)
    card_focuses = _card_focus_plan(scenes, modes, seed_context=script.title)
    clip_frames = [
        max(
            _frames_for_duration(speech[index]) + _transition_frames(modes[index], modes[index + 1]),
            visual_start_frames[index + 1] - visual_start_frames[index]
            + _transition_frames(modes[index], modes[index + 1]),
        )
        if index < len(scenes) - 1
        else narration_frames - visual_start_frames[index]
        for index in range(len(scenes))
    ]
    if any(frames <= 0 for frames in clip_frames):
        raise ValueError("Uma cena ficou curta demais para a composição horizontal.")
    clip_durations = [frames / FPS for frames in clip_frames]
    visual_starts = [frame / FPS for frame in visual_start_frames]
    scene_durations = [
        (visual_start_frames[index + 1] - visual_start_frames[index]) / FPS
        if index < len(scenes) - 1
        else (visual_total_frames - visual_start_frames[index]) / FPS
        for index in range(len(scenes))
    ]
    card_focuses = _native_effective_card_focuses(card_focuses, modes, scene_durations)
    visual_tail_seconds = visual_tail_frames / FPS
    visual_duration = visual_total_frames / FPS
    exits = _transition_directions(scenes, seed_context=script.title)
    # Cada pré-clipe tem duração finita e impede que ``-loop 1`` e
    # ``-stream_loop -1`` alimentem a cadeia cumulativa de xfade. Somente
    # cartões recebem um segundo passe curto com fundo e sombra PNG;
    # fullscreen segue direto para os segmentos. As tarefas independentes são
    # limitadas a dois workers para aproveitar CPU/AMF sem saturar a máquina.
    _report(progress_callback, 30, "Normalizando cenas visuais")
    base_paths = _native_render_scene_clips(
        scenes, job_dir / "cenas_base", scene_assets, modes, clip_frames, source_names=scene_source_names,
        progress_callback=progress_callback, progress_start=30, progress_end=42,
    )
    card_shadow = _native_card_shadow_asset(job_dir)
    card_mask = _native_card_mask_asset(job_dir)
    card_background_blur = _native_card_background_blur_asset(background, job_dir)
    _report(progress_callback, 42, "Compondo cartões visuais")
    scene_paths = _native_render_scene_canvases(
        scenes, base_paths, job_dir / "cenas_prontas", background, card_background_blur, card_shadow, card_mask,
        modes, card_focuses, exits, visual_starts, scene_durations, clip_durations,
        script.background_animation, visual_tail_seconds,
        progress_callback=progress_callback, progress_start=42, progress_end=54,
    )
    render_segments = _build_render_segments(visual_start_frames, visual_total_frames)
    segment_annotations: dict[int, list[tuple[list[str], float, float, str | None]]] = {
        index: [] for index in range(len(render_segments))
    }
    deferred_annotations: list[tuple[list[str], float, float, str | None]] = []
    # Uma anotação tem poucos segundos. Quando ela cabe em um fragmento, seu
    # blur é calculado somente naquela janela de até 90 s, não nos 10+ minutos
    # completos. Casos raros que atravessam uma fronteira permanecem no passe
    # final para não truncar letras nem stickers.
    for annotation in annotations:
        lines, annotation_start, annotation_end, emoji = annotation
        assigned = False
        for segment_index, segment in enumerate(render_segments):
            segment_start = visual_starts[segment.start_index] + segment.trim_start
            segment_end = segment_start + segment.output_duration
            if annotation_start >= segment_start - 1 / FPS and annotation_end <= segment_end + 1 / FPS:
                segment_annotations[segment_index].append((lines, annotation_start, annotation_end, emoji))
                assigned = True
                break
        if not assigned:
            deferred_annotations.append(annotation)

    def render_segment(number: int, segment: RenderSegment) -> Path:
        return _native_render_segment(
            segment, number, job_dir / "segmentos", scene_paths, base_paths, background, card_background_blur, card_shadow, card_mask,
            visual_starts, scene_durations, modes, card_focuses, exits, script.background_animation,
            segment_annotations[number - 1],
            opening_fade=OPENING_FADE_SECONDS if number == 1 else 0.0,
            closing_fade=CLOSING_FADE_SECONDS if number == len(render_segments) else 0.0,
        )

    # Os segmentos não compartilham filtros, arquivos de transição ou pastas
    # de anotação; a única dependência é a ordem do manifesto final. Processar
    # dois por vez remove o gargalo seriado sem voltar a criar um grafo global
    # que retenha frames de todo o vídeo na memória.
    _report(progress_callback, 54, "Compondo trechos visuais em paralelo")
    segment_paths: list[Path | None] = [None] * len(render_segments)
    workers = min(SEGMENT_RENDER_WORKERS, len(render_segments))
    if workers == 1:
        for number, segment in enumerate(render_segments, start=1):
            segment_paths[number - 1] = render_segment(number, segment)
            _report(
                progress_callback,
                54 + round(26 * number / max(1, len(render_segments))),
                f"Compondo trecho visual {number}/{len(render_segments)}",
            )
    else:
        completed = 0
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="horizontal-segment") as executor:
            futures = {
                executor.submit(copy_context().run, render_segment, number, segment): number
                for number, segment in enumerate(render_segments, start=1)
            }
            try:
                for future in as_completed(futures):
                    number = futures[future]
                    segment_paths[number - 1] = future.result()
                    completed += 1
                    _report(
                        progress_callback,
                        54 + round(26 * completed / max(1, len(render_segments))),
                        f"Compondo trecho visual {completed}/{len(render_segments)}",
                    )
            except Exception:
                for pending in futures:
                    pending.cancel()
                raise
    if any(path is None for path in segment_paths):
        raise RuntimeError("A fila de trechos não retornou todos os segmentos visuais.")
    ordered_segment_paths = [path for path in segment_paths if path is not None]

    _report(progress_callback, 80, "Preparando trilha, efeitos e fechamento")
    visual_manifest = _native_segment_manifest(ordered_segment_paths, job_dir)
    final = job_dir / f"{_slug(script.title)}.mp4"
    sfx_directory = job_dir / "sfx"
    music_directory = job_dir / "trilha"
    sfx_tracks = _native_render_sfx_tracks(events, visual_duration, sfx_directory)
    music_bed = _native_looped_music_bed(music, visual_duration, music_directory)
    _report(progress_callback, 88, "Misturando áudio e finalizando o vídeo")
    _native_finalize(
        visual_manifest, narration, music_bed, deferred_annotations, sfx_tracks,
        narration_seconds, visual_duration, final, job_dir / "filtros_renderizacao.ffscript",
    )
    if abs(_duration(final) - visual_duration) > 2 / FPS:
        raise RuntimeError("A entrega final não respeitou a duração da linha do tempo visual.")
    for path in [*ordered_segment_paths, visual_manifest]:
        if path is None:
            continue
        if path.is_file():
            path.unlink()
    for directory in (job_dir / "cenas_base", job_dir / "cenas_prontas", job_dir / "segmentos", sfx_directory, music_directory):
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


def _fullscreen_filter(index: int, seconds: float, *, trim_outer_edges: bool = False) -> str:
    focus_x, focus_y, zoom = FOCUS_POINTS[index % len(FOCUS_POINTS)]
    progress = f"(on/{max(1, _frames_for_duration(seconds) - 1)})"
    # A troca por zoompan reduziu CPU, mas alterou a cadência: com d=1 o
    # estado do zoom depende do comportamento do input em loop e, na prática,
    # várias fotos quase não se aproximavam. Este filtro restaura o Ken Burns
    # editorial original (aproximação e foco progressivos). Ele roda somente
    # no pré-clipe individual da cena, nunca no grafo acumulativo do segmento,
    # portanto não volta a reter centenas de frames em memória.
    viewport_w = f"(2000*2304/(2304+{zoom}*{progress}))"
    viewport_h = f"(({viewport_w})*0.5625)"
    viewport_x = f"(2400-({viewport_w}))*(0.50+({focus_x:.2f}-0.50)*{progress})"
    viewport_y = f"(1350-({viewport_h}))*(0.50+({focus_y:.2f}-0.50)*{progress})"
    edge_trim = _native_psychology_frame_trim_filter() if trim_outer_edges else ""
    return (
        f"{edge_trim}scale=2400:1350:force_original_aspect_ratio=increase,crop=2400:1350,"
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
    text_style: str = "impact",
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
    if text_style not in ANNOTATION_TEXT_STYLES:
        raise ValueError(f"Estilo de fonte inválido: {text_style!r}.")
    log = job_logger or logging.getLogger(__name__)
    if not background.is_file():
        raise FileNotFoundError(f"Imagem de fundo não encontrada: {background.name}")

    scenes = [scene for block in script.blocks for scene in block.scenes]
    job_dir.mkdir(parents=True, exist_ok=True)
    render_dir = RENDER_CACHE_DIR / job_dir.name
    # O UUID do job torna esse alvo exclusivo; nunca removemos uma pasta ampla
    # nem qualquer arquivo do workspace que o operador consulta no painel.
    render_dir.mkdir(parents=True, exist_ok=False)
    log.info("Iniciando renderização horizontal: %s cenas, voz %s.", len(scenes), script.voice)
    log.info("Temporários pesados em cache local: %s", render_dir)
    scene_asset_dir = render_dir / "cenas"
    resolved_sources: dict[str, str] = {}
    logger_token = _COMPOSITOR_LOGGER.set(log)
    text_style_token = _ANNOTATION_TEXT_STYLE.set(text_style)
    try:
        # Fundo e trilha também entram no cache. Assim nenhum processo FFmpeg
        # da composição consulta arquivos mutáveis do OneDrive durante o job.
        persistent_assets = render_dir / "assets_persistentes"
        persistent_assets.mkdir(parents=True, exist_ok=True)
        cached_background = persistent_assets / background.name
        shutil.copy2(background, cached_background)
        source_music = _native_music_path(music_name)
        cached_music = persistent_assets / source_music.name
        shutil.copy2(source_music, cached_music)
        scene_asset_dir, resolved_sources = _materialize_scene_assets(script, image_bindings, render_dir)
        log.info("Assets locais materializados: %s cenas, fundo e trilha.", len(resolved_sources))
        narration = render_dir / "narracao.mp3"
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
            timeline_narration = render_dir / "narracao_com_pausas.wav"
            _insert_narration_pauses(narration, timeline_narration, pauses)
            boundaries = _shift_boundaries_for_pauses(raw_boundaries, pauses)
        narration_seconds = _duration(timeline_narration)
        timing_file = render_dir / "timings_cenas.json"
        timing_payload = _write_scene_timing(timing_file, script, boundaries, narration_seconds)
        log.info("Time-codes de %s cena(s) validados; narração: %.3fs.", timing_payload["scene_count"], narration_seconds)
        _report(progress_callback, 24, "Fala alinhada às cenas; preparando composição visual")

        _report(progress_callback, 30, "Renderizando cenas, trilha e transições em fragmentos")
        composed = _native_composite(
            script,
            cached_background,
            render_dir,
            scene_asset_dir,
            resolved_sources,
            timeline_narration,
            timing_payload,
            cached_music,
            progress_callback,
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
                "text_style": text_style,
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
        shutil.rmtree(render_dir, ignore_errors=True)
        _ANNOTATION_TEXT_STYLE.reset(text_style_token)
        _COMPOSITOR_LOGGER.reset(logger_token)
