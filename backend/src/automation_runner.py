"""Controle do processo da automação externa, sem acoplar ao renderizador horizontal."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from threading import RLock
import re


class AutomationRunner:
    """Inicia, interrompe e observa o processo Playwright isolado em ``automation/``."""

    IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

    def __init__(self, project_root: Path) -> None:
        self.root = project_root
        self.directory = project_root / "automation"
        self._process: subprocess.Popen[bytes] | None = None
        self._started_at: float | None = None
        self._last_return_code: int | None = None
        self._lock = RLock()

    @property
    def _python(self) -> Path:
        return self.directory / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    @property
    def _manifest_path(self) -> Path:
        return self.directory / "state" / "input_manifest.json"

    @staticmethod
    def _safe_project_id(project_id: str) -> str:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", project_id):
            raise ValueError("Identificador do projeto inválido para a automação.")
        return project_id

    def _checkpoint_path_for(self, project_id: str) -> Path:
        """Mantém o histórico Vibes isolado de cada projeto do painel."""
        return self.directory / "state" / "projects" / f"{self._safe_project_id(project_id)}.json"

    def _manifest_payload(self) -> dict[str, object]:
        try:
            payload = json.loads(self._manifest_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _manifest_checkpoint_path(self) -> Path | None:
        payload = self._manifest_payload()
        value = payload.get("checkpoint_path")
        return Path(value) if isinstance(value, str) and value else None

    def _completed_count(self) -> tuple[int, int]:
        try:
            source_images = self._manifest_payload().get("images", [])
            images = [Path(item) for item in source_images]
        except TypeError:
            images = []
        checkpoint_path = self._manifest_checkpoint_path()
        try:
            states = json.loads(checkpoint_path.read_text(encoding="utf-8")).get("images", {}) if checkpoint_path and checkpoint_path.is_file() else {}
        except (OSError, json.JSONDecodeError, AttributeError):
            states = {}
        def signature(image: Path) -> str | None:
            try:
                stat = image.stat()
            except OSError:
                return None
            return f"{image.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"

        completed = sum(
            1 for image in images
            if states.get(str(image.resolve()), {}).get("status") == "success"
            and states.get(str(image.resolve()), {}).get("source_signature") == signature(image)
        )
        return completed, len(images)

    def _failed_final_images(self) -> list[str]:
        """Lista mídias que esgotaram as rodadas e exigem ação humana."""
        try:
            source_images = self._manifest_payload().get("images", [])
            images = [Path(item) for item in source_images]
            checkpoint_path = self._manifest_checkpoint_path()
            states = json.loads(checkpoint_path.read_text(encoding="utf-8")).get("images", {}) if checkpoint_path and checkpoint_path.is_file() else {}
        except (OSError, json.JSONDecodeError, TypeError, AttributeError):
            return []

        failed: list[str] = []
        for image in images:
            try:
                stat = image.stat()
            except OSError:
                continue
            signature = f"{image.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
            entry = states.get(str(image.resolve()), {})
            if entry.get("status") == "failed_final" and entry.get("source_signature") == signature:
                failed.append(image.name)
        return failed

    def _run_snapshot(self) -> dict[str, object]:
        path = self.directory / "state" / "run_state.json"
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _configured_resume_url(self) -> str | None:
        """Lê a URL de retomada sem acoplar o backend ao módulo Playwright."""
        env_path = self.directory / ".env"
        try:
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if line.startswith("RESUME_URL="):
                    value = line.split("=", 1)[1].strip()
                    return value or None
        except OSError:
            pass
        return None

    def _reap(self) -> None:
        if self._process is not None and (code := self._process.poll()) is not None:
            self._last_return_code = code
            self._process = None

    def status(self) -> dict[str, object]:
        with self._lock:
            self._reap()
            completed, total = self._completed_count()
            snapshot = self._run_snapshot()
            running = self._process is not None
            resume_url = self._configured_resume_url()
            failed_final = self._failed_final_images()
            if running:
                state = str(snapshot.get("state", "inicializando")).replace("_", " ")
                image = snapshot.get("image")
                message = f"Automação em {state}" + (f": {image}" if image else ".")
            elif self._last_return_code not in (None, 0):
                message = "A automação foi encerrada com erro. Consulte automation/logs/automation.log."
            elif failed_final:
                message = f"Automação encerrada para revisão manual: {len(failed_final)} mídia(s) esgotaram as retentativas."
            elif total and completed == total:
                message = "Todas as imagens foram concluídas."
            elif resume_url:
                message = "Automação pronta para iniciar um projeto novo. A retomada Vibes está disponível apenas por ação explícita."
            else:
                message = "Automação pronta para iniciar."
            return {
                "running": running,
                "pid": self._process.pid if self._process else None,
                "started_at": self._started_at,
                "last_return_code": self._last_return_code,
                "completed_images": completed,
                "total_images": total,
                "message": message,
                "current_state": snapshot.get("state"),
                "current_image": snapshot.get("image"),
                "last_event": snapshot.get("event"),
                "resume_available": bool(resume_url),
                "resume_url": resume_url,
                "project_id": self._manifest_payload().get("project_id"),
                "failed_final_images": failed_final,
            }

    def start(self, filenames: list[str], *, project_id: str, resume_existing: bool = False) -> dict[str, object]:
        with self._lock:
            self._reap()
            if self._process is not None:
                return self.status()
            if not self.directory.is_dir() or not (self.directory / "main.py").is_file():
                raise FileNotFoundError("A pasta da automação não está disponível.")
            if not (self.directory / ".env").is_file():
                raise FileNotFoundError("Crie e configure automation/.env antes de iniciar.")
            if not self._python.is_file():
                raise FileNotFoundError("Instale as dependências da automação em automation/.venv.")
            project_id = self._safe_project_id(project_id)
            source_dir = self.root / "assets" / "images"
            images = [source_dir / filename for filename in filenames]
            missing = [path.name for path in images if not path.is_file()]
            invalid = [path.name for path in images if path.suffix.lower() not in self.IMAGE_EXTENSIONS]
            if missing or invalid:
                parts = []
                if missing:
                    parts.append("não encontradas: " + ", ".join(missing))
                if invalid:
                    parts.append("não são imagens aceitas: " + ", ".join(invalid))
                raise ValueError("Mídias inválidas para animação — " + "; ".join(parts))
            self._manifest_path.parent.mkdir(parents=True, exist_ok=True)
            checkpoint_path = self._checkpoint_path_for(project_id)
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            resume_url = self._configured_resume_url() if resume_existing else None
            temporary = self._manifest_path.with_suffix(".tmp")
            temporary.write_text(json.dumps({
                "project_id": project_id,
                "images": [str(path.resolve()) for path in images],
                "checkpoint_path": str(checkpoint_path.resolve()),
                "resume_url": resume_url,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self._manifest_path)
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            launcher_log = self.directory / "logs" / "launcher.log"
            launcher_log.parent.mkdir(parents=True, exist_ok=True)
            # Captura erros antes de main.py configurar o logger, como um
            # perfil Firefox ainda bloqueado ou uma configuração inválida.
            with launcher_log.open("ab") as output:
                self._process = subprocess.Popen(
                    [str(self._python), "-m", "automation.main", "--manifest", str(self._manifest_path)], cwd=self.root,
                    stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                    creationflags=flags,
                )
            self._started_at = time.time()
            self._last_return_code = None
            return self.status()

    def stop(self) -> dict[str, object]:
        with self._lock:
            self._reap()
            process = self._process
            if process is None:
                return self.status()
            if os.name == "nt":
                subprocess.run(["taskkill", "/pid", str(process.pid), "/t", "/f"], check=False, capture_output=True, timeout=15)
            else:
                process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
            self._last_return_code = process.poll()
            self._process = None
            return self.status()

    def open_log(self) -> dict[str, str]:
        return self._open(self.directory / "logs" / "automation.log")

    @staticmethod
    def _open(path: Path) -> dict[str, str]:
        if not path.exists():
            raise FileNotFoundError(f"Caminho não encontrado: {path}")
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return {"path": str(path)}
