"""Voice switchboard for SynthReel narration."""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable

import edge_tts

try:
    from backend.src.config.settings import ROOT_DIR
    from backend.src.utils.logger import get_logger
except ModuleNotFoundError:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.append(str(PROJECT_ROOT))
    from backend.src.config.settings import ROOT_DIR
    from backend.src.utils.logger import get_logger


class TTSManager:
    """Routes narration to Edge neural voices or to the local cloned voice."""

    VOZES_NEURAIS = {
        "homem_01": "pt-BR-AntonioNeural",
        "mulher_01": "pt-BR-FranciscaNeural",
        "mulher_02": "pt-BR-ThalitaNeural",
    }
    VOZ_CLONE_LOCAL = "lucas_clone"
    DEFAULT_CLONADOR_SCRIPT = ROOT_DIR / "src" / "core" / "voxcpm_runner.py"
    DEFAULT_CLONADOR_ARGS = ("--text", "{texto}", "--output", "{output_path}")

    def __init__(
        self,
        clonador_dir: str | Path | None = None,
        clonador_script: str | Path | None = None,
        python_bin: str | Path | None = None,
        clonador_args: Iterable[str] | None = None,
    ) -> None:
        self.logger = get_logger(__name__)
        self.clonador_dir = Path(clonador_dir) if clonador_dir else ROOT_DIR / "clonador-voz"
        self.clonador_script = Path(
            clonador_script or os.getenv("CLONADOR_VOZ_SCRIPT", self.DEFAULT_CLONADOR_SCRIPT)
        )
        self.python_bin = Path(python_bin) if python_bin else self._resolver_python_clonador()
        self.clonador_args = tuple(clonador_args or self._args_clonador_env() or self.DEFAULT_CLONADOR_ARGS)
        self.clonador_retries = max(0, int(os.getenv("CLONADOR_VOZ_RETRIES", "1")))

    def narrar(self, texto: str, output_path: str | Path, voz: str) -> str:
        """Generates narration and returns the produced audio path."""

        texto = self._validar_texto(texto)
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        if voz == self.VOZ_CLONE_LOCAL:
            return self._narrar_lucas_clone(texto, output_file)

        if voz in self.VOZES_NEURAIS:
            return self._narrar_edge_neural(texto, voz, output_file)

        vozes = sorted([self.VOZ_CLONE_LOCAL, *self.VOZES_NEURAIS])
        raise ValueError(f"Voz desconhecida: {voz}. Vozes validas: {', '.join(vozes)}")

    def _narrar_lucas_clone(self, texto: str, output_path: Path) -> str:
        if not self.clonador_dir.exists():
            raise FileNotFoundError(f"Pasta clonador-voz nao encontrada: {self.clonador_dir}")

        script_path = self._resolver_script_clonador()
        if not script_path.exists():
            raise FileNotFoundError(
                "Script principal do clonador nao encontrado: "
                f"{script_path}. Ajuste CLONADOR_VOZ_SCRIPT ou o parametro clonador_script."
            )

        command = [
            str(self.python_bin),
            str(script_path),
            *self._renderizar_args_clonador(texto, output_path),
        ]

        self.logger.info("TTS: acionando clonador local voz=%s", self.VOZ_CLONE_LOCAL)
        for tentativa in range(1, self.clonador_retries + 2):
            try:
                subprocess.run(
                    command,
                    cwd=str(self.clonador_dir),
                    check=True,
                    capture_output=True,
                    text=True,
                )
                break
            except FileNotFoundError as exc:
                raise RuntimeError(f"Interpretador Python do clonador nao encontrado: {self.python_bin}") from exc
            except subprocess.CalledProcessError as exc:
                detail = self._compactar_saida(exc.stderr or exc.stdout)
                if tentativa <= self.clonador_retries:
                    self.logger.warning(
                        "TTS: clonador local falhou codigo=%s; tentando novamente (%s/%s)",
                        exc.returncode,
                        tentativa,
                        self.clonador_retries,
                    )
                    time.sleep(3)
                    continue
                raise RuntimeError(
                    f"Falha ao executar clonador-voz (codigo {exc.returncode}): {detail}"
                ) from exc

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError(
                "O clonador terminou sem gerar o arquivo esperado: "
                f"{output_path}. Ajuste os argumentos em CLONADOR_VOZ_ARGS."
            )

        self.logger.info("TTS: audio clonado salvo em %s", output_path)
        return str(output_path.resolve())

    def _narrar_edge_neural(self, texto: str, voz: str, output_path: Path) -> str:
        """Generates neural narration through Microsoft Edge TTS."""

        output_path.parent.mkdir(parents=True, exist_ok=True)
        voz_edge = self.VOZES_NEURAIS.get(voz, "pt-BR-AntonioNeural")

        async def _gerar() -> None:
            communicate = edge_tts.Communicate(texto, voz_edge)
            await communicate.save(str(output_path))

        try:
            asyncio.run(_gerar())
        except Exception as exc:
            raise RuntimeError(f"Falha ao gerar voz neural com edge-tts: {exc}") from exc

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError(f"Edge TTS nao gerou audio em: {output_path}")

        self.logger.info("TTS: voz neural %s salva em %s", voz_edge, output_path)
        return str(output_path.resolve())

    def _resolver_python_clonador(self) -> Path:
        env_python = os.getenv("CLONADOR_VOZ_PYTHON")
        if env_python:
            return Path(env_python)

        venv_python = self.clonador_dir / ".venv" / "Scripts" / "python.exe"
        if venv_python.exists():
            return venv_python

        return Path(sys.executable)

    def _resolver_script_clonador(self) -> Path:
        if self.clonador_script.is_absolute():
            return self.clonador_script

        root_candidate = ROOT_DIR / self.clonador_script
        if root_candidate.exists():
            return root_candidate

        return self.clonador_dir / self.clonador_script

    def _renderizar_args_clonador(self, texto: str, output_path: Path) -> list[str]:
        contexto = {
            "texto": texto,
            "output_path": str(output_path.resolve()),
            "voz": self.VOZ_CLONE_LOCAL,
        }
        return [arg.format(**contexto) for arg in self.clonador_args]

    @staticmethod
    def _args_clonador_env() -> tuple[str, ...] | None:
        raw_args = os.getenv("CLONADOR_VOZ_ARGS")
        if not raw_args:
            return None
        return tuple(shlex.split(raw_args))

    @staticmethod
    def _validar_texto(texto: str) -> str:
        texto = texto.strip()
        if not texto:
            raise ValueError("texto nao pode ser vazio.")
        return texto

    @staticmethod
    def _compactar_saida(output: str | None) -> str:
        if not output:
            return "sem detalhes no stderr/stdout"
        linhas = [linha.strip() for linha in output.splitlines() if linha.strip()]
        return " | ".join(linhas[-8:]) if linhas else "sem detalhes no stderr/stdout"


class TTSClonador(TTSManager):
    """Backward-compatible alias for previous code that imported TTSClonador."""
