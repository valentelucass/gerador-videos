"""Registro estruturado para reconstruir exatamente a execução do robô."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditTrail:
    def __init__(self, logs_dir: Path, state_dir: Path) -> None:
        self.events_path = logs_dir / "events.jsonl"
        self.snapshot_path = state_dir / "run_state.json"

    def record(self, event: str, **details: Any) -> None:
        payload = {"at": datetime.now(timezone.utc).isoformat(), "event": event, **details}
        try:
            with self.events_path.open("a", encoding="utf-8") as output:
                output.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
            try:
                current = json.loads(self.snapshot_path.read_text(encoding="utf-8")) if self.snapshot_path.is_file() else {}
            except (OSError, json.JSONDecodeError):
                current = {}
            # Eventos de ação não repetem estado/imagem. Mantemos o último
            # contexto conhecido para que o painel sempre mostre onde parou.
            snapshot = {**current, **payload}
            temporary = self.snapshot_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            temporary.replace(self.snapshot_path)
        except OSError:
            # Diagnóstico não pode interromper uma recuperação que ainda pode
            # concluir a imagem. O log padrão continua sendo a contingência.
            pass
