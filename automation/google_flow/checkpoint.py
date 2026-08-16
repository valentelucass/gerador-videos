"""Checkpoint atômico da esteira Flow, independente do checkpoint Vibes."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class FlowCheckpoint:
    def __init__(self, path: Path, *, source: Path, total: int) -> None:
        self.path = path
        self.data = self._read(source, total)

    def _read(self, source: Path, total: int) -> dict:
        if not self.path.exists():
            return {"source_txt": str(source.resolve()), "total_scenes": total, "scenes": {}}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("source_txt") != str(source.resolve()):
            raise ValueError("Este checkpoint pertence a outro TXT do Flow; escolha outro --state-dir.")
        return payload

    def entry(self, scene_id: str) -> dict:
        return dict(self.data.setdefault("scenes", {}).get(scene_id, {}))

    def status(self, scene_id: str) -> str | None:
        return self.entry(scene_id).get("status")

    def completed(self, scene_id: str) -> bool:
        return self.status(scene_id) == "image_deleted"

    def update(self, scene_id: str, status: str, **details: object) -> None:
        previous = self.data.setdefault("scenes", {}).get(scene_id, {})
        self.data["scenes"][scene_id] = {
            **previous, "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(), **details,
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)
