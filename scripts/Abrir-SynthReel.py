"""Inicia o SynthReel local e o encerra junto com a janela do Firefox."""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = PROJECT_ROOT / "frontend"
API_URL = "http://127.0.0.1:8000/api/catalog"
PANEL_URL = "http://127.0.0.1:5173"
WINDOWS_NO_CONSOLE = getattr(subprocess, "CREATE_NO_WINDOW", 0)
LOG_PATH = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "SynthReel" / "launcher.log"
_last_url_error = "sem tentativa"


def log(message: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as file:
        file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")


def show_error(message: str) -> None:
    """Exibe a falha sem abrir um terminal para quem clicou no atalho."""
    ctypes.windll.user32.MessageBoxW(0, message, "SynthReel", 0x10)


def url_is_ready(url: str) -> bool:
    global _last_url_error
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            _last_url_error = f"HTTP {response.status}"
            return 200 <= response.status < 500
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        _last_url_error = str(error)
        return False


def wait_for_url(url: str, label: str, timeout_seconds: float = 35) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if url_is_ready(url):
            return
        time.sleep(0.25)
    raise RuntimeError(
        f"{label} não respondeu em {timeout_seconds:.0f} segundos. "
        f"Último retorno: {_last_url_error}. Log: {LOG_PATH}"
    )


def find_firefox() -> Path:
    candidates = (
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Mozilla Firefox" / "firefox.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Mozilla Firefox" / "firefox.exe",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("O Mozilla Firefox não foi encontrado neste computador.")


class _ProcessEntry(ctypes.Structure):
    """Versão Unicode de PROCESSENTRY32 para localizar o filho do Firefox."""

    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def firefox_child_pid(parent_pid: int) -> int | None:
    """Retorna o processo real que o bootstrap do Firefox cria para a janela."""
    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    invalid_handle = ctypes.c_void_p(-1).value
    if snapshot == invalid_handle:
        return None
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(_ProcessEntry)
        has_entry = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while has_entry:
            if (
                entry.th32ParentProcessID == parent_pid
                and entry.szExeFile.lower() == "firefox.exe"
            ):
                return int(entry.th32ProcessID)
            has_entry = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return None


def wait_for_firefox_window_process(bootstrap: subprocess.Popen[bytes]) -> int:
    """Espera o processo que realmente detém a janela isolada do Firefox."""
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        child_pid = firefox_child_pid(bootstrap.pid)
        if child_pid is not None:
            return child_pid
        time.sleep(0.1)
    if bootstrap.poll() is None:
        # Versões que não usam bootstrap mantêm o próprio processo inicial.
        return bootstrap.pid
    raise RuntimeError("O Firefox iniciou, mas não criou a janela do SynthReel.")


def wait_for_process_exit(pid: int) -> None:
    """Aguarda a janela do Firefox sem depender do processo bootstrap."""
    synchronize = 0x00100000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        return
    try:
        kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)  # INFINITE
    finally:
        kernel32.CloseHandle(handle)


def start_hidden(command: list[str], working_directory: Path) -> subprocess.Popen[bytes]:
    """Inicia um processo próprio do atalho sem deixar console aberto."""
    log(f"Iniciando em {working_directory}: {subprocess.list2cmdline(command)}")
    with LOG_PATH.open("ab") as output:
        return subprocess.Popen(
            command,
            cwd=working_directory,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=WINDOWS_NO_CONSOLE,
        )


def stop_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Para apenas a árvore que este atalho criou."""
    if process.poll() is not None:
        return
    subprocess.run(
        ["taskkill.exe", "/pid", str(process.pid), "/t", "/f"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=WINDOWS_NO_CONSOLE,
        check=False,
    )


def stop_managed_processes(processes: list[subprocess.Popen[bytes]]) -> None:
    for process in reversed(processes):
        stop_process_tree(process)


def python_for_backend() -> str:
    """Troca pythonw pelo Python normal, ainda sem console pelo CREATE_NO_WINDOW."""
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        return str(executable.with_name("python.exe"))
    return str(executable)


def start_missing_services() -> list[subprocess.Popen[bytes]]:
    """Sobe somente os serviços ausentes e retorna os processos sob nosso controle."""
    api_ready = url_is_ready(API_URL)
    panel_ready = url_is_ready(PANEL_URL)
    if api_ready and panel_ready:
        # Uma sessão manual pode estar renderizando; nunca a derrubamos.
        return []

    managed: list[subprocess.Popen[bytes]] = []
    if not api_ready and not panel_ready:
        backend = start_hidden(
            [python_for_backend(), "-m", "uvicorn", "backend.src.main:app", "--host", "127.0.0.1", "--port", "8000"],
            PROJECT_ROOT,
        )
        managed.append(backend)
        try:
            wait_for_url(API_URL, "A API")
            vite = FRONTEND_ROOT / "node_modules" / "vite" / "bin" / "vite.js"
            if not vite.is_file():
                raise RuntimeError("O Vite não está instalado. Execute npm install na pasta frontend.")
            panel = start_hidden(["node.exe", str(vite)], FRONTEND_ROOT)
            managed.append(panel)
            wait_for_url(PANEL_URL, "O painel")
        except Exception:
            stop_managed_processes(managed)
            raise
        return managed
    if api_ready:
        vite = FRONTEND_ROOT / "node_modules" / "vite" / "bin" / "vite.js"
        if not vite.is_file():
            raise RuntimeError("O Vite não está instalado. Execute npm install na pasta frontend.")
        process = start_hidden(["node.exe", str(vite)], FRONTEND_ROOT)
        managed.append(process)
        try:
            wait_for_url(PANEL_URL, "O painel")
        except Exception:
            stop_managed_processes(managed)
            raise
        return managed

    process = start_hidden(
        [python_for_backend(), "-m", "uvicorn", "backend.src.main:app", "--host", "127.0.0.1", "--port", "8000"],
        PROJECT_ROOT,
    )
    managed.append(process)
    try:
        wait_for_url(API_URL, "A API")
    except Exception:
        stop_managed_processes(managed)
        raise
    return managed


def main() -> None:
    if not (FRONTEND_ROOT / "package.json").is_file():
        raise RuntimeError(f"A pasta do front-end não foi encontrada em:\n{FRONTEND_ROOT}")

    managed_processes: list[subprocess.Popen[bytes]] = []
    profile: Path | None = None
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text("", encoding="utf-8")
    log(f"Iniciador aberto por {sys.executable}")
    try:
        managed_processes = start_missing_services()
        log(f"Serviços prontos; processos gerenciados: {[process.pid for process in managed_processes]}")
        profile = Path(tempfile.mkdtemp(prefix="SynthReel-Firefox-"))
        # Perfil isolado + -no-remote fazem esta instância do Firefox ser
        # independente de outras janelas pessoais já abertas pelo usuário.
        firefox_environment = os.environ.copy()
        firefox_environment["MOZ_NO_REMOTE"] = "1"
        browser_bootstrap = subprocess.Popen(
            [
                str(find_firefox()),
                "-new-instance",
                "-no-remote",
                "-profile",
                str(profile),
                "-new-window",
                PANEL_URL,
            ],
            env=firefox_environment,
        )
        browser_pid = wait_for_firefox_window_process(browser_bootstrap)
        log(f"Firefox iniciado; bootstrap {browser_bootstrap.pid}, janela {browser_pid}")
        wait_for_process_exit(browser_pid)
        log("A janela isolada do Firefox foi fechada.")
    finally:
        stop_managed_processes(managed_processes)
        log("Processos gerenciados encerrados.")
        if profile is not None:
            shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # a mensagem é a única interface em caso de falha
        log(f"Falha: {error}")
        show_error(str(error))
