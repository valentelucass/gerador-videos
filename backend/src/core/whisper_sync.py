"""CPU Whisper timestamp extraction for SynthReel."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

try:
    import whisper
except ImportError as exc:  # pragma: no cover - depends on local environment.
    whisper = None
    WHISPER_IMPORT_ERROR = exc
else:
    WHISPER_IMPORT_ERROR = None

try:
    from backend.src.config.settings import ROOT_DIR
    from backend.src.utils.logger import get_logger
except ModuleNotFoundError:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.append(str(PROJECT_ROOT))
    from backend.src.config.settings import ROOT_DIR
    from backend.src.utils.logger import get_logger


PONTUACAO_SOLTA = " \t\r\n.,;:!?\"'()[]{}<>"


class WhisperSync:
    """Extracts word-level timestamps using Whisper on CPU."""

    def __init__(self) -> None:
        if whisper is None:
            raise RuntimeError(
                "Pacote openai-whisper nao instalado. Rode: pip install openai-whisper"
            ) from WHISPER_IMPORT_ERROR

        self.logger = get_logger(__name__)
        self.logger.info("Whisper: carregando modelo tiny em CPU")
        self.model = whisper.load_model("tiny", device="cpu")

    def extrair_timestamps(self, audio_path: str | Path) -> list[dict[str, float | str]]:
        """Returns strict word timestamp dictionaries for the provided audio."""

        audio_file = Path(audio_path)
        if not audio_file.exists():
            raise FileNotFoundError(f"Audio nao encontrado: {audio_file}")

        self.logger.info("Whisper: transcrevendo %s", audio_file)
        result = self.model.transcribe(str(audio_file), word_timestamps=True, fp16=False)
        segmentos = result.get("segments", [])
        if not isinstance(segmentos, list):
            raise RuntimeError("Whisper retornou segmentos em formato inesperado.")

        timestamps: list[dict[str, float | str]] = []
        for segmento in segmentos:
            palavras = segmento.get("words", []) if isinstance(segmento, dict) else []
            if not isinstance(palavras, list):
                continue

            for palavra in palavras:
                item = self._normalizar_palavra(palavra)
                if item is not None:
                    timestamps.append(item)

        self.logger.info("Whisper: %s palavras com timestamps extraidas", len(timestamps))
        return timestamps

    @staticmethod
    def _normalizar_palavra(word_data: dict[str, Any]) -> dict[str, float | str] | None:
        palavra = str(word_data.get("word", "")).strip(PONTUACAO_SOLTA)
        if not palavra:
            return None

        inicio = WhisperSync._to_float(word_data.get("start"))
        fim = WhisperSync._to_float(word_data.get("end"))
        if inicio is None or fim is None:
            return None

        return {
            "palavra": palavra,
            "inicio": round(inicio, 3),
            "fim": round(fim, 3),
        }

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


def _audio_teste_default() -> Path:
    candidatos = [
        ROOT_DIR / "src" / "workspace" / "temp" / "whisper_sync_test.wav",
        ROOT_DIR / "clonador-voz" / "examples" / "example.wav",
        ROOT_DIR / "clonador-voz" / "examples" / "minha_voz.wav",
    ]
    for candidato in candidatos:
        if candidato.exists():
            return candidato
    raise FileNotFoundError(
        "Nenhum audio de teste encontrado. Passe o caminho: "
        "python src/core/whisper_sync.py caminho/do/audio.wav"
    )


def _teste_isolado() -> None:
    audio_path = Path(sys.argv[1]) if len(sys.argv) > 1 else _audio_teste_default()
    sync = WhisperSync()
    timestamps = sync.extrair_timestamps(audio_path)
    print(json.dumps(timestamps, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    _teste_isolado()
