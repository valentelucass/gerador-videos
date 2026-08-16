"""Executor do Google Flow, separado do robô Vibes e do renderizador."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from urllib.parse import urlparse
from urllib.error import URLError
from urllib.request import urlopen
from pathlib import Path
from threading import RLock

from automation.google_flow.config import FlowSettings


class FlowAutomationRunner:
    FLOW_HOME_URL = "https://labs.google/fx/pt/tools/flow"

    def __init__(self, project_root: Path) -> None:
        self.root = project_root
        self.automation_dir = project_root / "automation"
        self.workspace = project_root / "workspace" / "flow_automation"
        self._process: subprocess.Popen[bytes] | None = None
        self._job_dir: Path | None = None
        self._started_at: float | None = None
        self._last_return_code: int | None = None
        self._chrome_process: subprocess.Popen[bytes] | None = None
        self._lock = RLock()

    @property
    def _python(self) -> Path:
        return self.automation_dir / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    def _reap(self) -> None:
        if self._process is not None and (code := self._process.poll()) is not None:
            self._last_return_code = code
            self._process = None

    def _checkpoint(self) -> dict:
        if self._job_dir is None:
            return {}
        path = self._job_dir / "flow_checkpoint.json"
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _scene_contract(contents: bytes) -> tuple[str, ...]:
        """Identidade estável do roteiro, independente do cabeçalho/exportador."""
        text = contents.decode("utf-8", errors="replace")
        scenes = re.findall(r"\[\[SCENE\s+\d+\]\](.*?)\[\[/SCENE\]\]", text, flags=re.IGNORECASE | re.DOTALL)
        return tuple(re.sub(r"\s+", " ", scene).strip() for scene in scenes)

    def _matching_job(self, contents: bytes) -> Path | None:
        """Retoma o checkpoint mais avançado para o mesmo conjunto de cenas."""
        contract = self._scene_contract(contents)
        if not contract:
            return None
        candidates = sorted(
            (path for path in self.workspace.iterdir() if path.is_dir() and (path / "source.txt").is_file()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        ) if self.workspace.is_dir() else []
        matches: list[tuple[int, int, float, Path]] = []
        for candidate in candidates:
            try:
                if self._scene_contract((candidate / "source.txt").read_bytes()) != contract:
                    continue
                checkpoint_path = candidate / "flow_checkpoint.json"
                checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8")) if checkpoint_path.is_file() else {}
                entries = checkpoint.get("scenes", {}).values() if isinstance(checkpoint, dict) else []
                completed = sum(1 for entry in entries if isinstance(entry, dict) and entry.get("status") == "batch_complete")
                sent = sum(1 for entry in entries if isinstance(entry, dict) and entry.get("status") == "sent")
                matches.append((completed, sent, candidate.stat().st_mtime, candidate))
            except (OSError, json.JSONDecodeError):
                continue
        return max(matches, default=None, key=lambda item: item[:3])[3] if matches else None

    @staticmethod
    def _assert_cdp_available(settings: FlowSettings) -> None:
        """Confirma que a janela já aberta permite automação, sem criar outra."""
        endpoint = f"{settings.cdp_url.rstrip('/')}/json/version"
        try:
            with urlopen(endpoint, timeout=2) as response:
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status}")
        except (OSError, URLError, RuntimeError) as exc:
            raise ValueError(
                "Não foi possível acessar o navegador já aberto para o Google Flow. "
                "Abra o Flow no Chrome iniciado com --remote-debugging-port=9222; "
                "a automação não abrirá outro navegador nem outra aba."
            ) from exc

    def _chrome_executable(self) -> Path:
        candidates = [
            Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        ]
        executable = next((item for item in candidates if item.is_file()), None)
        if executable is None:
            raise FileNotFoundError("Google Chrome não foi encontrado. Instale-o ou abra o Flow em um Chrome disponível.")
        return executable

    def prepare_browser(self, target_url: str | None = None) -> dict[str, object]:
        """Abre o início do Flow; a escolha do projeto/chat é sempre do usuário."""
        with self._lock:
            settings = FlowSettings.load(self.workspace)
            if settings.browser == "firefox":
                return self.status() | {
                    "message": "Clique em Ativar Flow: o Firefox autenticado será aberto no início do Flow e aguardará você escolher o projeto/chat."
                }
            try:
                self._assert_cdp_available(settings)
                return self.status() | {"message": "Chrome Flow já está pronto. Abra o projeto/chat desejado e depois clique em Ativar Flow."}
            except ValueError:
                pass
            profile = self.workspace / "chrome_flow_profile"
            profile.mkdir(parents=True, exist_ok=True)
            executable = self._chrome_executable()
            self._chrome_process = subprocess.Popen(
                [
                    str(executable),
                    "--remote-debugging-port=9222",
                    f"--user-data-dir={profile}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    target_url or self.FLOW_HOME_URL,
                ],
                cwd=self.root,
            )
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    self._assert_cdp_available(settings)
                    return self.status() | {
                        "message": "Chrome aberto no link informado do Google Flow. Aguarde a página carregar e clique em Ativar Flow novamente."
                    }
                except ValueError:
                    time.sleep(0.25)
            raise RuntimeError("O Chrome foi aberto, mas não disponibilizou a conexão de automação na porta 9222.")
    def status(self) -> dict[str, object]:
        with self._lock:
            self._reap()
            checkpoint = self._checkpoint()
            waiting_for_selection = bool(self._job_dir and (self._job_dir / "awaiting_user_selection.flag").is_file())
            waiting_for_continue = bool(self._job_dir and list(self._job_dir.glob("awaiting_continue_*.flag")))
            scenes = checkpoint.get("scenes", {}) if isinstance(checkpoint, dict) else {}
            entries = list(scenes.values()) if isinstance(scenes, dict) else []
            total = int(checkpoint.get("total_scenes", 0)) if isinstance(checkpoint, dict) else 0
            completed = sum(1 for entry in entries if isinstance(entry, dict) and entry.get("status") in {"batch_complete", "image_deleted"})
            videos = sum(1 for entry in entries if isinstance(entry, dict) and entry.get("status") in {"video_validated", "image_deleted"})
            failed = [scene_id for scene_id, entry in scenes.items() if isinstance(entry, dict) and entry.get("last_error")] if isinstance(scenes, dict) else []
            running = self._process is not None
            if waiting_for_selection:
                message = "Firefox autenticado aberto no Flow. Escolha o projeto/chat desejado e confirme no painel para começar."
            elif waiting_for_continue:
                message = "Grupo de 25 cenas confirmado pelo Flow. Revise o chat e clique em Continuar Flow para liberar as próximas 25."
            elif running:
                message = f"Google Flow em execução: {completed}/{total} cenas concluídas."
            elif self._last_return_code not in (None, 0):
                message = "Google Flow interrompido por erro; confira o log. As cenas concluídas foram preservadas."
            elif total and completed == total:
                message = f"Todos os {total} prompts foram enviados ao chat preparado do Flow."
            elif total and completed:
                message = f"{completed}/{total} prompts enviados ao chat do Flow. Aguarde o agente concluir este sublote antes de enviar o próximo."
            else:
                message = "Selecione um TXT Flow para iniciar a produção em grupos de 25 cenas."
            return {
                "running": running, "pid": self._process.pid if self._process else None,
                "started_at": self._started_at, "last_return_code": self._last_return_code,
                "job_id": self._job_dir.name if self._job_dir else None,
                "total_scenes": total, "completed_scenes": completed, "completed_videos": videos,
                "failed_scenes": failed, "message": message,
                "awaiting_user_selection": waiting_for_selection, "awaiting_continue": waiting_for_continue,
            }

    def resume_info(self, *, filename: str, contents: bytes) -> dict[str, object]:
        """Mostra o checkpoint compatível, sem iniciar nem alterar a automação."""
        if not filename.lower().endswith(".txt"):
            raise ValueError("Envie um arquivo .txt do Google Flow.")
        try:
            contents.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("O TXT do Google Flow deve usar UTF-8.") from exc
        job_dir = self._matching_job(contents)
        if job_dir is None:
            return {"available": False, "job_id": None, "completed_scenes": 0, "total_scenes": 0}
        checkpoint_path = job_dir / "flow_checkpoint.json"
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8")) if checkpoint_path.is_file() else {}
        entries = checkpoint.get("scenes", {}).values() if isinstance(checkpoint, dict) else []
        completed = sum(1 for entry in entries if isinstance(entry, dict) and entry.get("status") == "batch_complete")
        return {
            "available": True, "job_id": job_dir.name, "completed_scenes": completed,
            "total_scenes": int(checkpoint.get("total_scenes", 0)),
        }

    def start(self, *, filename: str, contents: bytes, target_url: str, resume_existing: bool = True) -> dict[str, object]:
        with self._lock:
            self._reap()
            if self._process is not None:
                return self.status()
            if not (self.automation_dir / ".env").is_file():
                raise FileNotFoundError("Crie e configure automation/.env antes de iniciar o Google Flow.")
            if not self._python.is_file():
                raise FileNotFoundError("Instale as dependências em automation/.venv antes de iniciar o Google Flow.")
            # Falhar na própria requisição evita um falso "ativado": sem uma
            # conexão CDP o subprocesso encerraria antes de tocar na aba já aberta.
            if not filename.lower().endswith(".txt"):
                raise ValueError("Envie um arquivo .txt do Google Flow.")
            try:
                text = contents.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("O TXT do Google Flow deve usar UTF-8.") from exc
            if "[[SCENE" not in text.upper() or "ANIMATION:" not in text.upper() or "IMAGE:" not in text.upper():
                raise ValueError("O TXT precisa conter blocos [[SCENE NN]], IMAGE: e ANIMATION:.")
            parsed_target = urlparse(target_url.strip())
            if parsed_target.scheme != "https" or parsed_target.netloc != "labs.google" or "/fx/" not in parsed_target.path:
                raise ValueError("Informe a URL HTTPS exata do projeto/chat no Google Flow (labs.google/fx).")
            settings = FlowSettings.load(self.workspace)
            if settings.browser == "chromium":
                try:
                    self._assert_cdp_available(settings)
                except ValueError:
                    # Primeiro clique: prepara o Chrome no início do Flow e espera a
                    # pessoa escolher o projeto. Não cria job nem envia prompt.
                    self.prepare_browser(target_url=target_url.strip())
                    self._assert_cdp_available(settings)
            job_dir = self._matching_job(contents) if resume_existing else None
            if job_dir is None:
                job_dir = self.workspace / uuid.uuid4().hex
                job_dir.mkdir(parents=True, exist_ok=False)
                (job_dir / "source.txt").write_text(text, encoding="utf-8")
            self._job_dir = job_dir
            checkpoint = self._checkpoint()
            if resume_existing and int(checkpoint.get("total_scenes", 0)) and sum(
                1 for entry in checkpoint.get("scenes", {}).values()
                if isinstance(entry, dict) and entry.get("status") == "sent"
            ) >= int(checkpoint.get("total_scenes", 0)):
                return self.status()
            source = job_dir / "source.txt"
            launcher_log = job_dir / "launcher.log"
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            child_env = os.environ.copy()
            child_env["FLOW_PAGE_URL_CONTAINS"] = target_url.strip().lower()
            child_env["FLOW_PAGE_URL_EXACT"] = "true"
            with launcher_log.open("ab") as output:
                self._process = subprocess.Popen(
                    [
                        str(self._python), "-m", "automation.google_flow.main", "--txt", str(source), "--state-dir", str(job_dir),
                        *( ["--await-user-selection"] if settings.browser == "firefox" else [] ),
                    ],
                    cwd=self.root, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                    creationflags=flags, env=child_env,
                )
            self._started_at = time.time()
            self._last_return_code = None
            return self.status()

    def confirm_user_selection(self) -> dict[str, object]:
        """Libera somente o job Firefox que está aguardando o chat escolhido pela pessoa."""
        with self._lock:
            self._reap()
            if self._process is None or self._job_dir is None:
                raise ValueError("Não há um Firefox Flow aguardando a escolha de projeto/chat.")
            waiting = self._job_dir / "awaiting_user_selection.flag"
            if not waiting.is_file():
                raise ValueError("O Google Flow não está aguardando uma escolha de projeto/chat.")
            (self._job_dir / "flow_ready.flag").write_text("confirmed\n", encoding="utf-8")
            return self.status() | {"message": "Chat confirmado. A automação do Google Flow está iniciando."}

    def continue_after_group(self) -> dict[str, object]:
        """Libera conscientemente o próximo grupo de 25 depois da revisão humana."""
        with self._lock:
            self._reap()
            if self._process is None or self._job_dir is None:
                raise ValueError("Não há uma automação Flow ativa aguardando continuação.")
            if not list(self._job_dir.glob("awaiting_continue_*.flag")):
                raise ValueError("O Flow ainda não concluiu um grupo de 25 para revisão.")
            (self._job_dir / "continue.flag").write_text("confirmed\n", encoding="utf-8")
            return self.status() | {"message": "Próximo grupo de 25 liberado para o Flow."}

    def stop(self) -> dict[str, object]:
        with self._lock:
            self._reap()
            if self._process is None:
                return self.status()
            if os.name == "nt":
                subprocess.run(["taskkill", "/pid", str(self._process.pid), "/t", "/f"], check=False, capture_output=True, timeout=15)
            else:
                self._process.terminate()
            self._last_return_code = self._process.poll()
            self._process = None
            return self.status()

    def open_log(self) -> dict[str, str]:
        if self._job_dir is None:
            raise FileNotFoundError("Ainda não há uma execução Google Flow para abrir o log.")
        path = self._job_dir / "launcher.log"
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return {"path": str(path)}
