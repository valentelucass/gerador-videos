"""Operacoes de arquivo resilientes a locks transitórios do Windows."""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar


T = TypeVar("T")
BACKOFF_LOCK_SECONDS = (1.0, 2.0, 4.0)


def executar_com_retry_lock(operacao: Callable[[], T], *, descricao: str) -> T:
    """Executa uma operacao de arquivo com quatro tentativas no caso de lock.

    O Windows pode manter handles breves enquanto OneDrive/antivirus inspeciona
    um arquivo recem-fechado. A primeira chamada mais tres retries respeitam a
    sequencia 1s, 2s e 4s. Outros erros de ``PermissionError`` seguem a mesma
    politica: ao fim, a excecao original e preservada para diagnostico.
    """

    for tentativa, espera in enumerate((*BACKOFF_LOCK_SECONDS, None), start=1):
        try:
            return operacao()
        except PermissionError:
            if espera is None:
                raise
            time.sleep(espera)

    raise RuntimeError(f"Retry de lock terminou sem executar: {descricao}")


def unlink_com_retry(path: str | Path) -> None:
    """Remove um arquivo usando ``os.unlink`` com retry para locks transitórios."""

    arquivo = Path(path)
    executar_com_retry_lock(lambda: os.unlink(arquivo), descricao=f"remocao de {arquivo}")


def replace_com_retry(origem: str | Path, destino: str | Path) -> None:
    """Substitui um arquivo via ``os.replace`` com retry para locks transitórios."""

    source = Path(origem)
    target = Path(destino)
    executar_com_retry_lock(
        lambda: os.replace(source, target),
        descricao=f"substituicao de {source} por {target}",
    )


def rmtree_com_retry(path: str | Path) -> None:
    """Remove uma arvore usando ``shutil.rmtree`` com retry para locks transitórios."""

    diretorio = Path(path)
    executar_com_retry_lock(
        lambda: shutil.rmtree(diretorio),
        descricao=f"limpeza de {diretorio}",
    )
