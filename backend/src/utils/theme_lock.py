"""Trava atomica de exclusao mutua para uma execucao horizontal por tema."""

from __future__ import annotations

import ctypes
import json
import os
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from backend.src.utils.file_retry import unlink_com_retry


class ThemeLockCollisionError(RuntimeError):
    """Raised when another active process owns the same theme lock."""


def pid_esta_ativo(pid: int) -> bool:
    """Verifica PID sem enviar sinais destrutivos em Windows ou Unix."""

    if pid <= 0:
        return False
    if os.name == "nt":
        return _pid_esta_ativo_windows(pid)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pid_esta_ativo_windows(pid: int) -> bool:
    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    )
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        # Access denied proves que o PID existe, mesmo sem permissao de consulta.
        return ctypes.get_last_error() == 5

    exit_code = wintypes.DWORD()
    try:
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


@dataclass(frozen=True)
class TravaTema:
    """Owner de um lock criado com ``O_CREAT | O_EXCL``."""

    path: Path
    pid: int
    timestamp: float
    token: str

    @classmethod
    def adquirir(cls, path: str | Path) -> "TravaTema":
        caminho = Path(path)
        caminho.parent.mkdir(parents=True, exist_ok=True)

        while True:
            try:
                return cls._criar_atomicamente(caminho)
            except FileExistsError:
                info = _ler_lock_existente(caminho)
                pid_existente = _pid_do_lock(info)
                if pid_existente is None:
                    raise ThemeLockCollisionError(
                        f"Trava de tema invalida ou em criacao: {caminho}. "
                        "Nao e seguro removê-la automaticamente."
                    )
                if pid_esta_ativo(pid_existente):
                    raise ThemeLockCollisionError(
                        f"Colisao de execucao para o tema em {caminho}: "
                        f"PID ativo {pid_existente}."
                    )

                # O PID registrado terminou; somente agora a trava e orfa.
                try:
                    unlink_com_retry(caminho)
                except FileNotFoundError:
                    # Outra instancia pode ter removido o mesmo lock entre a
                    # leitura e o unlink. Volte a competir pelo create exclusivo.
                    continue

    @classmethod
    def _criar_atomicamente(cls, path: Path) -> "TravaTema":
        pid = os.getpid()
        timestamp = time.time()
        token = uuid4().hex
        conteudo = json.dumps(
            {"pid": pid, "timestamp": timestamp, "token": token},
            sort_keys=True,
        ).encode("utf-8")
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            escritos = os.write(descriptor, conteudo)
            if escritos != len(conteudo):
                raise OSError("Nao foi possivel gravar o lock de tema integralmente.")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return cls(path=path, pid=pid, timestamp=timestamp, token=token)

    def liberar(self) -> None:
        """Remove somente o lock que pertence a esta instancia."""

        if not self.path.exists():
            return
        info = _ler_lock_existente(self.path)
        if info.get("token") != self.token:
            raise ThemeLockCollisionError(
                f"A trava {self.path} mudou de dono durante a execucao; ela sera preservada."
            )
        unlink_com_retry(self.path)

    def __enter__(self) -> "TravaTema":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        self.liberar()
        return False


def _ler_lock_existente(path: Path) -> dict[str, object]:
    try:
        conteudo = path.read_text(encoding="utf-8")
        info = json.loads(conteudo)
    except (OSError, json.JSONDecodeError):
        return {}
    return info if isinstance(info, dict) else {}


def _pid_do_lock(info: dict[str, object]) -> int | None:
    valor = info.get("pid")
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int) and valor > 0:
        return valor
    return None
