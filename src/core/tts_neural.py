"""Independent neural TTS engine for the horizontal SynthReel pipeline."""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from uuid import uuid4

import aiohttp
import edge_tts

try:
    from src.utils.logger import get_logger
except ModuleNotFoundError:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.append(str(PROJECT_ROOT))
    from src.utils.logger import get_logger


class TTSNeuralEngine:
    """Generates narration with Microsoft Edge neural voices.

    This module is isolated from the legacy cloned-voice pipeline and is meant
    for longer horizontal videos, where a slower and clearer cadence is needed.
    """

    DEFAULT_IDIOMA = "pl-PL"
    DEFAULT_RATE = "-10%"
    DEFAULT_TENTATIVAS = 3

    VOZES_PADRAO = {
        "pt-BR": "pt-BR-AntonioNeural",
        "pt": "pt-BR-AntonioNeural",
        "pl-PL": "pl-PL-MarekNeural",
        "pl": "pl-PL-MarekNeural",
        "hr-HR": "hr-HR-SreckoNeural",
        "hr": "hr-HR-SreckoNeural",
        "en-US": "en-US-GuyNeural",
        "en": "en-US-GuyNeural",
        "es-ES": "es-ES-AlvaroNeural",
        "es": "es-ES-AlvaroNeural",
        "de-DE": "de-DE-ConradNeural",
        "de": "de-DE-ConradNeural",
    }

    _SSML_BREAKS_SUPORTADOS = False
    _PONTO_SEM_ESPACO_RE = re.compile(r"\.(?=\s*[A-ZÀ-ÖØ-Þ])")
    _ESPACOS_RE = re.compile(r"\s+")

    def __init__(
        self,
        *,
        rate: str = DEFAULT_RATE,
        tentativas: int = DEFAULT_TENTATIVAS,
        connect_timeout: int = 10,
        receive_timeout: int = 60,
        retry_delay: float = 2.0,
    ) -> None:
        if tentativas < 1:
            raise ValueError("tentativas deve ser maior ou igual a 1.")

        self.rate = rate
        self.tentativas = tentativas
        self.connect_timeout = connect_timeout
        self.receive_timeout = receive_timeout
        self.retry_delay = retry_delay
        self.logger = get_logger(__name__)

    async def sintetizar(
        self,
        texto: str,
        idioma: str = DEFAULT_IDIOMA,
        caminho_saida: str | Path | None = None,
    ) -> str:
        """Synthesizes text into an audio file and returns the absolute path."""

        if caminho_saida is None:
            raise ValueError("caminho_saida e obrigatorio.")

        texto_preparado = self._preparar_texto_para_diccao(texto)
        voz = self._voz_padrao(idioma)
        output_path = Path(caminho_saida)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._criar_caminho_temporario(output_path)

        ultimo_erro: BaseException | None = None
        for tentativa in range(1, self.tentativas + 1):
            try:
                self._remover_temporario(temp_path)

                communicate = edge_tts.Communicate(
                    texto_preparado,
                    voz,
                    rate=self.rate,
                    boundary="SentenceBoundary",
                    connect_timeout=self.connect_timeout,
                    receive_timeout=self.receive_timeout,
                )
                await communicate.save(str(temp_path))

                self._validar_audio(temp_path)
                temp_path.replace(output_path)
                self.logger.info(
                    "TTS neural: audio salvo em %s idioma=%s voz=%s rate=%s",
                    output_path,
                    idioma,
                    voz,
                    self.rate,
                )
                return str(output_path.resolve())
            except (asyncio.TimeoutError, TimeoutError, aiohttp.ServerTimeoutError, aiohttp.ClientError) as exc:
                ultimo_erro = exc
                self._remover_temporario(temp_path)
                if tentativa >= self.tentativas:
                    break

                self.logger.warning(
                    "TTS neural: timeout/falha de rede com voz=%s; retry %s/%s",
                    voz,
                    tentativa + 1,
                    self.tentativas,
                )
                await asyncio.sleep(self.retry_delay * tentativa)
            except Exception as exc:
                self._remover_temporario(temp_path)
                raise RuntimeError(f"Falha critica ao gerar TTS neural com edge-tts: {exc}") from exc

        raise RuntimeError(
            "Falha critica ao gerar TTS neural com edge-tts apos "
            f"{self.tentativas} tentativas: {ultimo_erro}"
        ) from ultimo_erro

    def sintetizar_sync(
        self,
        texto: str,
        caminho_saida: str | Path,
        idioma: str = DEFAULT_IDIOMA,
    ) -> str:
        """Synchronous adapter for scripts that are not async-aware yet."""

        return asyncio.run(self.sintetizar(texto=texto, idioma=idioma, caminho_saida=caminho_saida))

    def _voz_padrao(self, idioma: str) -> str:
        idioma_normalizado = (idioma or self.DEFAULT_IDIOMA).strip().replace("_", "-")
        for chave, voz in self.VOZES_PADRAO.items():
            if chave.casefold() == idioma_normalizado.casefold():
                return voz

        idioma_base = idioma_normalizado.split("-", 1)[0].lower()
        voz = self.VOZES_PADRAO.get(idioma_base)
        if voz:
            return voz

        self.logger.warning(
            "TTS neural: idioma '%s' sem voz mapeada; usando %s",
            idioma,
            self.VOZES_PADRAO[self.DEFAULT_IDIOMA],
        )
        return self.VOZES_PADRAO[self.DEFAULT_IDIOMA]

    @classmethod
    def _preparar_texto_para_diccao(cls, texto: str) -> str:
        """Normalizes punctuation so Edge TTS respects sentence cadence."""

        if not isinstance(texto, str):
            raise TypeError("texto deve ser str.")

        texto_limpo = texto.strip()
        if not texto_limpo:
            raise ValueError("texto nao pode ser vazio.")

        texto_limpo = cls._ESPACOS_RE.sub(" ", texto_limpo)
        texto_limpo = re.sub(r"\s+([,;:!?])", r"\1", texto_limpo)
        texto_limpo = re.sub(r"\s+\.", ".", texto_limpo)

        if cls._SSML_BREAKS_SUPORTADOS:
            return re.sub(r"\.(?=\s|$)", '. <break time="350ms"/> ', texto_limpo).strip()

        # edge-tts escapes input before composing SSML. Raw break tags would not
        # be a reliable pause marker here, so keep sentence punctuation explicit.
        texto_limpo = cls._PONTO_SEM_ESPACO_RE.sub(". ", texto_limpo)
        texto_limpo = re.sub(r"([.!?])\s*", r"\1 ", texto_limpo)
        return cls._ESPACOS_RE.sub(" ", texto_limpo).strip()

    @staticmethod
    def _criar_caminho_temporario(output_path: Path) -> Path:
        token = uuid4().hex
        return output_path.with_name(f".{output_path.stem}.{token}.tmp{output_path.suffix}")

    @staticmethod
    def _validar_audio(audio_path: Path) -> None:
        if not audio_path.exists() or audio_path.stat().st_size == 0:
            raise RuntimeError(f"Edge TTS nao gerou audio valido em: {audio_path}")

    @staticmethod
    def _remover_temporario(temp_path: Path) -> None:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass
