"""Teste isolado do motor TTS neural horizontal."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.core.tts_neural import TTSNeuralEngine


TEXTO_TESTE = (
    "Witajcie. To jest test polskiego głosu do naszego nowego filmu "
    "dokumentalnego. Imperium Rzymskie upadło."
)


async def main() -> None:
    destino = ROOT_DIR / "teste_polones.mp3"
    motor = TTSNeuralEngine()
    audio_gerado = await motor.sintetizar(
        texto=TEXTO_TESTE,
        idioma="pl-PL",
        caminho_saida=destino,
    )
    print(Path(audio_gerado).resolve())


if __name__ == "__main__":
    asyncio.run(main())
