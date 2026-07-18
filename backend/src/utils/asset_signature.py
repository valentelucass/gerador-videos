"""Assinaturas rápidas e robustas para assets grandes de video."""

from __future__ import annotations

import hashlib
from pathlib import Path


HASH_SLICE_BYTES = 4 * 1024 * 1024


def calcular_hash_hibrido(caminho_arquivo: Path) -> str:
    """Retorna SHA-256 do tamanho e das extremidades binárias de um arquivo.

    Ler somente as duas fatias de 4 MiB detecta substituicoes de assets sem
    transformar o preflight do Resume Mode em leitura integral de videos
    grandes. Arquivos menores que uma fatia sao lidos uma unica vez.
    """

    arquivo = Path(caminho_arquivo)
    tamanho = arquivo.stat().st_size
    hasher = hashlib.sha256()
    hasher.update(str(tamanho).encode("ascii"))
    hasher.update(b"\0")

    with arquivo.open("rb") as stream:
        hasher.update(stream.read(HASH_SLICE_BYTES))
        if tamanho > HASH_SLICE_BYTES:
            stream.seek(max(0, tamanho - HASH_SLICE_BYTES))
            hasher.update(stream.read(HASH_SLICE_BYTES))

    return hasher.hexdigest()
