"""Renderiza um tema horizontal preparado e aprovado por curadoria humana.

Uso:
    python src/scripts/renderizar_horizontal.py \
        workspace/lotes_horizontais/historia/roma/

Esta esteira e deliberadamente independente do pipeline vertical. Ela usa o
TTS neural, o contrato de layouts horizontais e os assets persistentes em
``workspace/assets/horizontal``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from logging import Logger
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.src.config.settings import settings
from backend.src.core.layout_factory import LayoutFactory
from backend.src.core.pexels_fetcher import PexelsFetcher, PexelsFetcherError
from backend.src.core.tts_neural import TTSNeuralEngine
from backend.src.core.whisper_sync import WhisperSync
from backend.src.scripts.preparar_horizontal import (
    TEMPLATE_11_TOPICOS_EXIGIDOS,
    TEMPLATE_MEDIA_COUNTS,
    TEMPLATES_COM_TEXTO,
    SlotMidia,
    _slots_da_cena,
)
from backend.src.utils.asset_signature import calcular_hash_hibrido
from backend.src.utils.file_retry import replace_com_retry, rmtree_com_retry, unlink_com_retry
from backend.src.utils.logger import get_logger
from backend.src.utils.text_helpers import normalizar_ascii
from backend.src.utils.theme_lock import TravaTema


WORKSPACE_DIR = ROOT_DIR / "workspace"
LOTES_HORIZONTAIS_DIR = WORKSPACE_DIR / "lotes_horizontais"
HORIZONTAL_ASSETS_DIR = WORKSPACE_DIR / "assets" / "horizontal"
TRILHAS_DIR = HORIZONTAL_ASSETS_DIR / "trilhas"
OVERLAYS_DIR = HORIZONTAL_ASSETS_DIR / "overlays"
FUNDOS_ESTATICOS_DIR = HORIZONTAL_ASSETS_DIR / "fundos_estaticos"
SETA_APONTAMENTO_PATH = OVERLAYS_DIR / "seta_apontamento.png"
OUTPUT_HORIZONTAL_DIR = WORKSPACE_DIR / "output" / "horizontal"
TEMP_HORIZONTAL_DIR = Path(
    os.environ.get(
        "SYNTHREEL_TEMP_DIR",
        os.path.join(os.environ.get("TEMP", r"C:\temp"), "SynthReel"),
    )
) / "horizontal"

METADATA_FILENAME = "metadata.json"
FPS = 30
WIDTH = 1920
HEIGHT = 1080
MIN_GLOBAL_ALIGNMENT_COVERAGE = 0.20

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
VISUAL_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
AUDIO_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"}
TRANSITION_VIDEO_EXTENSIONS = VIDEO_EXTENSIONS | {".avi"}
# The audited overlay packs top out at 2.28s. Preserve their native A/V arc
# while still rejecting an accidentally long clip from dominating a cut.
MAX_TRANSITION_DURATION = 2.5
MAX_SCENE_DURATION = 4.0
MAX_TEMPLATE_12_SCENE_DURATION = 6.0
MAX_SCENE_RENDER_WORKERS = 2
SCENE_RENDER_TIMEOUT_SECONDS = 300
# A versao 6 invalida clips sem a digitação sequencial e captions em duas linhas.
SCENE_RESUME_MANIFEST_VERSION = 6
SCENE_RESUME_DURATION_TOLERANCE = (1 / FPS) + 0.001

TEMPLATE_MEDIA_COUNT = TEMPLATE_MEDIA_COUNTS
TEXT_TEMPLATES = TEMPLATES_COM_TEXTO
TEXT_REQUIRED_TEMPLATES = TEMPLATES_COM_TEXTO
TEMPLATES_COM_DIGITACAO = frozenset({4, 6, 7, 8, 9, 11})
TEMPLATES_COM_FUNDO_ESTATICO = frozenset({3, 4, 7, 8, 9, 10, 11, 12})
SOURCE_KEYS = ("fonte_midia", "fonte", "source_media", "media_source")


class AuditoriaHITLError(RuntimeError):
    """Raised before synthesis when human-curated assets are incomplete."""


class TempoCenaExcedidoError(RuntimeError):
    """Raised when acoustic timing violates the YouTube retention contract."""


@dataclass(frozen=True)
class MidiaAuditada:
    indice_slot: int
    letra: str | None
    fonte_midia: str
    papel_layout: str
    path: Path
    tipo: str
    width: int = 0
    height: int = 0


@dataclass(frozen=True)
class CenaAuditada:
    indice: int
    template_id: int
    texto: str
    textos_tela: tuple[str, ...]
    midias: tuple[MidiaAuditada, ...]
    fundo_estatico: Path | None = None

    @property
    def caminhos_midias(self) -> dict[str, str]:
        return {midia.papel_layout: str(midia.path) for midia in self.midias}


@dataclass(frozen=True)
class CenaTemporizada:
    cena: CenaAuditada
    inicio: float
    fim_fala: float
    fim: float
    cobertura_whisper: float

    @property
    def duracao(self) -> float:
        return round(self.fim - self.inicio, 3)


@dataclass(frozen=True)
class AssetGlobal:
    nome: str
    path: Path
    stream_type: str
    exige_duracao: bool = True


@dataclass(frozen=True)
class TrilhaComDuracao:
    """Faixa musical validada para a playlist dinamica da execucao."""

    path: Path
    duracao: float


@dataclass(frozen=True)
class TransicaoHorizontal:
    path: Path
    duracao_video: float | None
    duracao_audio: float | None
    canais_audio: int | None


@dataclass(frozen=True)
class TransicaoSelecionada:
    path: Path
    input_index: int
    corte: float
    inicio_visual: float | None
    duracao_video: float | None
    duracao_audio: float | None
    canais_audio: int | None


@dataclass(frozen=True)
class EventoEscrita:
    """Trecho curto de som de escrita sincronizado a uma cena textual."""

    inicio: float
    duracao: float


@dataclass(frozen=True)
class TrabalhoRenderizacaoCena:
    """Contexto serializavel de uma unica cena enviado a um worker."""

    cena_temporizada: CenaTemporizada
    metadata_cena: dict[str, Any]
    clip_path: Path
    manifest_path: Path
    ffmpeg: str
    ffprobe: str
    fonte: Path | None
    seta_path: Path
    ordem_render: int = 0
    cor_texto: str = "black"
    borda_texto: bool = True
    cor_borda_texto: str = "white"


@dataclass(frozen=True)
class ResultadoRenderizacaoCena:
    indice_cena: int
    clip_path: Path
    duracao: float
    reaproveitada: bool


def _estilo_texto_template(policy: object, template_id: int) -> tuple[str, bool, str]:
    data = policy if isinstance(policy, dict) else {}
    styles = data.get("styles") if isinstance(data.get("styles"), dict) else {}
    key = "template_4" if template_id == 4 else "template_6" if template_id == 6 else "others"
    style = styles.get(key) if isinstance(styles.get(key), dict) else data
    style = style if isinstance(style, dict) else {}
    return (
        LayoutFactory._normalizar_cor_texto(str(style.get("color", data.get("color", "black")))),
        bool(style.get("border_enabled", data.get("border_enabled", True))),
        LayoutFactory._normalizar_cor_texto(str(style.get("border_color", data.get("border_color", "white")))),
    )


def _listar_assets_dinamicos(diretorio: Path, extensoes: set[str]) -> list[Path]:
    """Lista arquivos nao vazios por extensao em toda a arvore de assets."""

    extensoes_normalizadas = {extensao.lower() for extensao in extensoes}
    candidatos: list[Path] = []
    if diretorio.is_dir():
        for path in diretorio.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in extensoes_normalizadas:
                continue
            try:
                if path.stat().st_size > 0:
                    candidatos.append(path.resolve())
            except OSError:
                continue

    if not candidatos:
        raise AuditoriaHITLError(
            f"O diretório {diretorio} não contém arquivos válidos para sorteio."
        )
    return sorted(candidatos, key=lambda path: str(path).casefold())


def _sortear_asset_dinamico(diretorio: Path, extensoes: set[str]) -> Path:
    """Compatibilidade para consumidores que precisam de somente um asset."""

    return random.choice(_listar_assets_dinamicos(diretorio, extensoes))


def _sortear_assets_globais() -> tuple[tuple[AssetGlobal, ...], AssetGlobal]:
    """Mapeia todas as trilhas candidatas e a seta persistente da execucao."""

    trilhas = tuple(
        AssetGlobal(f"trilha documental {indice:02d}", path, "audio")
        for indice, path in enumerate(
            _listar_assets_dinamicos(TRILHAS_DIR, AUDIO_EXTENSIONS),
            start=1,
        )
    )
    if not SETA_APONTAMENTO_PATH.is_file() or SETA_APONTAMENTO_PATH.stat().st_size <= 0:
        raise AuditoriaHITLError(
            "Asset obrigatorio da seta nao encontrado ou vazio: "
            f"{SETA_APONTAMENTO_PATH}"
        )
    seta = AssetGlobal(
        "seta persistente do template de apontamento",
        SETA_APONTAMENTO_PATH.resolve(),
        "video",
        exige_duracao=False,
    )
    return trilhas, seta


def _normalizar_trilhas_globais(
    value: AssetGlobal | Sequence[AssetGlobal],
) -> tuple[AssetGlobal, ...]:
    """Aceita a forma legada de uma trilha durante a transicao para playlist."""

    if isinstance(value, AssetGlobal):
        return (value,)
    trilhas = tuple(value)
    if not trilhas:
        raise AuditoriaHITLError("Nenhuma trilha documental foi encontrada para a playlist.")
    if any(asset.stream_type != "audio" for asset in trilhas):
        raise AuditoriaHITLError("A playlist horizontal aceita somente assets globais de audio.")
    return trilhas


def _mapear_fundos_estaticos(ffprobe: str) -> list[Path]:
    """Retorna somente imagens de fundo que o ffprobe confirmou como visuais.

    Fundos estaticos sao assets persistentes, nao midias de cena. Arquivos
    corrompidos ou de outro tipo podem coexistir na pasta, mas nunca entram no
    sorteio. Quando nao resta nenhuma imagem utilizavel, a renderizacao para
    com uma mensagem HITL antes de chamar o FFmpeg de uma cena.
    """

    if not FUNDOS_ESTATICOS_DIR.is_dir():
        raise AuditoriaHITLError(
            "Pasta obrigatoria de fundos estaticos nao encontrada: "
            f"{FUNDOS_ESTATICOS_DIR}"
        )

    candidatos: list[Path] = []
    for path in sorted(FUNDOS_ESTATICOS_DIR.rglob("*"), key=lambda item: str(item).casefold()):
        if not path.is_file() or path.suffix.casefold() not in IMAGE_EXTENSIONS:
            continue
        try:
            if path.stat().st_size > 0:
                candidatos.append(path.resolve())
        except OSError:
            continue

    if not candidatos:
        extensoes = ", ".join(sorted(IMAGE_EXTENSIONS))
        raise AuditoriaHITLError(
            "Nenhuma imagem de fundo estatica utilizavel foi encontrada em "
            f"{FUNDOS_ESTATICOS_DIR}. Adicione um arquivo nao vazio "
            f"({extensoes}) para os templates 3, 4, 7, 8, 9, 10, 11 e 12."
        )

    validos: list[Path] = []
    erros: list[str] = []
    for path in candidatos:
        try:
            payload = _ffprobe_json(path, ffprobe)
            videos = _streams(payload, "video")
            if not videos:
                raise RuntimeError("nenhum stream visual encontrado")
            width = int(videos[0].get("width") or 0)
            height = int(videos[0].get("height") or 0)
            if width <= 0 or height <= 0:
                raise RuntimeError("dimensoes reais invalidas no header")
        except (RuntimeError, TypeError, ValueError) as exc:
            erros.append(f"{path.name}: {exc}")
            continue
        validos.append(path)

    logger = get_logger(__name__)
    for erro in erros:
        logger.warning("Horizontal: fundo estatico ignorado: %s", erro)

    if not validos:
        detalhes = "\n".join(f"- {erro}" for erro in erros)
        raise AuditoriaHITLError(
            "Nenhuma imagem valida foi encontrada em fundos_estaticos para os "
            "templates 3, 4, 7, 8, 9, 10, 11 e 12."
            + (f"\n{detalhes}" if detalhes else "")
        )
    return validos


def _atribuir_fundos_estaticos(
    cenas: Sequence[CenaAuditada],
    ffprobe: str,
    *,
    fundo_padrao: Path | None = None,
    fundos_por_template: dict[int, Path] | None = None,
    fundos_por_cena: dict[int, Path] | None = None,
) -> list[CenaAuditada]:
    """Sorteia um fundo persistente para cada cena que o template exige."""

    if not any(
        cena.template_id in TEMPLATES_COM_FUNDO_ESTATICO
        for cena in cenas
    ):
        return list(cenas)

    fundos = _mapear_fundos_estaticos(ffprobe)
    fundos_validos = {path.resolve() for path in fundos}
    if fundo_padrao is not None and fundo_padrao.resolve() not in fundos_validos:
        raise AuditoriaHITLError(f"Fundo estático selecionado não é válido: {fundo_padrao}")
    mapeamento = fundos_por_template or {}
    mapeamento_cenas = fundos_por_cena or {}
    for template_id, fundo in mapeamento.items():
        if template_id not in TEMPLATES_COM_FUNDO_ESTATICO:
            raise AuditoriaHITLError(f"Template {template_id} não aceita fundo estático.")
        if fundo.resolve() not in fundos_validos:
            raise AuditoriaHITLError(f"Fundo selecionado para template {template_id} não é válido: {fundo}")
    for indice_cena, fundo in mapeamento_cenas.items():
        if indice_cena < 1:
            raise AuditoriaHITLError("Índice de cena do fundo deve começar em 1.")
        if fundo.resolve() not in fundos_validos:
            raise AuditoriaHITLError(f"Fundo selecionado para cena {indice_cena:02d} não é válido: {fundo}")
    logger = get_logger(__name__)
    resultado: list[CenaAuditada] = []
    for cena in cenas:
        if cena.template_id not in TEMPLATES_COM_FUNDO_ESTATICO:
            resultado.append(cena)
            continue

        fundo = mapeamento_cenas.get(cena.indice) or mapeamento.get(cena.template_id) or fundo_padrao or _selecionar_fundo_estatico_deterministico(cena, fundos)
        logger.info(
            "Horizontal: cena %02d template=%s usara fundo estatico=%s",
            cena.indice,
            cena.template_id,
            fundo.name,
        )
        resultado.append(replace(cena, fundo_estatico=fundo))
    return resultado


def _selecionar_fundo_estatico_deterministico(
    cena: CenaAuditada,
    fundos: Sequence[Path],
) -> Path:
    """Escolhe um fundo estavel para que o cache de resume seja reproduzivel."""

    if not fundos:
        raise AuditoriaHITLError("Pool de fundos estaticos vazio.")
    chave = f"{cena.indice}:{cena.template_id}:{cena.texto}".encode("utf-8")
    indice = int(hashlib.sha256(chave).hexdigest()[:16], 16) % len(fundos)
    return fundos[indice]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Renderiza um diretorio de tema horizontal previamente curado."
    )
    parser.add_argument(
        "diretorio_tema",
        type=Path,
        help="Ex.: workspace/lotes_horizontais/historia/roma/",
    )
    parser.add_argument(
        "--manter-artefatos",
        action="store_true",
        help="Mantem audio mestre, clipes e manifest de concatenacao para auditoria.",
    )
    return parser.parse_args()


def _ler_curadoria_orchestrator() -> dict[str, Any]:
    """Lê somente o contrato temporário criado pelo Studio, se houver.

    O renderer continua utilizável pelo terminal sem manifesto; nesse caso os
    pools persistentes mantêm o comportamento padrão.
    """

    valor = os.environ.get("SYNTHREEL_PROJECT_MANIFEST", "").strip()
    if not valor:
        return {}
    caminho = Path(valor)
    try:
        payload = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditoriaHITLError(f"Manifesto de curadoria inválido: {caminho}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("pipeline") != "horizontal":
        raise AuditoriaHITLError("Manifesto de curadoria não pertence à esteira horizontal.")
    return payload


def renderizar_horizontal(
    diretorio_tema: str | Path,
    *,
    manter_artefatos: bool = False,
    tts_engine: TTSNeuralEngine | None = None,
    whisper_sync: WhisperSync | None = None,
    ffmpeg_bin: str | None = None,
    ffprobe_bin: str | None = None,
    output_root: str | Path = OUTPUT_HORIZONTAL_DIR,
) -> dict[str, Any]:
    """Executa a fase horizontal sob exclusao mutua por nicho/tema."""

    _, nicho_slug, tema_slug = _validar_diretorio_tema(diretorio_tema)
    caminho_lock = _caminho_lock_tema(nicho_slug, tema_slug)
    with TravaTema.adquirir(caminho_lock):
        return _renderizar_horizontal_sem_lock(
            diretorio_tema,
            manter_artefatos=manter_artefatos,
            tts_engine=tts_engine,
            whisper_sync=whisper_sync,
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
            output_root=output_root,
        )


def _renderizar_horizontal_sem_lock(
    diretorio_tema: str | Path,
    *,
    manter_artefatos: bool = False,
    tts_engine: TTSNeuralEngine | None = None,
    whisper_sync: WhisperSync | None = None,
    ffmpeg_bin: str | None = None,
    ffprobe_bin: str | None = None,
    output_root: str | Path = OUTPUT_HORIZONTAL_DIR,
) -> dict[str, Any]:
    """Executa a fase horizontal assumindo que a trava do tema ja existe."""

    logger = get_logger(__name__)
    tema_dir, nicho_slug, tema_slug = _validar_diretorio_tema(diretorio_tema)
    metadata_path = tema_dir / METADATA_FILENAME
    metadata = _ler_metadata(metadata_path)

    # HITL is intentionally the first material phase. A missing IA asset is
    # the sole exception: it is resolved to a local landscape Pexels video.
    cenas = auditar_hitl(tema_dir, metadata)
    trilhas_globais, seta = _sortear_assets_globais()
    curadoria = _ler_curadoria_orchestrator()
    politica_texto = curadoria.get("text_policy", {})
    if not isinstance(politica_texto, dict):
        raise AuditoriaHITLError("Política de texto do manifesto é inválida.")
    try:
        estilos_texto = {
            template: _estilo_texto_template(politica_texto, template)
            for template in (1, 4, 6)
        }
    except ValueError as exc:
        raise AuditoriaHITLError(str(exc)) from exc
    musica_escolhida = curadoria.get("music_track")
    if musica_escolhida:
        caminho_musica = Path(musica_escolhida)
        if not caminho_musica.is_file() or caminho_musica.stat().st_size <= 0:
            raise AuditoriaHITLError(f"Trilha selecionada ausente ou vazia: {caminho_musica}")
        trilhas_globais = (AssetGlobal("trilha selecionada no Studio", caminho_musica.resolve(), "audio"),)
    trilhas_globais = _normalizar_trilhas_globais(trilhas_globais)
    logger.info(
        "Horizontal: assets globais mapeados: trilhas=%s seta=%s",
        len(trilhas_globais),
        seta.path.name,
    )

    ffmpeg = _resolver_executavel(ffmpeg_bin or settings.ffmpeg_bin, "FFmpeg")
    ffprobe = _resolver_executavel(ffprobe_bin or settings.ffprobe_bin, "ffprobe")
    cenas = _sondar_midias_cenas(cenas, ffprobe)
    _sondar_assets_globais((seta,), ffprobe)
    politica_fundos = curadoria.get("background_policy", {})
    if not isinstance(politica_fundos, dict):
        raise AuditoriaHITLError("Política de fundos do manifesto é inválida.")
    fundo_padrao = Path(politica_fundos["default"]) if politica_fundos.get("default") else None
    mapa_bruto = politica_fundos.get("by_template", {})
    if not isinstance(mapa_bruto, dict):
        raise AuditoriaHITLError("Mapa de fundos por template é inválido.")
    try:
        fundos_por_template = {int(template): Path(path) for template, path in mapa_bruto.items()}
    except (TypeError, ValueError) as exc:
        raise AuditoriaHITLError("Mapa de fundos por template contém uma entrada inválida.") from exc
    mapa_por_cena_bruto = politica_fundos.get("by_scene", {})
    if not isinstance(mapa_por_cena_bruto, dict):
        raise AuditoriaHITLError("Mapa de fundos por cena é inválido.")
    try:
        fundos_por_cena = {int(cena): Path(path) for cena, path in mapa_por_cena_bruto.items()}
    except (TypeError, ValueError) as exc:
        raise AuditoriaHITLError("Mapa de fundos por cena contém uma entrada inválida.") from exc
    cenas = _atribuir_fundos_estaticos(
        cenas,
        ffprobe,
        fundo_padrao=fundo_padrao,
        fundos_por_template=fundos_por_template,
        fundos_por_cena=fundos_por_cena,
    )
    pool_transicoes = _mapear_pool_transicoes_horizontal(ffprobe)
    transicoes_escolhidas = {Path(path).resolve() for path in curadoria.get("transition_pool", [])}
    if transicoes_escolhidas:
        pool_transicoes = [item for item in pool_transicoes if item.path in transicoes_escolhidas]
        if not pool_transicoes:
            raise AuditoriaHITLError("Nenhuma transição selecionada passou na auditoria audiovisual.")

    fonte = _resolver_fonte_deterministica(cenas)
    run_dir = _criar_diretorio_execucao(nicho_slug, tema_slug)
    logger.info("Horizontal: artefatos desta execucao em %s", run_dir)

    try:
        textos = [cena.texto for cena in cenas]
        texto_mestre = "\n\n".join(_texto_tts_com_fim_de_frase(texto) for texto in textos)
        idioma = _idioma_metadata(metadata)
        audio_mestre = run_dir / "narracao_mestre.mp3"

        logger.info("Horizontal: sintetizando narracao mestre com TTS neural rate=-10%%")
        tts = tts_engine or TTSNeuralEngine()
        audio_gerado = Path(
            tts.sintetizar_sync(
                texto=texto_mestre,
                caminho_saida=audio_mestre,
                idioma=idioma,
            )
        ).resolve()
        _validar_arquivo_nao_vazio(audio_gerado, "audio mestre do TTS")
        duracao_audio = _duracao_audio(audio_gerado, ffprobe)

        logger.info("Horizontal: extraindo timestamps do audio mestre com Whisper local")
        whisper = whisper_sync or WhisperSync()
        timestamps = whisper.extrair_timestamps(audio_gerado)
        cenas_temporizadas = alinhar_cenas_com_whisper(cenas, timestamps, duracao_audio)
        validar_duracao_maxima_cenas(cenas_temporizadas)
        trilhas_validas = _mapear_trilhas_validas(trilhas_globais, ffprobe)
        playlist_trilhas = _montar_playlist_dinamica(trilhas_validas, duracao_audio)
        logger.info(
            "Horizontal: playlist dinamica com %s faixas (%.3fs para mestre de %.3fs): %s",
            len(playlist_trilhas),
            sum(trilha.duracao for trilha in playlist_trilhas),
            duracao_audio,
            ", ".join(trilha.path.name for trilha in playlist_trilhas),
        )

        # Run the encoder/filter preflight only after acoustic timing passes.
        # A retention-contract failure must abort before any scene FFmpeg job.
        opcoes_encoder_compilacao_final = _validar_recursos_ffmpeg(ffmpeg, run_dir)
        logger.info(
            "Horizontal: encoder da compilacao final selecionado: %s",
            _nome_encoder_video(opcoes_encoder_compilacao_final),
        )

        trabalhos_cenas = tuple(
            TrabalhoRenderizacaoCena(
                cena_temporizada=cena_temporizada,
                ordem_render=ordem,
                metadata_cena=_metadata_cena_para_resume(
                    metadata,
                    cena_temporizada.cena.indice,
                ),
                clip_path=run_dir
                / f"cena_{ordem:02d}_render.mp4",
                manifest_path=_caminho_manifest_cena(
                    run_dir,
                    ordem,
                ),
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
                fonte=fonte,
                seta_path=seta.path,
                cor_texto=estilos_texto[4 if cena_temporizada.cena.template_id == 4 else 6 if cena_temporizada.cena.template_id == 6 else 1][0],
                borda_texto=estilos_texto[4 if cena_temporizada.cena.template_id == 4 else 6 if cena_temporizada.cena.template_id == 6 else 1][1],
                cor_borda_texto=estilos_texto[4 if cena_temporizada.cena.template_id == 4 else 6 if cena_temporizada.cena.template_id == 6 else 1][2],
            )
            for ordem, cena_temporizada in enumerate(cenas_temporizadas, start=1)
        )
        resultados_cenas = _renderizar_trabalhos_cenas_concorrentes(trabalhos_cenas)
        clipes = [resultado.clip_path for resultado in resultados_cenas]
        duracoes_clipes = [resultado.duracao for resultado in resultados_cenas]

        manifest = run_dir / "cenas.ffconcat"
        _escrever_manifest_concat(manifest, clipes, duracoes_clipes)
        cortes = _calcular_cortes_reais(duracoes_clipes)
        atribuicoes_brutas = curadoria.get("transition_assignments", {})
        if not isinstance(atribuicoes_brutas, dict):
            raise AuditoriaHITLError("Atribuições de transição do manifesto são inválidas.")
        try:
            atribuicoes_transicoes = {
                int(corte): Path(caminho)
                for corte, caminho in atribuicoes_brutas.items()
                if isinstance(caminho, str) and caminho
            }
        except (TypeError, ValueError) as exc:
            raise AuditoriaHITLError("Atribuições de transição possuem um corte inválido.") from exc
        transicoes = _selecionar_transicoes_horizontais(
            pool_transicoes,
            cortes,
            duracao_audio,
            primeiro_input=2 + len(playlist_trilhas),
            atribuicoes=atribuicoes_transicoes,
        )

        output_dir = Path(output_root).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        output_final = output_dir / f"{nicho_slug}_{tema_slug}.mp4"
        # O resultado parcial fica no mesmo volume do destino. Isso preserva
        # ``os.replace`` atomico mesmo quando os clips temporarios estao fora
        # do escopo do OneDrive ou em outro disco.
        output_parcial = output_dir / f".{output_final.stem}.{uuid4().hex}.part.mp4"

        logger.info(
            "Horizontal: compondo video final com ducking e %s transicoes dinamicas",
            len(transicoes),
        )
        _renderizar_compilacao_final(
            manifest=manifest,
            audio_mestre=audio_gerado,
            output_path=output_parcial,
            duracao=duracao_audio,
            transicoes=transicoes,
            eventos_escrita=_eventos_escrita(cenas_temporizadas),
            ffmpeg=ffmpeg,
            trilhas_paths=tuple(trilha.path for trilha in playlist_trilhas),
            opcoes_encoder_video=opcoes_encoder_compilacao_final,
        )
        _validar_video_final(output_parcial, ffprobe, duracao_audio)

        # replace is atomic on the same workspace volume and leaves a previous
        # successful output untouched if FFmpeg fails before this point.
        replace_com_retry(output_parcial, output_final)

        resumo = {
            "status": "ok",
            "diretorio_tema": str(tema_dir),
            "metadata": str(metadata_path),
            "nicho": nicho_slug,
            "tema": tema_slug,
            "cenas": len(cenas_temporizadas),
            "duracao_audio": round(duracao_audio, 3),
            "playlist_trilhas": [str(trilha.path) for trilha in playlist_trilhas],
            "fundos_estaticos": [
                {
                    "cena": cena_temporizada.cena.indice,
                    "asset": str(cena_temporizada.cena.fundo_estatico),
                }
                for cena_temporizada in cenas_temporizadas
                if cena_temporizada.cena.fundo_estatico is not None
            ],
            "cortes": [round(corte, 3) for corte in cortes],
            "transicoes": [
                {
                    "corte": round(transicao.corte, 3),
                    "asset": str(transicao.path),
                }
                for transicao in transicoes
            ],
            "video_final": str(output_final),
            "artefatos": str(run_dir) if manter_artefatos else None,
        }
        logger.info("Horizontal: video final salvo em %s", output_final)

        if not manter_artefatos:
            try:
                _limpar_diretorio_execucao(run_dir)
            except OSError as exc:
                # The final output is already atomically installed. A transient
                # OneDrive/antivirus lock on diagnostics must not turn success
                # into a false render failure.
                logger.warning(
                    "Horizontal: video concluido, mas artefatos nao puderam ser removidos: %s",
                    exc,
                )
                resumo["artefatos"] = str(run_dir)

        try:
            _limpar_lote_horizontal_concluido(tema_dir, output_final)
        except (OSError, RuntimeError) as exc:
            # The validated output is already installed. A cleanup failure
            # must not turn a completed render into a false processing error.
            logger.warning(
                "Horizontal: video concluido, mas o lote temporario nao pode ser removido: %s",
                exc,
            )
        else:
            logger.info("Horizontal: lote temporario removido apos sucesso: %s", tema_dir)
        return resumo
    except Exception:
        logger.error(
            "Horizontal: renderizacao interrompida; lote curado preservado. "
            "Artefatos de diagnostico: %s",
            run_dir,
        )
        raise


def _validar_diretorio_tema(diretorio_tema: str | Path) -> tuple[Path, str, str]:
    tema_dir = Path(diretorio_tema).resolve()
    if not tema_dir.exists() or not tema_dir.is_dir():
        raise FileNotFoundError(f"Diretorio do tema nao encontrado: {tema_dir}")

    raiz = LOTES_HORIZONTAIS_DIR.resolve()
    try:
        relativo = tema_dir.relative_to(raiz)
    except ValueError as exc:
        raise ValueError(
            "O renderer horizontal aceita somente temas dentro de "
            f"{raiz}; recebido: {tema_dir}"
        ) from exc

    if len(relativo.parts) != 2:
        raise ValueError(
            "Passe o diretorio direto do tema no formato "
            "workspace/lotes_horizontais/{nicho}/{tema}."
        )

    metadata_path = tema_dir / METADATA_FILENAME
    if not metadata_path.is_file():
        raise FileNotFoundError(f"metadata.json nao encontrado no tema: {metadata_path}")

    return tema_dir, _slug(relativo.parts[0]), _slug(relativo.parts[1])


def _ler_metadata(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"metadata.json invalido em {path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"metadata.json deve ser um objeto JSON: {path}")
    return payload


def auditar_hitl(
    tema_dir: Path,
    metadata: dict[str, Any],
    *,
    pexels_fetcher: PexelsFetcher | None = None,
) -> list[CenaAuditada]:
    """Validates scene slots and resolves missing IA assets with Pexels video."""

    cenas_raw = metadata.get("cenas")
    if not isinstance(cenas_raw, list) or not cenas_raw:
        raise AuditoriaHITLError("metadata.json precisa conter uma lista nao vazia em 'cenas'.")

    logger = get_logger(__name__)
    arquivos = [path for path in tema_dir.iterdir() if path.is_file()]
    pexels = pexels_fetcher or PexelsFetcher(logger=logger)
    midias_pexels_usadas: set[str] = set()
    erros: list[str] = []
    auditadas: list[CenaAuditada] = []

    for indice, cena_raw in enumerate(cenas_raw, start=1):
        prefixo = f"cena {indice:02d}"
        if not isinstance(cena_raw, dict):
            erros.append(f"{prefixo}: deve ser um objeto JSON.")
            continue

        texto = cena_raw.get("texto")
        if not isinstance(texto, str) or not texto.strip():
            erros.append(f"{prefixo}: campo obrigatorio 'texto' ausente ou vazio.")
            continue
        if not _tokenizar(texto):
            erros.append(f"{prefixo}: 'texto' nao possui palavras validas para sincronizacao.")
            continue

        try:
            template_id = _template_id_estrito(cena_raw["template_id"])
        except (KeyError, TypeError, ValueError):
            erros.append(f"{prefixo}: 'template_id' obrigatorio deve ser inteiro entre 1 e 12.")
            continue
        if template_id not in TEMPLATE_MEDIA_COUNT:
            erros.append(f"{prefixo}: template_id invalido {template_id}; use 1 a 12.")
            continue

        try:
            textos_tela = _textos_tela(cena_raw)
        except AuditoriaHITLError as exc:
            erros.append(f"{prefixo}: {exc}")
            continue
        if template_id in TEXT_REQUIRED_TEMPLATES and not any(textos_tela):
            erros.append(
                f"{prefixo}: template {template_id} exige 'textos_tela' explicito e nao vazio."
            )
            continue
        if template_id in {11, 12}:
            try:
                _validar_topicos_template_lista(textos_tela, template_id)
            except AuditoriaHITLError as exc:
                erros.append(f"{prefixo}: {exc}")
                continue

        try:
            slots = _slots_da_cena(cena_raw)
        except (TypeError, ValueError) as exc:
            erros.append(f"{prefixo}: contrato de midia invalido: {exc}")
            continue

        esperado = TEMPLATE_MEDIA_COUNT[template_id]
        if len(slots) != esperado:
            erros.append(
                f"{prefixo}: template {template_id} exige exatamente {esperado} midias; "
                f"metadata declarou {len(slots)}."
            )
            continue

        if not _fontes_explicitas_para_slots(cena_raw, len(slots)):
            erros.append(
                f"{prefixo}: 'fonte_midia' deve ser explicita em todos os slots; "
                "nao ha fallback no renderer."
            )

        papeis = _papeis_layout(template_id, len(slots))
        midias: list[MidiaAuditada] = []
        for slot, papel in zip(slots, papeis, strict=True):
            try:
                midia = _auditar_slot_midia(
                    tema_dir=tema_dir,
                    arquivos=arquivos,
                    indice_cena=indice,
                    total_slots=len(slots),
                    slot=slot,
                    papel_layout=papel,
                    pexels_fetcher=pexels,
                    midias_pexels_usadas=midias_pexels_usadas,
                    logger=logger,
                )
            except AuditoriaHITLError as exc:
                erros.append(str(exc))
                continue
            if slot.fonte_midia == "ia" and midia.fonte_midia == "pexels":
                _registrar_fallback_pexels(cena_raw, slot, midia.path)
            midias.append(midia)

        if len(midias) != len(slots):
            continue

        auditadas.append(
            CenaAuditada(
                indice=indice,
                template_id=template_id,
                texto=texto.strip(),
                textos_tela=textos_tela,
                midias=tuple(midias),
            )
        )

    if erros:
        detalhes = "\n".join(f"- {erro}" for erro in erros)
        raise AuditoriaHITLError(
            "Auditoria HITL bloqueou a renderizacao. Corrija os itens abaixo:\n" + detalhes
        )

    return auditadas


def _fontes_explicitas_para_slots(cena: dict[str, Any], total_slots: int) -> bool:
    midias = cena.get("midias")
    if midias is None:
        midias = cena.get("medias")
    for index in range(total_slots):
        item = midias[index] if isinstance(midias, list) and index < len(midias) else None
        fonte_item = None
        if isinstance(item, dict):
            fonte_item = _primeiro_valor_fonte(item, index=None)
        if _valor_fonte_preenchido(fonte_item):
            continue

        fonte_cena = _primeiro_valor_fonte(cena, index=index)
        if not _valor_fonte_preenchido(fonte_cena):
            return False
    return True


def _primeiro_valor_fonte(mapping: dict[str, Any], index: int | None) -> Any:
    for key in SOURCE_KEYS:
        if key not in mapping:
            continue
        value = mapping[key]
        if index is None:
            return value
        if isinstance(value, list):
            return value[index] if index < len(value) else None
        if isinstance(value, dict):
            letra = chr(ord("A") + index) if index < 26 else str(index + 1)
            for slot_key in (letra, letra.lower(), str(index + 1), index):
                if slot_key in value:
                    return value[slot_key]
            return None
        return value
    return None


def _valor_fonte_preenchido(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return False


def _template_id_estrito(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("booleano nao e template_id")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
        return int(value.strip())
    raise ValueError("template_id deve ser inteiro")


def _auditar_slot_midia(
    *,
    tema_dir: Path,
    arquivos: list[Path],
    indice_cena: int,
    total_slots: int,
    slot: SlotMidia,
    papel_layout: str,
    pexels_fetcher: PexelsFetcher,
    midias_pexels_usadas: set[str],
    logger: Any,
) -> MidiaAuditada:
    slot_nome = f" slot {slot.letra}" if slot.letra else ""
    contexto = f"cena {indice_cena:02d}{slot_nome}"
    base = _base_slot(indice_cena, slot.letra)

    if slot.fonte_midia == "ia":
        prompt_nome = f"{base}_PROMPT_IA.txt"
        prompts = [path for path in arquivos if path.name.casefold() == prompt_nome.casefold()]
        candidatos = [
            path
            for path in arquivos
            if path.suffix.lower() in VISUAL_EXTENSIONS
            and _corresponde_asset_ia(path, base, total_slots)
        ]
        if candidatos:
            if prompts:
                raise AuditoriaHITLError(
                    f"{contexto}: prompt IA ainda existe ({prompts[0].name}). "
                    "Apague o TXT somente depois de colocar a imagem final."
                )
            return _midia_unica(
                candidatos,
                contexto=contexto,
                fonte="ia",
                papel_layout=papel_layout,
                indice_slot=slot.indice,
                letra=slot.letra,
                esperada=f"{base}.jpg/.png/.mp4 (ou {base}_*.jpg/.png/.mp4)",
            )

        fallback_pexels = [
            path
            for path in arquivos
            if path.suffix.lower() == ".mp4"
            and path.stem.casefold() == f"{base}_pexels".casefold()
        ]
        if fallback_pexels:
            return _midia_unica(
                fallback_pexels,
                contexto=contexto,
                fonte="pexels",
                papel_layout=papel_layout,
                indice_slot=slot.indice,
                letra=slot.letra,
                esperada=f"{base}_pexels.mp4",
            )

        logger.warning(
            "%s: midia IA ausente; usando video Pexels de fallback em landscape.",
            contexto,
        )
        fallback = _baixar_fallback_pexels(
            tema_dir=tema_dir,
            base=base,
            contexto=contexto,
            slot=slot,
            papel_layout=papel_layout,
            pexels_fetcher=pexels_fetcher,
            midias_pexels_usadas=midias_pexels_usadas,
        )
        arquivos.append(fallback.path)
        return fallback

    if slot.fonte_midia in {"pexels", "local"}:
        fonte = slot.fonte_midia
        stem_esperado = f"{base}_{fonte}".casefold()
        candidatos = [
            path
            for path in arquivos
            if path.suffix.lower() in VISUAL_EXTENSIONS
            and path.stem.casefold() == stem_esperado
        ]
        return _midia_unica(
            candidatos,
            contexto=contexto,
            fonte=fonte,
            papel_layout=papel_layout,
            indice_slot=slot.indice,
            letra=slot.letra,
            esperada=(
                f"{base}_local.mp4/.jpg/.jpeg/.png"
                if fonte == "local"
                else f"{base}_pexels.mp4/.jpg/.png"
            ),
        )

    raise AuditoriaHITLError(
        f"{contexto}: fonte_midia invalida '{slot.fonte_midia}'. "
        "Use 'pexels', 'ia' ou 'local'."
    )


def _baixar_fallback_pexels(
    *,
    tema_dir: Path,
    base: str,
    contexto: str,
    slot: SlotMidia,
    papel_layout: str,
    pexels_fetcher: PexelsFetcher,
    midias_pexels_usadas: set[str],
) -> MidiaAuditada:
    """Downloads a landscape Pexels video and normalizes its scene filename."""

    try:
        media = pexels_fetcher.obter_midia_para_cena(
            query=slot.prompt_ou_busca,
            midias_usadas=midias_pexels_usadas,
            storage_path=tema_dir,
            orientacao="landscape",
        )
    except (PexelsFetcherError, OSError, ValueError) as exc:
        raise AuditoriaHITLError(f"{contexto}: fallback Pexels falhou: {exc}") from exc

    if str(media.get("tipo", "")).casefold() != "video":
        raise AuditoriaHITLError(
            f"{contexto}: fallback Pexels nao retornou video landscape para '{slot.prompt_ou_busca}'."
        )

    origem = Path(str(media.get("path_local", "")))
    destino = tema_dir / f"{base}_pexels.mp4"
    try:
        _validar_arquivo_nao_vazio(origem, f"fallback Pexels de {contexto}", erro_hitl=True)
        if origem.suffix.lower() != ".mp4":
            raise AuditoriaHITLError(f"{contexto}: fallback Pexels retornou arquivo nao MP4.")
        if origem.resolve() != destino.resolve():
            if destino.exists():
                raise AuditoriaHITLError(f"{contexto}: destino do fallback ja existe: {destino.name}")
            replace_com_retry(origem, destino)
        _validar_arquivo_nao_vazio(destino, f"fallback Pexels de {contexto}", erro_hitl=True)
    except OSError as exc:
        raise AuditoriaHITLError(f"{contexto}: nao foi possivel salvar fallback Pexels: {exc}") from exc

    media_id = str(media.get("id", "")).strip()
    if media_id:
        midias_pexels_usadas.add(media_id)

    return MidiaAuditada(
        indice_slot=slot.indice,
        letra=slot.letra,
        fonte_midia="pexels",
        papel_layout=papel_layout,
        path=destino.resolve(),
        tipo="video",
    )


def _registrar_fallback_pexels(cena: dict[str, Any], slot: SlotMidia, path: Path) -> None:
    """Records the effective local asset in the in-memory scene contract."""

    caminho = str(path.resolve())
    midias = cena.get("midias")
    if midias is None:
        midias = cena.get("medias")
    if isinstance(midias, list) and slot.indice <= len(midias):
        item = midias[slot.indice - 1]
        if isinstance(item, dict):
            item["caminho_local"] = caminho
            item["fonte_midia_resolvida"] = "pexels"
            return

    resolvidas = cena.setdefault("_midias_resolvidas", {})
    if isinstance(resolvidas, dict):
        resolvidas[slot.letra or "principal"] = caminho


def _midia_unica(
    candidatos: Sequence[Path],
    *,
    contexto: str,
    fonte: str,
    papel_layout: str,
    indice_slot: int,
    letra: str | None,
    esperada: str,
) -> MidiaAuditada:
    if not candidatos:
        raise AuditoriaHITLError(f"{contexto}: midia ausente; esperado {esperada}.")
    if len(candidatos) > 1:
        nomes = ", ".join(sorted(path.name for path in candidatos))
        raise AuditoriaHITLError(
            f"{contexto}: midia ambigua ({nomes}). Mantenha exatamente um arquivo por slot."
        )

    path = candidatos[0]
    _validar_arquivo_nao_vazio(path, f"midia de {contexto}", erro_hitl=True)
    tipo = "imagem" if path.suffix.lower() in IMAGE_EXTENSIONS else "video"
    return MidiaAuditada(
        indice_slot=indice_slot,
        letra=letra,
        fonte_midia=fonte,
        papel_layout=papel_layout,
        path=path.resolve(),
        tipo=tipo,
    )


def _corresponde_asset_ia(path: Path, base: str, total_slots: int) -> bool:
    stem = path.stem.casefold()
    base_normalizada = base.casefold()
    if "_pexels" in stem:
        return False
    if stem != base_normalizada and not stem.startswith(base_normalizada + "_"):
        return False

    # A single-slot scene must not accidentally consume a leftover A/B file.
    if total_slots == 1:
        resto = stem[len(base_normalizada) :]
        if re.match(r"_[a-z](?:_|$)", resto):
            return False
    return True


def _base_slot(indice_cena: int, letra: str | None) -> str:
    sufixo = f"_{letra}" if letra else ""
    return f"cena_{indice_cena:02d}{sufixo}"


def _papeis_layout(template_id: int, total: int) -> list[str]:
    if template_id in {3, 7, 9, 10}:
        papeis = ["esquerda", "direita"]
    elif template_id == 5:
        papeis = ["celular_1", "celular_2", "celular_3"]
    elif template_id in {11, 12}:
        papeis = ["esquerda"]
    else:
        papeis = ["principal"]

    while len(papeis) < total:
        papeis.append(f"midia_{len(papeis) + 1}")
    return papeis[:total]


def _textos_tela(cena: dict[str, Any]) -> tuple[str, ...]:
    value: Any = None
    for key in ("textos_tela", "textos_na_tela", "texto_tela", "texto_na_tela", "textos"):
        if key in cena:
            value = cena[key]
            break

    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        if not all(isinstance(item, str) for item in value):
            raise AuditoriaHITLError("textos_tela deve conter somente strings.")
        # O T12 normalizado conserva lacunas finais para representar topicos
        # ainda nao introduzidos. A factory ignora essas linhas vazias ao
        # desenhar, mas o contrato geometrico continua tendo quatro posicoes.
        return tuple(item.strip() for item in value)
    raise AuditoriaHITLError("textos_tela deve ser string ou lista de strings.")


def _validar_topicos_template_lista(
    textos_tela: Sequence[str],
    template_id: int,
) -> None:
    """Garante os quatro topicos explicitos previstos nos templates de lista."""

    if len(textos_tela) != TEMPLATE_11_TOPICOS_EXIGIDOS:
        raise AuditoriaHITLError(
            f"template {template_id} exige exatamente "
            f"{TEMPLATE_11_TOPICOS_EXIGIDOS} topicos explicitos em 'textos_tela' "
            "(itens 1, 2, 3 e 4)."
        )
    if template_id == 12:
        encontrou_lacuna = False
        for topico in textos_tela:
            if not topico:
                encontrou_lacuna = True
            elif encontrou_lacuna:
                raise AuditoriaHITLError(
                    "template 12 exige topicos acumulados sem lacunas."
                )
        if not textos_tela[0]:
            raise AuditoriaHITLError("template 12 exige ao menos o primeiro topico.")
    elif any(not topico for topico in textos_tela):
        raise AuditoriaHITLError(
            f"template {template_id} exige topicos explicitos nao vazios."
        )
    if any("\n" in topico or "\r" in topico for topico in textos_tela):
        raise AuditoriaHITLError(
            f"cada topico do template {template_id} deve ocupar uma unica linha."
        )


def _resolver_executavel(valor: str, nome: str) -> str:
    candidato = Path(valor)
    if candidato.parent != Path(".") or candidato.is_absolute():
        if candidato.is_file():
            return str(candidato.resolve())
        raise FileNotFoundError(f"{nome} nao encontrado em: {candidato}")

    encontrado = shutil.which(valor)
    if not encontrado:
        raise FileNotFoundError(f"{nome} nao encontrado no PATH: {valor}")
    return encontrado


def _executar(
    args: Sequence[str],
    etapa: str,
    *,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Executa um comando drenando os dois pipes durante toda a execucao.

    ``communicate`` le stdout e stderr simultaneamente, evitando que um
    processo FFmpeg bloqueie ao preencher um pipe no Windows. O timeout e
    usado pelos workers de cena, que rodam em processos isolados.
    """

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    comando = [str(arg) for arg in args]
    try:
        processo = subprocess.Popen(
            comando,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"Falha em {etapa}: executavel nao encontrado ({args[0]}).") from exc

    try:
        stdout, stderr = processo.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        processo.kill()
        stdout, stderr = processo.communicate()
        detalhe = (stderr or stdout or "sem detalhes").strip()
        if len(detalhe) > 5000:
            detalhe = detalhe[-5000:]
        raise RuntimeError(
            f"Tempo limite em {etapa} apos {timeout:.0f}s; processo FFmpeg encerrado.\n"
            f"{detalhe}"
        ) from exc

    result = subprocess.CompletedProcess(comando, processo.returncode, stdout, stderr)

    if result.returncode != 0:
        detalhe = (result.stderr or result.stdout or "sem detalhes").strip()
        if len(detalhe) > 5000:
            detalhe = detalhe[-5000:]
        raise RuntimeError(
            f"Falha em {etapa} (codigo {result.returncode}).\n{detalhe}"
        )
    return result


def _ffprobe_json(path: Path, ffprobe: str) -> dict[str, Any]:
    result = _executar(
        [
            ffprobe,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        f"ffprobe de {path.name}",
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe retornou JSON invalido para {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"ffprobe retornou payload inesperado para {path}")
    return payload


def _streams(payload: dict[str, Any], stream_type: str) -> list[dict[str, Any]]:
    streams = payload.get("streams", [])
    if not isinstance(streams, list):
        return []
    return [
        stream
        for stream in streams
        if isinstance(stream, dict) and stream.get("codec_type") == stream_type
    ]


def _sondar_midias_cenas(cenas: Sequence[CenaAuditada], ffprobe: str) -> list[CenaAuditada]:
    resultado: list[CenaAuditada] = []
    erros: list[str] = []
    for cena in cenas:
        midias: list[MidiaAuditada] = []
        for midia in cena.midias:
            try:
                payload = _ffprobe_json(midia.path, ffprobe)
                videos = _streams(payload, "video")
                if not videos:
                    raise RuntimeError("nenhum stream de video/imagem encontrado")
                width = int(videos[0].get("width") or 0)
                height = int(videos[0].get("height") or 0)
                if width <= 0 or height <= 0:
                    raise RuntimeError("dimensoes reais invalidas no header")
                if midia.tipo == "video":
                    _extrair_duracao_probe(payload, midia.path)
                midias.append(replace(midia, width=width, height=height))
            except (RuntimeError, TypeError, ValueError) as exc:
                erros.append(f"cena {cena.indice:02d} ({midia.path.name}): {exc}")
        if len(midias) == len(cena.midias):
            resultado.append(replace(cena, midias=tuple(midias)))

    if erros:
        raise AuditoriaHITLError(
            "ffprobe rejeitou midias da curadoria:\n"
            + "\n".join(f"- {erro}" for erro in erros)
        )
    return resultado


def _sondar_assets_globais(assets_globais: Sequence[AssetGlobal], ffprobe: str) -> None:
    """Valida por ffprobe os assets efetivamente sorteados nesta execucao."""

    erros: list[str] = []
    for asset in assets_globais:
        try:
            payload = _ffprobe_json(asset.path, ffprobe)
            streams = _streams(payload, asset.stream_type)
            if not streams:
                raise RuntimeError(f"nao contem stream de {asset.stream_type}")
            if asset.exige_duracao:
                _extrair_duracao_probe(payload, asset.path)
            if asset.stream_type == "video":
                width = int(streams[0].get("width") or 0)
                height = int(streams[0].get("height") or 0)
                if width <= 0 or height <= 0:
                    raise RuntimeError("stream visual sem dimensoes validas")
        except (RuntimeError, TypeError, ValueError) as exc:
            erros.append(f"{asset.nome} ({asset.path}): {exc}")
    if erros:
        raise AuditoriaHITLError(
            "Assets globais invalidos:\n" + "\n".join(f"- {erro}" for erro in erros)
        )


def _mapear_trilhas_validas(
    trilhas: Sequence[AssetGlobal],
    ffprobe: str,
) -> tuple[TrilhaComDuracao, ...]:
    """Valida e mede todas as trilhas candidatas sem deixar uma corrompida parar o pool."""

    validas: list[TrilhaComDuracao] = []
    erros: list[str] = []
    for trilha in trilhas:
        try:
            payload = _ffprobe_json(trilha.path, ffprobe)
            if not _streams(payload, "audio"):
                raise RuntimeError("nao contem stream de audio")
            duracao = _extrair_duracao_probe(payload, trilha.path)
            validas.append(TrilhaComDuracao(trilha.path.resolve(), duracao))
        except (RuntimeError, TypeError, ValueError) as exc:
            erros.append(f"{trilha.path.name}: {exc}")

    logger = get_logger(__name__)
    for erro in erros:
        logger.warning("Horizontal: trilha ignorada na playlist: %s", erro)
    if not validas:
        detalhes = "\n".join(f"- {erro}" for erro in erros)
        raise AuditoriaHITLError(
            "Nenhuma trilha valida foi encontrada para a playlist dinamica."
            + (f"\n{detalhes}" if detalhes else "")
        )
    return tuple(validas)


def _montar_playlist_dinamica(
    trilhas: Sequence[TrilhaComDuracao],
    duracao_mestre: float,
) -> tuple[TrilhaComDuracao, ...]:
    """Sorteia faixas ate cobrir toda a narracao sem repetir consecutivamente.

    A playlist deliberadamente traz pelo menos duas faixas distintas quando o
    acervo permite. Isso evita que um unico MP3 seja tratado como audio mestre
    e, sobretudo, assegura que o filtro ``concat`` tenha cobertura fisica para
    todo o video longo.
    """

    if not math.isfinite(duracao_mestre) or duracao_mestre <= 0:
        raise RuntimeError("Duracao do audio mestre invalida para a playlist dinamica.")

    candidatas = [
        trilha
        for trilha in trilhas
        if math.isfinite(trilha.duracao) and trilha.duracao > 0
    ]
    if not candidatas:
        raise AuditoriaHITLError("A playlist dinamica nao possui trilhas com duracao valida.")

    # A exigencia de variedade so e aplicavel se ha variedade fisica no acervo.
    # Com uma unica faixa valida o motor ainda concatena repeticoes dela, em vez
    # de encerrar a musica e, por efeito colateral, a narracao no meio do video.
    min_faixas_distintas = min(2, len({trilha.path.resolve() for trilha in candidatas}))
    playlist: list[TrilhaComDuracao] = []
    duracao_total = 0.0
    caminhos_distintos: set[Path] = set()
    anterior: Path | None = None

    while (
        duracao_total + 1e-6 < duracao_mestre
        or len(caminhos_distintos) < min_faixas_distintas
    ):
        opcoes = [
            trilha for trilha in candidatas if trilha.path.resolve() != anterior
        ]
        # So ocorre com uma unica faixa. Mantemos o comportamento seguro de
        # cobertura em vez de falhar por uma restricao de variedade impossivel.
        escolhida = random.choice(opcoes or candidatas)
        playlist.append(escolhida)
        duracao_total += escolhida.duracao
        caminho = escolhida.path.resolve()
        caminhos_distintos.add(caminho)
        anterior = caminho

    if duracao_total + 1e-6 < duracao_mestre:
        raise RuntimeError(
            "Playlist dinamica nao atingiu a duracao do audio mestre; "
            f"playlist={duracao_total:.3f}s mestre={duracao_mestre:.3f}s."
        )
    return tuple(playlist)


def _mapear_pool_transicoes_horizontal(ffprobe: str) -> list[TransicaoHorizontal]:
    if not OVERLAYS_DIR.is_dir():
        raise AuditoriaHITLError(
            f"O diretório {OVERLAYS_DIR} não contém coleções de transições audiovisuais."
        )

    transicoes: list[TransicaoHorizontal] = []
    erros: list[str] = []
    silenciosos_por_colecao: dict[str, int] = {}
    for path in sorted(OVERLAYS_DIR.rglob("*"), key=lambda item: str(item).lower()):
        # Static overlays such as seta_apontamento.png live at the root. Only
        # audiovisual clips inside named collections are transition assets.
        if path.parent == OVERLAYS_DIR:
            continue
        if not path.is_file() or path.suffix.lower() not in TRANSITION_VIDEO_EXTENSIONS:
            continue

        try:
            payload = _ffprobe_json(path, ffprobe)
            videos = _streams(payload, "video")
            audios = _streams(payload, "audio")
            if not videos or not audios:
                colecao = path.relative_to(OVERLAYS_DIR).parts[0]
                silenciosos_por_colecao[colecao] = (
                    silenciosos_por_colecao.get(colecao, 0) + 1
                )
                continue

            width = int(videos[0].get("width") or 0)
            height = int(videos[0].get("height") or 0)
            if width <= 0 or height <= 0:
                raise RuntimeError("stream de video sem dimensoes validas")
            duracao_video = _duracao_stream_probe(videos[0])
            duracao_audio = _duracao_stream_probe(audios[0])
            canais_audio = int(audios[0].get("channels") or 0)
            if canais_audio <= 0:
                raise RuntimeError("stream de audio sem numero de canais valido")
        except (RuntimeError, TypeError, ValueError) as exc:
            relativo = path.relative_to(OVERLAYS_DIR)
            erros.append(f"{relativo}: {exc}")
            continue

        transicoes.append(
            TransicaoHorizontal(
                path=path.resolve(),
                duracao_video=duracao_video,
                duracao_audio=duracao_audio,
                canais_audio=canais_audio,
            )
        )

    if not transicoes:
        detalhe = "\n".join(f"- {erro}" for erro in erros[:10])
        raise AuditoriaHITLError(
            f"O diretório {OVERLAYS_DIR} não contém transições com vídeo e áudio."
            + (f"\n{detalhe}" if detalhe else "")
        )

    logger = get_logger(__name__)
    logger.info(
        "Horizontal: pool dinamico mapeado com %s transicoes audiovisuais em %s",
        len(transicoes),
        OVERLAYS_DIR,
    )
    for colecao, quantidade in sorted(silenciosos_por_colecao.items()):
        logger.warning(
            "Horizontal: colecao '%s' ignorou %s clipes sem audio sincronizado.",
            colecao,
            quantidade,
        )
    for erro in erros[:10]:
        logger.warning("Horizontal: asset de transicao ignorado: %s", erro)
    if len(erros) > 10:
        logger.warning(
            "Horizontal: outros %s assets de transicao invalidos foram ignorados.",
            len(erros) - 10,
        )
    return transicoes


def _selecionar_transicoes_horizontais(
    pool: Sequence[TransicaoHorizontal],
    cortes: Sequence[float],
    duracao_total: float,
    *,
    primeiro_input: int = 3,
    atribuicoes: Mapping[int, Path] | None = None,
) -> list[TransicaoSelecionada]:
    if primeiro_input < 3:
        raise ValueError("primeiro_input das transicoes deve vir depois de video, voz e trilha.")
    transicoes: list[TransicaoSelecionada] = []
    por_caminho = {item.path.resolve(): item for item in pool}
    for indice, corte in enumerate(cortes):
        escolhido = (atribuicoes or {}).get(indice)
        asset = por_caminho.get(escolhido.resolve()) if escolhido else None
        if asset is None:
            asset = random.choice(pool)
        if asset.duracao_video is None or asset.duracao_audio is None:
            raise RuntimeError(
                f"Transicao sem par audiovisual no pool validado: {asset.path}"
            )

        duracao_av = min(
            asset.duracao_video,
            asset.duracao_audio,
            MAX_TRANSITION_DURATION,
        )
        inicio_visual = max(0.0, float(corte) - (duracao_av / 2))
        duracao_av = min(duracao_av, max(0.0, duracao_total - inicio_visual))
        if duracao_av <= 0:
            raise RuntimeError(
                f"Transicao sem duracao util no corte {float(corte):.3f}s: {asset.path}"
            )

        transicoes.append(
            TransicaoSelecionada(
                path=asset.path,
                # Inputs globais: 0=concat, 1=voz, 2..N=playlist de trilhas.
                # Cada transicao ocupa exatamente um input apos a playlist.
                input_index=primeiro_input + indice,
                corte=round(float(corte), 3),
                inicio_visual=round(inicio_visual, 3),
                duracao_video=round(duracao_av, 3),
                duracao_audio=round(duracao_av, 3),
                canais_audio=asset.canais_audio,
            )
        )
    return transicoes


def _validar_recursos_ffmpeg(ffmpeg: str, run_dir: Path) -> tuple[str, ...]:
    """Valida filtros obrigatorios e seleciona AMF com fallback para software."""

    filtros = _executar([ffmpeg, "-hide_banner", "-filters"], "auditoria de filtros")
    for filtro in ("sidechaincompress", "zoompan"):
        if filtro not in filtros.stdout:
            raise RuntimeError(f"FFmpeg nao oferece o filtro obrigatorio '{filtro}'.")

    if not _amf_habilitado_por_configuracao():
        get_logger(__name__).info(
            "Horizontal: h264_amf desabilitado por SYNTHREEL_ENABLE_AMF; "
            "usando fallback libx264."
        )
        return _opcoes_libx264()

    # Listing the encoder is insufficient when the driver/GPU is unavailable.
    # After acoustic retention passes, one 1920x1080 frame catches AMF
    # initialization failures before the actual scene render loop. A GPU is an
    # optimization, not a prerequisite for a valid horizontal render.
    preflight = run_dir / "preflight_h264_amf.mp4"
    try:
        encoders = _executar(
            [ffmpeg, "-hide_banner", "-encoders"],
            "auditoria de encoders",
        )
        if "h264_amf" not in encoders.stdout:
            raise RuntimeError("FFmpeg nao oferece o encoder AMD h264_amf.")
        _executar(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c=black:s={WIDTH}x{HEIGHT}:r={FPS}",
                "-frames:v",
                "1",
                "-an",
                *_opcoes_h264_amf(),
                str(preflight),
            ],
            "preflight do encoder AMD h264_amf",
        )
        _validar_arquivo_nao_vazio(preflight, "preflight h264_amf")
    except (OSError, RuntimeError) as exc:
        get_logger(__name__).warning(
            "Horizontal: AMD AMF indisponivel (%s); usando fallback libx264.",
            exc,
        )
        return _opcoes_libx264()

    return _opcoes_h264_amf()


def _amf_habilitado_por_configuracao() -> bool:
    """Permite desabilitar AMF sem alterar o contrato de renderizacao."""

    valor = os.environ.get("SYNTHREEL_ENABLE_AMF", "1").strip().casefold()
    return valor not in {"0", "false", "no", "off"}


def _opcoes_h264_amf() -> tuple[str, ...]:
    return (
        "-c:v",
        "h264_amf",
        "-usage",
        "transcoding",
        "-quality",
        "balanced",
        "-rc",
        # QVBR privilegia qualidade, mas nao impoe um teto de pico. Para um
        # MP4 longo, use o controle de pico do AMF com VBV/HRD explicitos:
        # limita a taxa instantanea que chega ao decoder do player.
        "vbr_peak",
        "-b:v",
        "8M",
        "-maxrate",
        "10M",
        "-bufsize",
        "16M",
        "-enforce_hrd",
        "1",
        "-pix_fmt",
        "yuv420p",
    )


def _opcoes_libx264() -> tuple[str, ...]:
    return ("-c:v", "libx264", "-pix_fmt", "yuv420p")


def _nome_encoder_video(opcoes_encoder_video: Sequence[str]) -> str:
    try:
        indice = tuple(opcoes_encoder_video).index("-c:v")
        return str(opcoes_encoder_video[indice + 1])
    except (IndexError, ValueError):
        raise ValueError("Opcoes de encoder de video sem argumento '-c:v' valido.") from None


def _resolver_fonte_deterministica(cenas: Sequence[CenaAuditada]) -> Path | None:
    if not any(cena.template_id in TEXT_TEMPLATES for cena in cenas):
        return None

    configurada = os.getenv("SYNTHREEL_FONT_FILE", "").strip()
    if configurada:
        path = Path(configurada).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"SYNTHREEL_FONT_FILE nao existe: {path}")
        return path.resolve()

    windir = Path(os.getenv("WINDIR", "C:/Windows"))
    candidatos = (
        windir / "Fonts" / "segoeui.ttf",
        windir / "Fonts" / "arial.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
    )
    for candidato in candidatos:
        if candidato.is_file():
            return candidato.resolve()

    raise FileNotFoundError(
        "Nenhuma fonte deterministica encontrada para drawtext. Configure "
        "SYNTHREEL_FONT_FILE com um arquivo .ttf."
    )


def _injetar_fonte_drawtext(filter_complex: str, fonte: Path | None) -> str:
    if "drawtext=" not in filter_complex:
        return filter_complex
    if fonte is None:
        raise RuntimeError("Layout usa drawtext, mas nenhuma fonte foi resolvida.")

    def escapar(path: Path) -> str:
        return (
            path.as_posix()
            .replace("\\", "\\\\")
            .replace(":", "\\:")
            .replace("'", "'" + ("\\" * 3) + "''")
        )

    path = escapar(fonte)
    windir = Path(os.getenv("WINDIR", "C:/Windows"))
    editorial = windir / "Fonts" / "georgiab.ttf"
    display = windir / "Fonts" / "ariblk.ttf"
    if not display.is_file():
        display = windir / "Fonts" / "arialbd.ttf"
    editorial_path = escapar(editorial if editorial.is_file() else fonte)
    display_path = escapar(display if display.is_file() else fonte)
    filter_complex = filter_complex.replace(
        "__SYNTHREEL_EDITORIAL_FONT__", editorial_path
    )
    filter_complex = filter_complex.replace(
        "__SYNTHREEL_DISPLAY_FONT__", display_path
    )
    # expansion=none keeps literal percentages from being parsed as %{...}
    # expressions while preserving the exact screen text from metadata.
    replacement = f"drawtext=fontfile='{path}':expansion=none:"
    return re.sub(
        r"drawtext=(?!fontfile=)",
        lambda _match: replacement,
        filter_complex,
    )


def _idioma_metadata(metadata: dict[str, Any]) -> str:
    value = metadata.get("idioma", TTSNeuralEngine.DEFAULT_IDIOMA)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("metadata.idioma deve ser uma string nao vazia quando informado.")
    idioma = value.strip().replace("_", "-")
    idiomas_suportados = {
        chave.casefold(): chave for chave in TTSNeuralEngine.VOZES_PADRAO
    }
    idioma_canonico = idiomas_suportados.get(idioma.casefold())
    if idioma_canonico is None:
        suportados = ", ".join(sorted(TTSNeuralEngine.VOZES_PADRAO))
        raise ValueError(
            f"metadata.idioma '{idioma}' nao possui voz horizontal mapeada. "
            f"Use um de: {suportados}."
        )
    return idioma_canonico


def _texto_tts_com_fim_de_frase(texto: str) -> str:
    texto = texto.strip()
    if texto and texto[-1] not in ".!?…":
        return texto + "."
    return texto


def alinhar_cenas_com_whisper(
    cenas: Sequence[CenaAuditada],
    timestamps: Sequence[dict[str, float | str]],
    duracao_audio: float,
) -> list[CenaTemporizada]:
    """Maps canonical JSON words to acoustic timestamps and derives scene cuts."""

    if not math.isfinite(duracao_audio) or duracao_audio <= 0:
        raise RuntimeError("Duracao fisica do audio mestre e invalida.")

    oficiais: list[dict[str, Any]] = []
    indices_por_cena: list[list[int]] = []
    for cena_pos, cena in enumerate(cenas):
        indices: list[int] = []
        for palavra in _tokenizar(cena.texto):
            normalizada = _normalizar_palavra(palavra)
            if not normalizada:
                continue
            indices.append(len(oficiais))
            oficiais.append(
                {
                    "cena": cena_pos,
                    "normalizada": normalizada,
                    "inicio": None,
                    "fim": None,
                    "ancora": False,
                }
            )
        if not indices:
            raise RuntimeError(f"Cena {cena.indice:02d} nao possui palavras alinhaveis.")
        indices_por_cena.append(indices)

    logger = get_logger(__name__)
    whisper: list[dict[str, Any]] = []
    ultimo_inicio = -1.0
    for item in timestamps:
        try:
            inicio = float(item["inicio"])
            fim = float(item["fim"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Whisper retornou timestamp invalido: {item}") from exc
        normalizada = _normalizar_palavra(str(item.get("palavra", "")))
        if not normalizada:
            continue
        if (
            not math.isfinite(inicio)
            or not math.isfinite(fim)
            or inicio < 0
            or fim < inicio
            or inicio + 0.001 < ultimo_inicio
        ):
            raise RuntimeError(f"Whisper retornou timestamps nao monotonicos: {item}")
        if fim == inicio:
            if inicio >= duracao_audio:
                raise RuntimeError("Whisper retornou palavra fora do audio mestre.")
            fim = min(duracao_audio, inicio + 0.001)
            logger.warning(
                "Horizontal: Whisper retornou palavra com duracao zero (%s em %.3fs); "
                "ajustando para 1 ms.",
                item.get("palavra", ""),
                inicio,
            )
        ultimo_inicio = inicio
        whisper.append({"normalizada": normalizada, "inicio": inicio, "fim": fim})

    if not whisper:
        raise RuntimeError("Whisper nao retornou palavras validas para o audio mestre.")

    ultimo_fim = max(float(item["fim"]) for item in whisper)
    if ultimo_fim > duracao_audio + 0.05:
        raise RuntimeError(
            "Whisper retornou timestamps alem da duracao fisica do audio "
            f"({ultimo_fim:.3f}s > {duracao_audio:.3f}s)."
        )
    for item in whisper:
        item["inicio"] = min(float(item["inicio"]), duracao_audio)
        item["fim"] = min(float(item["fim"]), duracao_audio)
        if float(item["fim"]) <= float(item["inicio"]):
            raise RuntimeError("Whisper retornou palavra fora do audio mestre.")

    matcher = SequenceMatcher(
        None,
        [item["normalizada"] for item in oficiais],
        [item["normalizada"] for item in whisper],
        autojunk=False,
    )
    total_ancoras = 0
    for tag, off_start, off_end, wh_start, wh_end in matcher.get_opcodes():
        if tag != "equal":
            continue
        for indice_oficial, indice_whisper in zip(
            range(off_start, off_end),
            range(wh_start, wh_end),
            strict=True,
        ):
            base = whisper[indice_whisper]
            oficiais[indice_oficial]["inicio"] = float(base["inicio"])
            oficiais[indice_oficial]["fim"] = float(base["fim"])
            oficiais[indice_oficial]["ancora"] = True
            total_ancoras += 1

    if total_ancoras == 0:
        raise RuntimeError(
            "Nao foi possivel alinhar nenhuma palavra oficial do JSON ao Whisper."
        )
    cobertura_global = total_ancoras / len(oficiais)
    if cobertura_global < MIN_GLOBAL_ALIGNMENT_COVERAGE:
        raise RuntimeError(
            "Cobertura lexical global do Whisper insuficiente para cortes confiaveis: "
            f"{cobertura_global * 100:.1f}% < "
            f"{MIN_GLOBAL_ALIGNMENT_COVERAGE * 100:.1f}%."
        )

    cenas_sem_ancora = [
        cenas[cena_pos].indice
        for cena_pos, indices in enumerate(indices_por_cena)
        if not any(bool(oficiais[indice]["ancora"]) for indice in indices)
    ]
    if cenas_sem_ancora:
        lista = ", ".join(f"{indice:02d}" for indice in cenas_sem_ancora)
        raise RuntimeError(
            "Whisper nao encontrou nenhuma ancora lexical nas cenas: "
            f"{lista}. Nao e seguro inventar o corte por interpolacao total."
        )

    _interpolar_palavras_sem_ancora(oficiais, duracao_audio)

    limites = [0.0]
    for indices in indices_por_cena[1:]:
        limites.append(float(oficiais[indices[0]]["inicio"]))
    limites.append(duracao_audio)
    limites = [round(value, 3) for value in limites]

    for index in range(1, len(limites)):
        if limites[index] <= limites[index - 1]:
            raise RuntimeError(
                "Alinhamento Whisper produziu cenas sem duracao positiva: "
                f"limites={limites}."
            )

    resultado: list[CenaTemporizada] = []
    for cena_pos, (cena, indices) in enumerate(zip(cenas, indices_por_cena, strict=True)):
        ancoras = sum(1 for indice in indices if bool(oficiais[indice]["ancora"]))
        cobertura = ancoras / len(indices)
        fim_fala = min(float(oficiais[indices[-1]]["fim"]), limites[cena_pos + 1])
        temporizada = CenaTemporizada(
            cena=cena,
            inicio=limites[cena_pos],
            fim_fala=round(max(limites[cena_pos], fim_fala), 3),
            fim=limites[cena_pos + 1],
            cobertura_whisper=round(cobertura, 4),
        )
        if temporizada.duracao < 0.1:
            raise RuntimeError(
                f"Cena {cena.indice:02d} ficou curta demais apos alinhamento: "
                f"{temporizada.duracao:.3f}s."
            )
        if cobertura < 0.25:
            logger.warning(
                "Horizontal: cena %02d teve baixa cobertura lexical do Whisper (%.1f%%); "
                "palavras restantes foram interpoladas.",
                cena.indice,
                cobertura * 100,
            )
        resultado.append(temporizada)

    logger.info(
        "Horizontal: %s/%s palavras oficiais ancoradas ao Whisper (%.1f%%)",
        total_ancoras,
        len(oficiais),
        cobertura_global * 100,
    )
    return resultado


def validar_duracao_maxima_cenas(
    cenas_temporizadas: Sequence[CenaTemporizada],
) -> None:
    """Rejects scenes that exceed the acoustic ceiling of their template.

    The JSON is the sole author of scene length. In particular, do not split a
    long scene visually after Whisper: doing so would conceal a script that
    violates the pacing contract.
    """

    for cena_temporizada in cenas_temporizadas:
        duracao = cena_temporizada.duracao
        limite = (
            MAX_TEMPLATE_12_SCENE_DURATION
            if cena_temporizada.cena.template_id == 12
            else MAX_SCENE_DURATION
        )
        if duracao > limite:
            trecho_texto = cena_temporizada.cena.texto[:30]
            raise TempoCenaExcedidoError(
                f'Cena {cena_temporizada.cena.indice:02d} ("{trecho_texto}..."): '
                f"duracao calculada de "
                f"{duracao:.3f}s viola a diretriz de retencao do YouTube "
                f"(maximo permitido para o template "
                f"{cena_temporizada.cena.template_id}: {limite:.3f}s)."
            )


def _interpolar_palavras_sem_ancora(palavras: list[dict[str, Any]], duracao: float) -> None:
    cursor = 0
    while cursor < len(palavras):
        if palavras[cursor]["inicio"] is not None:
            cursor += 1
            continue

        inicio_run = cursor
        while cursor < len(palavras) and palavras[cursor]["inicio"] is None:
            cursor += 1
        fim_run = cursor

        limite_inicio = (
            float(palavras[inicio_run - 1]["fim"]) if inicio_run > 0 else 0.0
        )
        limite_fim = (
            float(palavras[fim_run]["inicio"]) if fim_run < len(palavras) else duracao
        )
        quantidade = fim_run - inicio_run
        if limite_fim < limite_inicio:
            raise RuntimeError("Ancora Whisper regressiva durante interpolacao lexical.")

        passo = (limite_fim - limite_inicio) / max(1, quantidade)
        for offset, indice in enumerate(range(inicio_run, fim_run)):
            inicio = limite_inicio + offset * passo
            fim = limite_inicio + (offset + 1) * passo
            palavras[indice]["inicio"] = inicio
            palavras[indice]["fim"] = max(inicio + 0.001, fim)


def _renderizar_cena(
    cena_temporizada: CenaTemporizada,
    output_path: Path,
    *,
    ffmpeg: str,
    fonte: Path | None,
    seta_path: Path,
    cor_texto: str = "black",
    borda_texto: bool = True,
    cor_borda_texto: str = "white",
) -> None:
    """Renderiza um clip intermediario exclusivamente com libx264.

    A composicao final e a unica etapa horizontal autorizada a usar AMF. Isso
    isola os filtros irregulares dos workers de cena do driver AMD.
    """

    cena = cena_temporizada.cena
    # Quantize absolute scene boundaries, not each duration independently. The
    # frame counts then telescope to round(master_duration * FPS), avoiding an
    # accumulated drift of up to half a frame per scene in ten-minute videos.
    frames = _quantidade_frames_cena(cena_temporizada)
    indices_imagens = frozenset(
        index
        for index, midia in enumerate(cena.midias)
        if midia.tipo == "imagem"
        and midia.path.suffix.casefold() in LayoutFactory.IMAGE_EXTENSIONS
    )

    args: list[str] = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    for midia in cena.midias:
        if midia.tipo == "imagem":
            args.extend(["-loop", "1", "-framerate", str(FPS), "-i", str(midia.path)])
        else:
            args.extend(["-stream_loop", "-1", "-i", str(midia.path)])

    caminhos_layout = cena.caminhos_midias
    if cena.template_id == 3:
        # The arrow is a persistent composition asset, never scene media from
        # Pexels. It is input 2 after the two curated A/B slots.
        args.extend(["-loop", "1", "-framerate", str(FPS), "-i", str(seta_path)])
        caminhos_layout["seta"] = str(seta_path)

    if cena.template_id in TEMPLATES_COM_FUNDO_ESTATICO:
        fundo = cena.fundo_estatico
        if fundo is None:
            raise AuditoriaHITLError(
                f"Cena {cena.indice:02d} template {cena.template_id} exige um fundo "
                "estatico validado em workspace/assets/horizontal/fundos_estaticos."
            )
        _validar_arquivo_nao_vazio(
            fundo,
            f"fundo estatico da cena {cena.indice:02d}",
            erro_hitl=True,
        )
        # The background is a persistent composition asset. It is deliberately
        # outside indices_imagens so LayoutFactory keeps it static instead of
        # applying Ken Burns to it.
        args.extend(["-loop", "1", "-framerate", str(FPS), "-i", str(fundo)])
        caminhos_layout["fundo_estatico"] = str(fundo)
    elif cena.fundo_estatico is not None:
        raise AuditoriaHITLError(
            f"Cena {cena.indice:02d}: fundo estatico nao e permitido no template "
            f"{cena.template_id}."
        )

    bloco_layout = LayoutFactory.build_filter_complex(
        cena.template_id,
        caminhos_layout,
        cena.textos_tela,
        indices_imagens=indices_imagens,
        total_frames=frames,
        cor_texto=cor_texto,
        borda_texto=borda_texto,
        cor_borda_texto=cor_borda_texto,
    )
    bloco_layout = _injetar_fonte_drawtext(bloco_layout, fonte)
    filter_complex = (
        f"{bloco_layout};"
        # Todos os workers precisam sair com as mesmas propriedades de cor.
        # O concat demuxer troca de stream ao mudar de cena; uma alternancia
        # tv/BT.709 <-> pc/BT.470 faz o FFmpeg reinicializar o grafo global e
        # descartar amostras de audio que ja estavam em voo.
        f"[vout]fps={FPS},setsar=1,setrange=tv,format=yuv420p,"
        f"trim=end_frame={frames},"
        "setpts=PTS-STARTPTS[vscene]"
    )

    args.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            "[vscene]",
            "-an",
            "-frames:v",
            str(frames),
            "-fps_mode",
            "cfr",
            "-color_range",
            "tv",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            *_opcoes_libx264(),
            str(output_path),
        ]
    )
    _executar(
        args,
        f"renderizacao da cena {cena.indice:02d}",
        timeout=SCENE_RENDER_TIMEOUT_SECONDS,
    )
    _validar_arquivo_nao_vazio(output_path, f"clip da cena {cena.indice:02d}")


def _processar_trabalho_cena(
    trabalho: TrabalhoRenderizacaoCena,
) -> ResultadoRenderizacaoCena:
    """Renderiza ou reaproveita uma cena em um processo isolado.

    O contrato de entrada usa apenas dados serializaveis para funcionar com o
    metodo ``spawn`` do Windows. Assim, o worker nao compartilha objetos
    mutaveis do orquestrador nem o logger dele.
    """

    cena_temporizada = trabalho.cena_temporizada
    indice = cena_temporizada.cena.indice
    ordem_render = trabalho.ordem_render or indice
    prefixo_log = f"[CENA {indice:02d}]"
    logger = get_logger(__name__)

    try:
        hash_entrada = _hash_entrada_cena(
            cena_temporizada,
            metadata_cena=trabalho.metadata_cena,
            fonte=trabalho.fonte,
            seta_path=trabalho.seta_path,
            cor_texto=trabalho.cor_texto,
            borda_texto=trabalho.borda_texto,
            cor_borda_texto=trabalho.cor_borda_texto,
        )
        duracao_reaproveitada = _reaproveitar_cena_integra(
            clip_path=trabalho.clip_path,
            manifest_path=trabalho.manifest_path,
            hash_entrada=hash_entrada,
            ffprobe=trabalho.ffprobe,
            logger=logger,
            prefixo_log=prefixo_log,
        )
        if duracao_reaproveitada is not None:
            return ResultadoRenderizacaoCena(
                indice_cena=ordem_render,
                clip_path=trabalho.clip_path,
                duracao=duracao_reaproveitada,
                reaproveitada=True,
            )

        _invalidar_artefatos_cena(trabalho.clip_path, trabalho.manifest_path)
        logger.info(
            "%s INFO | renderizando template=%s duracao=%.3fs",
            prefixo_log,
            cena_temporizada.cena.template_id,
            cena_temporizada.duracao,
        )
        _renderizar_cena(
            cena_temporizada,
            trabalho.clip_path,
            ffmpeg=trabalho.ffmpeg,
            fonte=trabalho.fonte,
            seta_path=trabalho.seta_path,
        )
        duracao_renderizada = _duracao_video(trabalho.clip_path, trabalho.ffprobe)
        _salvar_manifest_cena(
            trabalho.manifest_path,
            hash_entrada=hash_entrada,
            duracao_esperada=_duracao_renderizada_esperada(cena_temporizada),
        )
        logger.info("%s INFO | renderizacao concluida.", prefixo_log)
        return ResultadoRenderizacaoCena(
            indice_cena=ordem_render,
            clip_path=trabalho.clip_path,
            duracao=duracao_renderizada,
            reaproveitada=False,
        )
    except Exception as exc:
        logger.error("%s ERROR | falha na renderizacao: %s", prefixo_log, exc)
        raise


def _renderizar_trabalhos_cenas_concorrentes(
    trabalhos: Sequence[TrabalhoRenderizacaoCena],
    *,
    max_workers: int = MAX_SCENE_RENDER_WORKERS,
) -> list[ResultadoRenderizacaoCena]:
    """Executa cenas com concorrencia limitada e falha rapida do lote."""

    if not trabalhos:
        raise RuntimeError("Nenhum trabalho de cena foi preparado para renderizacao.")
    if not 1 <= max_workers <= MAX_SCENE_RENDER_WORKERS:
        raise ValueError(
            "max_workers deve ficar entre 1 e "
            f"{MAX_SCENE_RENDER_WORKERS} nesta fase de estabilizacao."
        )

    logger = get_logger(__name__)
    executor = ProcessPoolExecutor(max_workers=max_workers)
    futures: dict[Future[ResultadoRenderizacaoCena], TrabalhoRenderizacaoCena] = {}
    shutdown_solicitado = False
    try:
        futures = {
            executor.submit(_processar_trabalho_cena, trabalho): trabalho
            for trabalho in trabalhos
        }
        resultados: list[ResultadoRenderizacaoCena] = []
        for future in as_completed(futures):
            trabalho = futures[future]
            try:
                resultados.append(future.result())
            except BaseException as exc:
                indice = trabalho.cena_temporizada.cena.indice
                logger.critical(
                    "[CENA %02d] ERROR | falha critica no worker; "
                    "cancelando as cenas pendentes: %s",
                    indice,
                    exc,
                )
                for pendente in futures:
                    if pendente is not future:
                        pendente.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
                shutdown_solicitado = True
                raise
    except BaseException:
        # Falhas durante submit recebem a mesma semantica de short-circuit.
        # Os workers ja em execucao nao sao interrompidos pelo executor do
        # Python, mas nenhuma tarefa ainda enfileirada e iniciada.
        if not shutdown_solicitado:
            for future in futures:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True, cancel_futures=False)

    return sorted(resultados, key=lambda resultado: resultado.indice_cena)


def _quantidade_frames_cena(cena_temporizada: CenaTemporizada) -> int:
    """Quantiza limites absolutos sem alterar o contrato de timing existente."""

    frame_inicio = int(round(cena_temporizada.inicio * FPS))
    frame_fim = int(round(cena_temporizada.fim * FPS))
    return max(1, frame_fim - frame_inicio)


def _duracao_renderizada_esperada(cena_temporizada: CenaTemporizada) -> float:
    return _quantidade_frames_cena(cena_temporizada) / FPS


def _caminho_manifest_cena(run_dir: Path, indice_cena: int) -> Path:
    return run_dir / f"cena_{indice_cena:02d}.json"


def _metadata_cena_para_resume(metadata: dict[str, Any], indice_cena: int) -> dict[str, Any]:
    """Extrai somente os campos de origem visual que definem a cena."""

    cenas = metadata.get("cenas")
    if not isinstance(cenas, list) or indice_cena < 1 or indice_cena > len(cenas):
        raise RuntimeError(
            f"Metadata nao possui a cena {indice_cena:02d} para o manifesto de resume."
        )
    cena = cenas[indice_cena - 1]
    if not isinstance(cena, dict):
        raise RuntimeError(f"Metadata da cena {indice_cena:02d} nao e um objeto JSON.")

    chaves_relevantes = (
        "fonte_midia",
        "fonte",
        "source_media",
        "media_source",
        "prompt_ou_busca",
        "busca",
        "prompt",
        "busca_local",
        "buscas_locais",
        "midias",
        "medias",
        "_midias_resolvidas",
        "caminho_local",
        "fonte_midia_resolvida",
    )
    return {chave: cena[chave] for chave in chaves_relevantes if chave in cena}


def _hash_entrada_cena(
    cena_temporizada: CenaTemporizada,
    *,
    metadata_cena: dict[str, Any],
    fonte: Path | None,
    seta_path: Path,
    cor_texto: str = "black",
    borda_texto: bool = True,
    cor_borda_texto: str = "white",
) -> str:
    """Gera a identidade deterministica de uma cena renderizada."""

    cena = cena_temporizada.cena
    arquivos = [
        {
            "slot": midia.indice_slot,
            "papel": midia.papel_layout,
            "fonte": midia.fonte_midia,
            "tipo": midia.tipo,
            "arquivo": _assinatura_arquivo_para_resume(midia.path),
        }
        for midia in cena.midias
    ]
    if cena.fundo_estatico is not None:
        arquivos.append(
            {
                "papel": "fundo_estatico",
                "arquivo": _assinatura_arquivo_para_resume(cena.fundo_estatico),
            }
        )
    if cena.template_id == 3:
        arquivos.append(
            {"papel": "seta", "arquivo": _assinatura_arquivo_para_resume(seta_path)}
        )
    if fonte is not None:
        arquivos.append(
            {"papel": "fonte", "arquivo": _assinatura_arquivo_para_resume(fonte)}
        )

    payload = {
        "versao_manifest": SCENE_RESUME_MANIFEST_VERSION,
        "texto": cena.texto,
        "textos_tela": list(cena.textos_tela),
        "template_id": cena.template_id,
        "cor_texto": cor_texto,
        "borda_texto": borda_texto,
        "cor_borda_texto": cor_borda_texto,
        "inicio": round(cena_temporizada.inicio, 6),
        "fim": round(cena_temporizada.fim, 6),
        "duracao_whisper": round(cena_temporizada.duracao, 6),
        "frames": _quantidade_frames_cena(cena_temporizada),
        "fps": FPS,
        "canvas": [WIDTH, HEIGHT],
        "metadata_origem": metadata_cena,
        "arquivos": arquivos,
    }
    serializado = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serializado.encode("utf-8")).hexdigest()


def _assinatura_arquivo_para_resume(path: Path) -> dict[str, int | str]:
    try:
        stat = path.resolve().stat()
    except OSError as exc:
        raise RuntimeError(f"Arquivo de entrada indisponivel para resume: {path}") from exc
    return {
        "caminho": str(path.resolve()),
        "tamanho": stat.st_size,
        "hash_hibrido": calcular_hash_hibrido(path),
    }


def _reaproveitar_cena_integra(
    *,
    clip_path: Path,
    manifest_path: Path,
    hash_entrada: str,
    ffprobe: str,
    logger: Logger | None = None,
    prefixo_log: str | None = None,
) -> float | None:
    """Retorna a duracao de um clip valido ou ``None`` para renderizacao nova."""

    if not clip_path.is_file() or not manifest_path.is_file():
        return None
    try:
        manifesto = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifesto, dict):
            return None
        if manifesto.get("versao") != SCENE_RESUME_MANIFEST_VERSION:
            return None
        if manifesto.get("hash_entrada") != hash_entrada:
            return None
        duracao_esperada = float(manifesto["duracao_esperada"])
        if not math.isfinite(duracao_esperada) or duracao_esperada <= 0:
            return None
        duracao_encontrada = _validar_clip_para_resume(clip_path, ffprobe)
    except (KeyError, OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError):
        return None

    if abs(duracao_encontrada - duracao_esperada) > SCENE_RESUME_DURATION_TOLERANCE:
        return None

    indice_cena = _indice_cena_do_clip(clip_path)
    logger_efetivo = logger or get_logger(__name__)
    if prefixo_log:
        logger_efetivo.info(
            "%s INFO | [RESUME] Cena %02d integra detectada. Pulando renderizacao.",
            prefixo_log,
            indice_cena,
        )
    else:
        logger_efetivo.info(
            "[RESUME] Cena %02d íntegra detectada. Pulando renderização.",
            indice_cena,
        )
    return duracao_encontrada


def _validar_clip_para_resume(clip_path: Path, ffprobe: str) -> float:
    """Confirma geometria, CFR e duracao de um clip candidato ao resume."""

    _validar_arquivo_nao_vazio(clip_path, "clip candidato ao resume")
    payload = _ffprobe_json(clip_path, ffprobe)
    videos = _streams(payload, "video")
    if not videos:
        raise RuntimeError(f"Clip de resume nao possui stream de video: {clip_path}")
    video = videos[0]
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if (width, height) != (WIDTH, HEIGHT):
        raise RuntimeError(
            f"Clip de resume {clip_path.name} saiu em {width}x{height}; esperado "
            f"{WIDTH}x{HEIGHT}."
        )
    if abs(_fps_stream_probe(video) - FPS) > 0.01:
        raise RuntimeError(f"Clip de resume {clip_path.name} nao esta em CFR {FPS} fps.")
    return _extrair_duracao_probe(payload, clip_path)


def _fps_stream_probe(stream: dict[str, Any]) -> float:
    for campo in ("avg_frame_rate", "r_frame_rate"):
        valor = str(stream.get(campo) or "")
        if "/" not in valor:
            continue
        try:
            numerador, denominador = valor.split("/", 1)
            fps = float(numerador) / float(denominador)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if math.isfinite(fps) and fps > 0:
            return fps
    raise RuntimeError("ffprobe nao informou frame rate valido para o clip de resume.")


def _indice_cena_do_clip(clip_path: Path) -> int:
    match = re.fullmatch(r"cena_(\d+)_render", clip_path.stem, flags=re.IGNORECASE)
    if match is None:
        raise RuntimeError(f"Nome de clip de resume invalido: {clip_path.name}")
    return int(match.group(1))


def _invalidar_artefatos_cena(clip_path: Path, manifest_path: Path) -> None:
    """Remove apenas artefatos de cache que nao passaram na verificacao."""

    for path in (clip_path, manifest_path):
        if path.exists():
            unlink_com_retry(path)


def _salvar_manifest_cena(
    manifest_path: Path,
    *,
    hash_entrada: str,
    duracao_esperada: float,
) -> None:
    """Persiste o manifesto por cena de forma atomica e resiliente a locks."""

    if not math.isfinite(duracao_esperada) or duracao_esperada <= 0:
        raise RuntimeError("Duracao esperada invalida para manifesto de resume.")
    payload = {
        "versao": SCENE_RESUME_MANIFEST_VERSION,
        "hash_entrada": hash_entrada,
        "duracao_esperada": round(duracao_esperada, 6),
    }
    temporario = manifest_path.with_name(f".{manifest_path.name}.{uuid4().hex}.tmp")
    try:
        temporario.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        replace_com_retry(temporario, manifest_path)
    finally:
        if temporario.exists():
            unlink_com_retry(temporario)


def _escrever_manifest_concat(
    path: Path,
    clipes: Sequence[Path],
    duracoes: Sequence[float],
) -> None:
    """Escreve um ffconcat com duracao explicita para cada clip CFR.

    A duracao vem do ffprobe executado logo apos cada render de cena. Nao
    delegamos essa inferencia ao demuxer concat: em particular, clips gerados
    de imagens via zoompan podem ter metadados de container ambiguos.
    """

    if not clipes:
        raise RuntimeError("Nenhum clip de cena foi gerado para concatenacao.")
    if len(clipes) != len(duracoes):
        raise RuntimeError(
            "Quantidade de clips e duracoes diverge na concatenacao: "
            f"clips={len(clipes)} duracoes={len(duracoes)}."
        )

    linhas = ["ffconcat version 1.0"]
    for clip, duracao in zip(clipes, duracoes, strict=True):
        if not math.isfinite(duracao) or duracao <= 0:
            raise RuntimeError(
                f"Duracao invalida para o clip {clip.name}: {duracao!r}."
            )
        escaped = clip.resolve().as_posix().replace("'", "'\\''")
        linhas.append(f"file '{escaped}'")
        linhas.append(f"duration {_fmt_float(duracao)}")
    path.write_text("\n".join(linhas) + "\n", encoding="utf-8")


def _calcular_cortes_reais(duracoes_clipes: Sequence[float]) -> list[float]:
    cortes: list[float] = []
    cursor = 0.0
    for duracao in duracoes_clipes[:-1]:
        if duracao <= 0:
            raise RuntimeError(f"Clip de cena com duracao invalida: {duracao}")
        cursor += duracao
        cortes.append(round(cursor, 3))
    return cortes


def _eventos_escrita(cenas: Sequence[CenaTemporizada]) -> tuple[EventoEscrita, ...]:
    """Agenda uma escrita curta no início de cada composição com texto."""

    eventos: list[EventoEscrita] = []
    for temporizada in cenas:
        cena = temporizada.cena
        if cena.template_id not in TEMPLATES_COM_DIGITACAO:
            continue
        linhas = LayoutFactory._linhas_texto(cena.textos_tela)
        if cena.template_id == 6:
            linhas = LayoutFactory._linhas_texto_em_duas_linhas(cena.textos_tela)
        if not linhas:
            continue
        duracao = sum(
            LayoutFactory._duracao_digitacao(linha)
            + LayoutFactory.TYPING_LINE_PAUSE_SECONDS
            for linha in linhas
        )
        duracao = min(2.8, max(0.25, duracao - LayoutFactory.TYPING_LINE_PAUSE_SECONDS))
        eventos.append(EventoEscrita(inicio=temporizada.inicio + 0.10, duracao=duracao))
    return tuple(eventos)


def construir_filtro_global(
    duracao: float,
    transicoes: Sequence[TransicaoSelecionada],
    *,
    indices_trilhas: Sequence[int] = (2,),
    eventos_escrita: Sequence[EventoEscrita] = (),
) -> str:
    """Builds the playlist, dynamic transition overlays and synchronized mix."""

    d = _fmt_float(duracao)
    indices = tuple(indices_trilhas)
    if not indices:
        raise ValueError("A composicao final exige ao menos uma trilha na playlist.")
    if len(set(indices)) != len(indices) or any(index < 2 for index in indices):
        raise ValueError("Indices da playlist de trilhas invalidos.")

    filtros = [
        (
            f"[0:v]fps={FPS},scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT},setsar=1,format=yuv420p[video_base]"
        ),
        (
            f"[1:a]aresample=48000,atrim=duration={d},asetpts=PTS-STARTPTS,"
            "asplit=2[voz_mix][voz_sidechain]"
        ),
    ]
    labels_faixas: list[str] = []
    for ordem, input_index in enumerate(indices):
        label = f"trilha_faixa_{ordem}"
        filtros.append(
            f"[{input_index}:a]aresample=48000,aformat=channel_layouts=stereo,"
            f"asetpts=PTS-STARTPTS[{label}]"
        )
        labels_faixas.append(f"[{label}]")
    filtros.extend(
        [
            f"{''.join(labels_faixas)}concat=n={len(labels_faixas)}:v=0:a=1[playlist_bruta]",
            (
                f"[playlist_bruta]atrim=duration={d},asetpts=PTS-STARTPTS,"
                "volume=0.22[trilha]"
            ),
            (
                "[trilha][voz_sidechain]sidechaincompress=threshold=0.03:ratio=10:"
                "attack=15:release=750[trilha_ducked]"
            ),
        ]
    )

    video_atual = "video_base"
    labels_audio: list[str] = []
    for indice, transicao in enumerate(transicoes):
        if transicao.duracao_video is not None and transicao.inicio_visual is not None:
            inicio = _fmt_float(transicao.inicio_visual)
            fim = _fmt_float(transicao.inicio_visual + transicao.duracao_video)
            duracao_video = _fmt_float(transicao.duracao_video)
            label_video = f"transicao_video_{indice}"
            proximo_video = f"video_transicao_{indice}"
            filtros.append(
                f"[{transicao.input_index}:v]fps={FPS},"
                f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
                f"crop={WIDTH}:{HEIGHT},trim=duration={duracao_video},"
                f"setpts=PTS-STARTPTS+{inicio}/TB,format=rgba,"
                f"colorchannelmixer=aa=0.65[{label_video}]"
            )
            filtros.append(
                f"[{video_atual}][{label_video}]overlay=x=0:y=0:eof_action=pass:"
                f"shortest=0:repeatlast=0:enable='between(t,{inicio},{fim})'"
                f"[{proximo_video}]"
            )
            video_atual = proximo_video

        if transicao.duracao_audio is not None:
            layout = "mono" if transicao.canais_audio == 1 else "stereo"
            inicio_audio = (
                transicao.inicio_visual
                if transicao.inicio_visual is not None
                else transicao.corte
            )
            atraso_ms = max(0, int(round(inicio_audio * 1000)))
            duracao_audio = max(0.0, float(transicao.duracao_audio))
            # Evita estalos e picos no inicio/fim do audio embutido na
            # transicao. O fade se adapta a clipes excepcionalmente curtos.
            duracao_fade = min(0.05, duracao_audio / 2)
            inicio_fade_saida = max(0.0, duracao_audio - duracao_fade)
            label_audio = f"transicao_audio_{indice}"
            filtros.append(
                f"[{transicao.input_index}:a]aresample=48000,"
                f"aformat=channel_layouts={layout},"
                f"atrim=duration={_fmt_float(duracao_audio)},"
                "asetpts=PTS-STARTPTS,volume=0.25,"
                f"afade=t=in:st=0:d={_fmt_float(duracao_fade)},"
                f"afade=t=out:st={_fmt_float(inicio_fade_saida)}:"
                f"d={_fmt_float(duracao_fade)},"
                f"adelay={atraso_ms}:all=1[{label_audio}]"
            )
            labels_audio.append(f"[{label_audio}]")

    for indice, evento in enumerate(eventos_escrita):
        inicio = max(0.0, float(evento.inicio))
        duracao_evento = max(0.05, float(evento.duracao))
        atraso_ms = int(round(inicio * 1000))
        fade_saida = max(0.0, duracao_evento - 0.08)
        label_audio = f"escrita_audio_{indice}"
        # Dois harmônicos amortecidos formam um toque mecânico leve (corpo
        # grave + tecla metálica), muito mais suave que rajadas de ruído.
        filtros.append(
            "aevalsrc=exprs='0.16*(sin(2*PI*280*t)+"
            "0.35*sin(2*PI*1100*t))*exp(-mod(t\\,0.095)*55)':"
            f"s=48000:d={_fmt_float(duracao_evento)},"
            "highpass=f=140,lowpass=f=2600,volume=0.42,"
            "afade=t=in:st=0:d=0.020,"
            f"afade=t=out:st={_fmt_float(fade_saida)}:d=0.080,"
            f"adelay={atraso_ms}:all=1[{label_audio}]"
        )
        labels_audio.append(f"[{label_audio}]")

    filtros.append(f"[{video_atual}]format=yuv420p[vfinal]")
    entradas_mix = "[voz_mix][trilha_ducked]" + "".join(labels_audio)
    filtros.append(
        f"{entradas_mix}amix=inputs={2 + len(labels_audio)}:duration=longest:"
        f"dropout_transition=0:normalize=0,"
        "alimiter=limit=0.92:attack=5:release=50:latency=1,"
        f"atrim=duration={d}[afinal]"
    )
    return ";".join(filtros)


def _renderizar_compilacao_final(
    *,
    manifest: Path,
    audio_mestre: Path,
    output_path: Path,
    duracao: float,
    transicoes: Sequence[TransicaoSelecionada],
    eventos_escrita: Sequence[EventoEscrita] = (),
    ffmpeg: str,
    trilhas_paths: Sequence[Path] | None = None,
    trilha_path: Path | None = None,
    opcoes_encoder_video: Sequence[str] | None = None,
) -> None:
    """Concatena uma playlist musical completa antes do ducking final.

    ``trilha_path`` continua aceito apenas para chamadas internas legadas e
    testes isolados. A execucao normal sempre informa ``trilhas_paths`` com a
    playlist dimensionada para a duracao do audio mestre.
    """

    if trilhas_paths is not None and trilha_path is not None:
        raise ValueError("Informe trilhas_paths ou trilha_path, nunca ambos.")
    caminhos_trilhas = tuple(Path(path) for path in (trilhas_paths or ()))
    if trilha_path is not None:
        caminhos_trilhas = (Path(trilha_path),)
    if not caminhos_trilhas:
        raise RuntimeError("Composicao horizontal final sem trilhas para a playlist.")
    # Chamadas internas recebem o resultado do preflight. O fallback local
    # protege consumidores isolados que invoquem esta funcao sem preflight.
    opcoes_video = tuple(opcoes_encoder_video or _opcoes_libx264())
    _nome_encoder_video(opcoes_video)

    primeiro_input_transicao = 2 + len(caminhos_trilhas)
    for indice, transicao in enumerate(transicoes):
        esperado = primeiro_input_transicao + indice
        if transicao.input_index != esperado:
            raise RuntimeError(
                "Indice de input da transicao nao corresponde a playlist de trilhas: "
                f"transicao={transicao.path.name} input={transicao.input_index} "
                f"esperado={esperado}."
            )

    args = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        # O demuxer concat pode receber clips com PTS ausente ou ambiguo,
        # especialmente quando a fonte foi uma imagem com zoompan. Gere uma
        # linha de tempo antes de o material entrar no grafo global.
        "-fflags",
        "+genpts",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(manifest),
        "-i",
        str(audio_mestre),
    ]
    for trilha in caminhos_trilhas:
        args.extend(["-i", str(trilha)])
    for transicao in transicoes:
        args.extend(["-i", str(transicao.path)])

    args.extend(
        [
            "-filter_complex",
            construir_filtro_global(
                duracao,
                transicoes,
                indices_trilhas=tuple(range(2, 2 + len(caminhos_trilhas))),
                eventos_escrita=eventos_escrita,
            ),
            "-map",
            "[vfinal]",
            "-map",
            "[afinal]",
            # A composicao ja entrega video CFR; declare-o tambem no muxer
            # para que cada encoder (inclusive AMF) receba uma cadencia unica.
            "-fps_mode",
            "cfr",
            "-r",
            str(FPS),
            "-t",
            _fmt_float(duracao),
            *opcoes_video,
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            # 90 kHz e uma escala de tempo MP4 amplamente compativel e torna
            # exatos os timestamps de 30 fps (3.000 ticks por frame).
            "-video_track_timescale",
            "90000",
            "-avoid_negative_ts",
            "make_zero",
            "-movflags",
            "+faststart",
            "-map_metadata",
            "-1",
            str(output_path),
        ]
    )
    _executar(args, "composicao horizontal final")
    _validar_arquivo_nao_vazio(output_path, "video horizontal final")


def _duracao_audio(path: Path, ffprobe: str) -> float:
    payload = _ffprobe_json(path, ffprobe)
    if not _streams(payload, "audio"):
        raise RuntimeError(f"Audio mestre nao possui stream de audio: {path}")
    return _extrair_duracao_probe(payload, path)


def _duracao_video(path: Path, ffprobe: str) -> float:
    payload = _ffprobe_json(path, ffprobe)
    videos = _streams(payload, "video")
    if not videos:
        raise RuntimeError(f"Clip nao possui stream de video: {path}")
    width = int(videos[0].get("width") or 0)
    height = int(videos[0].get("height") or 0)
    if (width, height) != (WIDTH, HEIGHT):
        raise RuntimeError(
            f"Clip {path.name} saiu em {width}x{height}; esperado {WIDTH}x{HEIGHT}."
        )
    return _extrair_duracao_probe(payload, path)


def _extrair_duracao_probe(payload: dict[str, Any], path: Path) -> float:
    candidatos: list[Any] = []
    formato = payload.get("format")
    if isinstance(formato, dict):
        candidatos.append(formato.get("duration"))
    streams = payload.get("streams")
    if isinstance(streams, list):
        candidatos.extend(
            stream.get("duration") for stream in streams if isinstance(stream, dict)
        )

    for value in candidatos:
        try:
            duracao = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(duracao) and duracao > 0:
            return duracao
    raise RuntimeError(f"Nao foi possivel obter duracao fisica via ffprobe: {path}")


def _validar_video_final(path: Path, ffprobe: str, duracao_esperada: float) -> None:
    payload = _ffprobe_json(path, ffprobe)
    videos = _streams(payload, "video")
    audios = _streams(payload, "audio")
    if not videos or not audios:
        raise RuntimeError("Video final precisa conter streams de video e audio.")

    video = videos[0]
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    codec = str(video.get("codec_name") or "")
    if (width, height) != (WIDTH, HEIGHT):
        raise RuntimeError(f"Video final saiu em {width}x{height}; esperado 1920x1080.")
    if codec not in {"h264", "avc1"}:
        raise RuntimeError(f"Video final nao esta em H.264: codec={codec or 'desconhecido'}")

    duracao_video = _duracao_stream_probe(video)
    duracao_audio = _duracao_stream_probe(audios[0])
    tolerancia_video = max(2 / FPS, 0.1)
    if abs(duracao_video - duracao_esperada) > tolerancia_video:
        raise RuntimeError(
            "Stream de video final divergiu do audio mestre: "
            f"video={duracao_video:.3f}s mestre={duracao_esperada:.3f}s."
        )
    if abs(duracao_audio - duracao_esperada) > 0.1:
        raise RuntimeError(
            "Stream de audio final divergiu do audio mestre: "
            f"audio={duracao_audio:.3f}s mestre={duracao_esperada:.3f}s."
        )


def _duracao_stream_probe(stream: dict[str, Any]) -> float:
    candidatos: list[Any] = [stream.get("duration")]
    duration_ts = stream.get("duration_ts")
    time_base = str(stream.get("time_base") or "")
    if duration_ts is not None and "/" in time_base:
        try:
            numerador, denominador = time_base.split("/", 1)
            candidatos.append(
                float(duration_ts) * (float(numerador) / float(denominador))
            )
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    for value in candidatos:
        try:
            duracao = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(duracao) and duracao > 0:
            return duracao
    raise RuntimeError("ffprobe nao informou duracao valida para um stream final.")


def _criar_diretorio_execucao(nicho: str, tema: str) -> Path:
    """Retorna um diretório persistente por tema para permitir retomada segura."""

    TEMP_HORIZONTAL_DIR.mkdir(parents=True, exist_ok=True)
    path = TEMP_HORIZONTAL_DIR / f"{nicho}_{tema}"
    path.mkdir(parents=False, exist_ok=True)
    return path.resolve()


def _caminho_lock_tema(nicho: str, tema: str) -> Path:
    """Retorna a trava adjacente ao diretório persistente de resume do tema."""

    return TEMP_HORIZONTAL_DIR / f"{nicho}_{tema}.lock"


def _limpar_diretorio_execucao(path: Path) -> None:
    resolved = path.resolve()
    raiz = TEMP_HORIZONTAL_DIR.resolve()
    try:
        relativo = resolved.relative_to(raiz)
    except ValueError as exc:
        raise RuntimeError(f"Recusa ao limpar diretorio fora do temp horizontal: {resolved}") from exc
    if len(relativo.parts) != 1 or not resolved.is_dir():
        raise RuntimeError(f"Diretorio de execucao inseguro para limpeza: {resolved}")
    rmtree_com_retry(resolved)


def _limpar_lote_horizontal_concluido(path: Path, output_path: Path) -> None:
    """Remove only a direct theme directory after its final output is safe."""

    resolved = path.resolve()
    output_resolved = output_path.resolve()
    raiz = LOTES_HORIZONTAIS_DIR.resolve()

    try:
        relativo = resolved.relative_to(raiz)
    except ValueError as exc:
        raise RuntimeError(
            f"Recusa ao limpar lote fora de lotes_horizontais: {resolved}"
        ) from exc

    if len(relativo.parts) != 2 or not resolved.is_dir():
        raise RuntimeError(f"Diretorio de lote inseguro para limpeza: {resolved}")
    if not output_resolved.is_file() or output_resolved.stat().st_size <= 0:
        raise RuntimeError(
            f"Resultado final ausente ou vazio; lote preservado: {output_resolved}"
        )

    try:
        output_resolved.relative_to(resolved)
    except ValueError:
        pass
    else:
        raise RuntimeError(
            "Recusa ao apagar lote porque o resultado final esta dentro dele: "
            f"{output_resolved}"
        )

    rmtree_com_retry(resolved)


def _validar_arquivo_nao_vazio(
    path: Path,
    descricao: str,
    *,
    erro_hitl: bool = False,
) -> None:
    if path.is_file() and path.stat().st_size > 0:
        return
    mensagem = f"{descricao} ausente ou vazio: {path}"
    if erro_hitl:
        raise AuditoriaHITLError(mensagem)
    raise RuntimeError(mensagem)


def _tokenizar(texto: str) -> list[str]:
    palavras: list[str] = []
    atual: list[str] = []
    for char in texto:
        if char.isalnum() or char in {"'", "’"}:
            atual.append(char)
            continue
        if atual:
            palavras.append("".join(atual))
            atual = []
    if atual:
        palavras.append("".join(atual))
    return palavras


def _normalizar_palavra(palavra: str) -> str:
    apenas_palavra = "".join(
        "'" if char == "’" else char
        for char in palavra
        if char.isalnum() or char in {"'", "’"}
    )
    return normalizar_ascii(apenas_palavra)


def _slug(value: str) -> str:
    slug = normalizar_ascii(str(value))
    slug = re.sub(r"[^a-z0-9]+", "_", slug).strip("_")
    return slug or "video"


def _fmt_float(value: float) -> str:
    return f"{float(value):.3f}"


def main() -> int:
    args = parse_args()
    logger = get_logger(__name__)
    try:
        resumo = renderizar_horizontal(
            args.diretorio_tema,
            manter_artefatos=args.manter_artefatos,
        )
    except Exception as exc:
        logger.error("Horizontal: ERROR: %s", exc)
        return 1

    print(json.dumps(resumo, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
