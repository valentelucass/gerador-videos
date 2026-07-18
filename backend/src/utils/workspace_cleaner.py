"""Workspace cleanup helpers for SynthReel generated artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

try:
    from backend.src.config.settings import OUTPUT_DIR, TEMP_DIR, WORKSPACE_DIR
    from backend.src.utils.logger import get_logger
except ModuleNotFoundError:
    import sys

    ROOT_DIR = Path(__file__).resolve().parents[2]
    if str(ROOT_DIR) not in sys.path:
        sys.path.append(str(ROOT_DIR))
    from backend.src.config.settings import OUTPUT_DIR, TEMP_DIR, WORKSPACE_DIR
    from backend.src.utils.logger import get_logger


class WorkspaceCleaner:
    """Deletes temporary render artifacts while preserving final deliverables."""

    def __init__(
        self,
        workspace_dir: str | Path = WORKSPACE_DIR,
        temp_dir: str | Path = TEMP_DIR,
        output_dir: str | Path = OUTPUT_DIR,
    ) -> None:
        self.workspace_dir = Path(workspace_dir).resolve()
        self.temp_dir = Path(temp_dir).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.logger = get_logger(__name__)

    def clean_run(
        self,
        temp_dir: str | Path,
        output_dir: str | Path,
        keep_paths: Iterable[str | Path],
    ) -> dict[str, Any]:
        """Cleans a single pipeline run, keeping only explicit final paths."""

        keep = {Path(path).resolve() for path in keep_paths}
        summary = self._summary("clean_run")
        run_temp = Path(temp_dir).resolve()
        run_output = Path(output_dir).resolve()

        self._assert_inside(run_temp, self.temp_dir)
        self._assert_inside(run_output, self.output_dir)

        if run_temp.exists():
            self._delete_tree_contents(run_temp, keep, summary)
            self._remove_empty_dirs(run_temp, summary, include_root=True)

        if run_output.exists():
            self._delete_tree_contents(run_output, keep, summary)
            self._remove_empty_dirs(run_output, summary, include_root=False)

        summary["kept"] = [str(path) for path in sorted(keep, key=str)]
        self.logger.info(
            "Cleanup: removidos %s arquivos e %s diretorios",
            summary["removed_files"],
            summary["removed_dirs"],
        )
        return summary

    def clean_existing_artifacts(self) -> dict[str, Any]:
        """Remove old run data while preserving every final MP4 in ``output``.

        Prepared lots are a HITL staging area, not an archive.  Once the user
        chooses a full cleanup they can be discarded along with previews and
        manifests; persistent assets and voice references are deliberately
        outside this list.
        """

        summary = self._summary("clean_existing_artifacts")

        if self.temp_dir.exists():
            self._delete_tree_contents(self.temp_dir, keep=set(), summary=summary)
            self._remove_empty_dirs(self.temp_dir, summary, include_root=False)

        keep_outputs = self._discover_final_outputs()
        if self.output_dir.exists():
            self._delete_tree_contents(self.output_dir, keep_outputs, summary)
            self._remove_empty_dirs(self.output_dir, summary, include_root=False)

        for staging_dir in (
            self.workspace_dir / "lotes_preparados",
            self.workspace_dir / "lotes_horizontais",
            self.workspace_dir / "orchestrator_previews",
            self.workspace_dir / "orchestrator_manifests",
        ):
            if staging_dir.exists():
                self._delete_tree_contents(staging_dir, keep=set(), summary=summary)
                self._remove_empty_dirs(staging_dir, summary, include_root=False)

        summary["kept"] = [str(path) for path in sorted(keep_outputs, key=str)]
        self.logger.info(
            "Cleanup manual: removidos %s arquivos e %s diretorios",
            summary["removed_files"],
            summary["removed_dirs"],
        )
        return summary

    def _discover_final_outputs(self) -> set[Path]:
        if not self.output_dir.exists():
            return set()

        finals: set[Path] = set()
        # Output is the sole delivery archive.  Naming conventions differ
        # between vertical and horizontal renderers, so every non-empty MP4
        # stored there is a final deliverable.
        for path in self.output_dir.rglob("*.mp4"):
            if path.is_file() and path.stat().st_size > 0:
                finals.add(path.resolve())
        return finals

    def _delete_tree_contents(
        self,
        root: Path,
        keep: set[Path],
        summary: dict[str, Any],
    ) -> None:
        self._assert_inside(root, self.workspace_dir)
        if not root.exists():
            return

        for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            resolved = path.resolve()
            if resolved in keep or self._has_kept_descendant(resolved, keep):
                continue

            try:
                if path.is_file() or path.is_symlink():
                    self._delete_file(path, summary)
                elif path.is_dir():
                    self._delete_empty_dir(path, summary)
            except OSError as exc:
                summary["errors"].append({"path": str(resolved), "error": str(exc)})

    def _remove_empty_dirs(
        self,
        root: Path,
        summary: dict[str, Any],
        include_root: bool,
    ) -> None:
        self._assert_inside(root, self.workspace_dir)
        if not root.exists():
            return

        dirs = [path for path in root.rglob("*") if path.is_dir()]
        dirs.sort(key=lambda item: len(item.parts), reverse=True)
        if include_root:
            dirs.append(root)

        for path in dirs:
            try:
                self._delete_empty_dir(path, summary)
            except OSError as exc:
                summary["errors"].append({"path": str(path.resolve()), "error": str(exc)})

    def _delete_file(self, path: Path, summary: dict[str, Any]) -> None:
        resolved = path.resolve()
        self._assert_inside(resolved, self.workspace_dir)
        size = path.stat().st_size if path.exists() else 0
        path.unlink()
        summary["removed_files"] += 1
        summary["removed_bytes"] += size
        self._add_removed_sample(summary, resolved)

    def _delete_empty_dir(self, path: Path, summary: dict[str, Any]) -> None:
        resolved = path.resolve()
        self._assert_inside(resolved, self.workspace_dir)
        if not path.exists() or not path.is_dir():
            return
        try:
            path.rmdir()
        except OSError:
            return
        summary["removed_dirs"] += 1
        self._add_removed_sample(summary, resolved)

    def _assert_inside(self, path: Path, parent: Path) -> None:
        resolved = Path(path).resolve()
        resolved_parent = Path(parent).resolve()
        if resolved == resolved_parent:
            return
        try:
            resolved.relative_to(resolved_parent)
        except ValueError as exc:
            raise ValueError(f"Cleanup recusou caminho fora do workspace: {resolved}") from exc

    @staticmethod
    def _has_kept_descendant(path: Path, keep: set[Path]) -> bool:
        if not path.is_dir():
            return False
        for kept_path in keep:
            try:
                kept_path.relative_to(path)
                return True
            except ValueError:
                continue
        return False

    @staticmethod
    def _summary(mode: str) -> dict[str, Any]:
        return {
            "mode": mode,
            "removed_files": 0,
            "removed_dirs": 0,
            "removed_bytes": 0,
            "removed_samples": [],
            "kept": [],
            "errors": [],
        }

    @staticmethod
    def _add_removed_sample(summary: dict[str, Any], path: Path) -> None:
        samples = summary.setdefault("removed_samples", [])
        if len(samples) < 20:
            samples.append(str(path))
