"""Checkpoint atômico: uma imagem concluída jamais volta ao estado pendente."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class CheckpointStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._data = self._read()

    def _read(self) -> dict:
        if not self.path.exists():
            return {"images": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def is_complete(self, image: Path) -> bool:
        entry = self._data["images"].get(str(image.resolve()), {})
        return entry.get("status") in {"success", "skipped_video", "failed_final"} and entry.get("source_signature") == self.signature_for(image)

    def status_for(self, image: Path) -> str | None:
        """Retorna o estado atual se ele ainda pertence à mesma versão do arquivo."""
        entry = self._data["images"].get(str(image.resolve()), {})
        return entry.get("status") if entry.get("source_signature") == self.signature_for(image) else None

    def details_for(self, image: Path) -> dict:
        """Metadados válidos da imagem, inclusive contadores recuperáveis."""
        entry = self._data["images"].get(str(image.resolve()), {})
        return dict(entry) if entry.get("source_signature") == self.signature_for(image) else {}

    def is_uploaded(self, image: Path) -> bool:
        """Indica se esta versão física do arquivo já teve upload confirmado."""
        entry = self._data["images"].get(str(image.resolve()), {})
        return entry.get("upload_signature") == self.signature_for(image) and bool(entry.get("project_url"))

    def project_url_for(self, images: list[Path]) -> str | None:
        """Retorna o único projeto Vibes associado aos uploads válidos.

        Se não houver URL ou houver divergência, a automação não presume que
        dois projetos diferentes sejam equivalentes.
        """
        urls = {
            entry["project_url"]
            for image in images
            if self.is_uploaded(image)
            for entry in [self._data["images"].get(str(image.resolve()), {})]
            if entry.get("project_url")
        }
        return urls.pop() if len(urls) == 1 else None

    def mark_uploaded(self, image: Path, project_url: str) -> None:
        """Persiste sucesso do envio antes de esperar a renderização da UI."""
        state = self.status_for(image) if self.is_complete(image) else "uploaded"
        self._write_entry(
            image,
            state,
            upload_signature=self.signature_for(image),
            project_url=project_url,
        )

    def mark_uploaded_batch(self, images: list[Path], project_url: str) -> None:
        """Registra uma retomada já validada com apenas uma gravação atômica."""
        for image in images:
            key = str(image.resolve())
            previous = self._data.setdefault("images", {}).get(key, {})
            state = self.status_for(image) if self.is_complete(image) else "uploaded"
            self._data["images"][key] = {
                **previous,
                "status": state,
                "source_signature": self.signature_for(image),
                "upload_signature": self.signature_for(image),
                "project_url": project_url,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        self._persist()

    def media_id_for(self, image: Path) -> str | None:
        entry = self._data["images"].get(str(image.resolve()), {})
        return entry.get("media_id") if self.is_uploaded(image) else None

    def bind_media_id(self, image: Path, media_id: str) -> None:
        """Associa arquivo local ao ID estável do card retornado pelo Vibes."""
        self._write_entry(image, self._data["images"].get(str(image.resolve()), {}).get("status", "uploaded"), media_id=media_id)

    def clear_project_state(self, images: list[Path]) -> None:
        """Remove vínculos de um projeto Vibes que não existe mais."""
        for image in images:
            self._data.setdefault("images", {}).pop(str(image.resolve()), None)
        self._persist()

    def _persist(self) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    @staticmethod
    def signature_for(image: Path) -> str:
        """Distingue uma imagem substituída de um sucesso antigo com mesmo nome."""
        stat = image.stat()
        return f"{image.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"

    def update(self, image: Path, state: str, **details: object) -> None:
        self._write_entry(image, state, **details)

    def _write_entry(self, image: Path, state: str, **details: object) -> None:
        key = str(image.resolve())
        previous = self._data.setdefault("images", {}).get(key, {})
        self._data["images"][key] = {
            **previous,
            "status": state,
            "source_signature": self.signature_for(image),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **details,
        }
        self._persist()
