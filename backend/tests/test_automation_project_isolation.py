import json
from pathlib import Path

from backend.src.automation_runner import AutomationRunner


class _RunningProcess:
    pid = 4321

    def poll(self):
        return None


def _runner_workspace(tmp_path: Path) -> AutomationRunner:
    automation = tmp_path / "automation"
    (automation / ".venv" / "Scripts").mkdir(parents=True)
    (automation / "main.py").write_text("# test launcher", encoding="utf-8")
    (automation / ".env").write_text(
        "PLATFORM_URL=https://vibes.ai\nRESUME_URL=https://vibes.ai/projects/old/content/item\n",
        encoding="utf-8",
    )
    (automation / ".venv" / "Scripts" / "python.exe").write_bytes(b"")
    image_dir = tmp_path / "assets" / "images"
    image_dir.mkdir(parents=True)
    (image_dir / "cena_01.png").write_bytes(b"image")
    return AutomationRunner(tmp_path)


def test_new_project_ignores_global_resume_url_and_uses_its_own_checkpoint(tmp_path, monkeypatch):
    runner = _runner_workspace(tmp_path)
    monkeypatch.setattr("backend.src.automation_runner.subprocess.Popen", lambda *args, **kwargs: _RunningProcess())

    runner.start(["cena_01.png"], project_id="new-project")
    manifest = json.loads((tmp_path / "automation" / "state" / "input_manifest.json").read_text(encoding="utf-8"))

    assert manifest["project_id"] == "new-project"
    assert manifest["resume_url"] is None
    assert Path(manifest["checkpoint_path"]).name == "new-project.json"
    assert Path(manifest["checkpoint_path"]).parent.name == "projects"


def test_explicit_resume_is_the_only_path_that_copies_resume_url_into_manifest(tmp_path, monkeypatch):
    runner = _runner_workspace(tmp_path)
    monkeypatch.setattr("backend.src.automation_runner.subprocess.Popen", lambda *args, **kwargs: _RunningProcess())

    runner.start(["cena_01.png"], project_id="project-a", resume_existing=True)
    manifest = json.loads((tmp_path / "automation" / "state" / "input_manifest.json").read_text(encoding="utf-8"))

    assert manifest["resume_url"] == "https://vibes.ai/projects/old/content/item"


def test_project_scoped_checkpoint_does_not_read_legacy_global_checkpoint(tmp_path):
    runner = _runner_workspace(tmp_path)
    legacy_checkpoint = tmp_path / "automation" / "state" / "checkpoint.json"
    legacy_checkpoint.parent.mkdir(parents=True, exist_ok=True)
    image = (tmp_path / "assets" / "images" / "cena_01.png").resolve()
    signature = f"{image}:{image.stat().st_size}:{image.stat().st_mtime_ns}"
    legacy_checkpoint.write_text(json.dumps({"images": {str(image): {"status": "success", "source_signature": signature}}}), encoding="utf-8")
    manifest_path = tmp_path / "automation" / "state" / "input_manifest.json"
    scoped_checkpoint = tmp_path / "automation" / "state" / "projects" / "new-project.json"
    manifest_path.write_text(json.dumps({"project_id": "new-project", "images": [str(image)], "checkpoint_path": str(scoped_checkpoint)}), encoding="utf-8")

    assert runner._completed_count() == (0, 1)
