"""Síntese neural dedicada aos vídeos horizontais."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

import edge_tts


# Catálogo conferido pela instalação atual do Edge TTS. Ele é o contrato
# canônico para a voz declarada no JSON; nenhuma voz de outro locale é usada.
VOICE_CATALOG = {
    "pt-BR": {"Masculinas": ("pt-BR-AntonioNeural",), "Femininas": ("pt-BR-FranciscaNeural", "pt-BR-ThalitaMultilingualNeural")},
    "pl-PL": {"Masculinas": ("pl-PL-MarekNeural",), "Femininas": ("pl-PL-ZofiaNeural",)},
    "hr-HR": {"Masculinas": ("hr-HR-SreckoNeural",), "Femininas": ("hr-HR-GabrijelaNeural",)},
    "en-US": {"Masculinas": ("en-US-AndrewMultilingualNeural", "en-US-AndrewNeural", "en-US-BrianMultilingualNeural", "en-US-BrianNeural", "en-US-ChristopherNeural", "en-US-EricNeural", "en-US-GuyNeural", "en-US-RogerNeural", "en-US-SteffanNeural"), "Femininas": ("en-US-AnaNeural", "en-US-AriaNeural", "en-US-AvaMultilingualNeural", "en-US-AvaNeural", "en-US-EmmaMultilingualNeural", "en-US-EmmaNeural", "en-US-JennyNeural", "en-US-MichelleNeural")},
    "es-ES": {"Masculinas": ("es-ES-AlvaroNeural",), "Femininas": ("es-ES-ElviraNeural", "es-ES-XimenaNeural")},
    "de-DE": {"Masculinas": ("de-DE-ConradNeural", "de-DE-FlorianMultilingualNeural", "de-DE-KillianNeural"), "Femininas": ("de-DE-AmalaNeural", "de-DE-KatjaNeural", "de-DE-SeraphinaMultilingualNeural")},
}


@dataclass(frozen=True)
class WordBoundary:
    """Um intervalo acústico emitido pelo próprio Edge TTS.

    Os offsets entregues pela API vêm em unidades de 100 ns. Convertê-los aqui
    mantém o resto do pipeline em segundos e permite alinhar cada cena à fala
    real, sem estimar duração por contagem de palavras.
    """

    text: str
    start: float
    end: float


class TTSNeuralEngine:
    """Gera a narração com Edge TTS, em uma cadência documental mais pausada."""

    RATE = "-10%"
    VOICES = {
        "pt": "pt-BR-AntonioNeural", "pt-BR": "pt-BR-AntonioNeural",
        "pl": "pl-PL-MarekNeural", "pl-PL": "pl-PL-MarekNeural",
        "hr": "hr-HR-SreckoNeural", "hr-HR": "hr-HR-SreckoNeural",
        "en": "en-US-GuyNeural", "en-US": "en-US-GuyNeural",
        "es": "es-ES-AlvaroNeural", "es-ES": "es-ES-AlvaroNeural",
        "de": "de-DE-ConradNeural", "de-DE": "de-DE-ConradNeural",
    }

    @classmethod
    def voice_for(cls, language: str) -> str:
        normalized = language.strip().replace("_", "-")
        if normalized in cls.VOICES:
            return cls.VOICES[normalized]
        base = normalized.split("-", 1)[0].lower()
        if base in cls.VOICES:
            return cls.VOICES[base]
        raise ValueError(
            "Idioma não suportado para a voz neural: "
            f"{language}. Use pt, pl, hr, en, es ou de (ou seus locales)."
        )

    @classmethod
    def default_voice_for(cls, language: str, narrator_gender: str) -> str:
        """Resolve roteiros legados que ainda não trazem ``voice``.

        Os novos roteiros sempre devem declarar a voz no JSON. Este fallback
        preserva os roteiros já salvos, respeitando o ``narrator_gender`` que
        eles já tinham informado.
        """
        if narrator_gender == "male":
            # Mantém a seleção automática histórica para os roteiros legados.
            return cls.voice_for(language)
        if narrator_gender == "female":
            locale = cls.locale_for(language)
            return VOICE_CATALOG[locale]["Femininas"][0]
        return cls.voice_for(language)

    @classmethod
    def validate_voice(cls, language: str, selected_voice: str) -> str:
        """Retorna uma voz válida para o locale ou falha com erro explícito."""
        voice = selected_voice.strip()
        locale = cls.locale_for(language)
        available = {item for group in VOICE_CATALOG[locale].values() for item in group}
        if voice not in available:
            raise ValueError(f"A voz {selected_voice} não pertence ao idioma {locale}.")
        return voice

    @classmethod
    def gender_for_voice(cls, language: str, selected_voice: str) -> str:
        """Obtém o gênero de catálogo da voz, depois de validar seu locale."""
        voice = cls.validate_voice(language, selected_voice)
        locale = cls.locale_for(language)
        if voice in VOICE_CATALOG[locale]["Masculinas"]:
            return "male"
        return "female"

    @staticmethod
    def prepare_text(text: str) -> str:
        cleaned = re.sub(r"\s+", " ", text).strip()
        if not cleaned:
            raise ValueError("O roteiro não contém texto para narração.")
        # Pontuação explícita é o que preserva a respiração da narração no Edge.
        return re.sub(r"([.!?])(?=\S)", r"\1 ", cleaned)

    async def synthesize(self, text: str, language: str, output: Path, voice: str | None = None) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.stem}.tmp{output.suffix}")
        if temporary.exists():
            temporary.unlink()
        try:
            await edge_tts.Communicate(
                self.prepare_text(text), self._selected_voice(language, voice), rate=self.RATE
            ).save(str(temporary))
            if not temporary.exists() or temporary.stat().st_size == 0:
                raise RuntimeError("O Edge TTS não retornou um arquivo de áudio válido.")
            temporary.replace(output)
            return output
        finally:
            if temporary.exists():
                temporary.unlink()

    def synthesize_sync(self, text: str, language: str, output: Path, voice: str | None = None) -> Path:
        return asyncio.run(self.synthesize(text, language, output, voice))

    async def synthesize_with_word_boundaries(
        self,
        text: str,
        language: str,
        output: Path,
        voice: str | None = None,
    ) -> list[WordBoundary]:
        """Sintetiza uma única narração e devolve seus time-codes acústicos.

        A narração continua sendo um fluxo único — não há cortes ou pausas
        artificiais entre blocos. Os metadados de ``WordBoundary`` vêm da mesma
        síntese que originou o MP3 entregue ao compositor.
        """
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.stem}.tmp{output.suffix}")
        if temporary.exists():
            temporary.unlink()

        boundaries: list[WordBoundary] = []
        try:
            communication = edge_tts.Communicate(
                self.prepare_text(text),
                self._selected_voice(language, voice),
                rate=self.RATE,
                boundary="WordBoundary",
            )
            with temporary.open("wb") as audio:
                async for message in communication.stream():
                    if message["type"] == "audio":
                        audio.write(message["data"])
                    elif message["type"] == "WordBoundary":
                        start = float(message["offset"]) / 10_000_000
                        duration = float(message["duration"]) / 10_000_000
                        boundaries.append(WordBoundary(str(message["text"]), start, start + duration))

            if not temporary.exists() or temporary.stat().st_size == 0:
                raise RuntimeError("O Edge TTS não retornou um arquivo de áudio válido.")
            if not boundaries:
                raise RuntimeError("O Edge TTS não retornou time-codes de palavras para a narração.")
            temporary.replace(output)
            return boundaries
        finally:
            if temporary.exists():
                temporary.unlink()

    def synthesize_with_word_boundaries_sync(
        self,
        text: str,
        language: str,
        output: Path,
        voice: str | None = None,
    ) -> list[WordBoundary]:
        return asyncio.run(self.synthesize_with_word_boundaries(text, language, output, voice))

    @classmethod
    def _selected_voice(cls, language: str, selected_voice: str | None) -> str:
        if not selected_voice:
            return cls.voice_for(language)
        return cls.validate_voice(language, selected_voice)

    @classmethod
    def locale_for(cls, language: str) -> str:
        normalized = language.strip().replace("_", "-")
        if normalized in VOICE_CATALOG:
            return normalized
        base = normalized.split("-", 1)[0].lower()
        return next((locale for locale in VOICE_CATALOG if locale.split("-", 1)[0] == base), "") or cls._unsupported(language)

    @staticmethod
    def _unsupported(language: str) -> str:
        raise ValueError(f"Idioma não suportado: {language}.")
