"""Síntese neural dedicada aos vídeos horizontais."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import edge_tts


# Catálogo conferido pela instalação atual do Edge TTS. As vozes permanecem
# separadas por idioma e gênero no painel; nenhuma voz de outro locale é usada.
VOICE_CATALOG = {
    "pt-BR": {"Masculinas": ("pt-BR-AntonioNeural",), "Femininas": ("pt-BR-FranciscaNeural", "pt-BR-ThalitaMultilingualNeural")},
    "pl-PL": {"Masculinas": ("pl-PL-MarekNeural",), "Femininas": ("pl-PL-ZofiaNeural",)},
    "hr-HR": {"Masculinas": ("hr-HR-SreckoNeural",), "Femininas": ("hr-HR-GabrijelaNeural",)},
    "en-US": {"Masculinas": ("en-US-AndrewMultilingualNeural", "en-US-AndrewNeural", "en-US-BrianMultilingualNeural", "en-US-BrianNeural", "en-US-ChristopherNeural", "en-US-EricNeural", "en-US-GuyNeural", "en-US-RogerNeural", "en-US-SteffanNeural"), "Femininas": ("en-US-AnaNeural", "en-US-AriaNeural", "en-US-AvaMultilingualNeural", "en-US-AvaNeural", "en-US-EmmaMultilingualNeural", "en-US-EmmaNeural", "en-US-JennyNeural", "en-US-MichelleNeural")},
    "es-ES": {"Masculinas": ("es-ES-AlvaroNeural",), "Femininas": ("es-ES-ElviraNeural", "es-ES-XimenaNeural")},
    "de-DE": {"Masculinas": ("de-DE-ConradNeural", "de-DE-FlorianMultilingualNeural", "de-DE-KillianNeural"), "Femininas": ("de-DE-AmalaNeural", "de-DE-KatjaNeural", "de-DE-SeraphinaMultilingualNeural")},
}


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

    @classmethod
    def _selected_voice(cls, language: str, selected_voice: str | None) -> str:
        if not selected_voice:
            return cls.voice_for(language)
        locale = cls.locale_for(language)
        available = {voice for group in VOICE_CATALOG[locale].values() for voice in group}
        if selected_voice not in available:
            raise ValueError(f"A voz {selected_voice} não pertence ao idioma {locale}.")
        return selected_voice

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
