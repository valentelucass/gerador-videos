"""Isolated diagnosis for the horizontal Pexels -> Grid 1x3 path."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.src.core.editor_ffmpeg import FFmpegEngine
from backend.src.core.pexels_fetcher import PexelsFetcher
from backend.src.core.pipeline import VideoPipeline


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.status_code = 200
        self.ok = True
        self.text = json.dumps(payload)
        self.headers = {"X-Ratelimit-Remaining": "mock"}

    def json(self) -> dict[str, Any]:
        return self.payload


class FakePexelsSession:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, headers: dict[str, str], params: dict[str, Any], timeout: int) -> FakeResponse:
        self.calls.append({"url": url, "params": dict(params)})
        orientation = params.get("orientation")

        if "videos/search" in url and orientation == "portrait":
            return FakeResponse({"videos": []})

        if "videos/search" in url and orientation == "landscape":
            return FakeResponse(
                {
                    "videos": [
                        {
                            "id": 987654321,
                            "width": 1920,
                            "height": 1080,
                            "url": "https://pexels.example/video",
                            "user": {"name": "Mock Author"},
                            "video_files": [
                                {
                                    "file_type": "video/mp4",
                                    "link": "https://pexels.example/video-1920x1080.mp4",
                                    "width": 1920,
                                    "height": 1080,
                                }
                            ],
                        }
                    ]
                }
            )

        return FakeResponse({"photos": []})


class MockDownloadPexelsFetcher(PexelsFetcher):
    def _download_file(self, download_url: str, output_path: Path) -> None:
        output_path.write_bytes(b"mock horizontal mp4")


class SpyEditor:
    width = 1080
    height = 1920

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def cortar_midia(self, input_path: str | Path, output_path: str | Path, start_time: float, duration: float) -> Path:
        output = Path(output_path)
        output.write_bytes(b"cut")
        self.calls.append(("cortar_midia", str(input_path), str(output_path)))
        return output

    def aplicar_grid_1x3(self, input_path: str | Path, output_path: str | Path) -> Path:
        output = Path(output_path)
        output.write_bytes(b"grid")
        self.calls.append(("aplicar_grid_1x3", str(input_path), str(output_path)))
        return output

    def aplicar_fullscreen_9x16(self, input_path: str | Path, output_path: str | Path) -> Path:
        output = Path(output_path)
        output.write_bytes(b"fullscreen")
        self.calls.append(("aplicar_fullscreen_9x16", str(input_path), str(output_path)))
        return output

    def aplicar_ken_burns(self, input_path: str | Path, output_path: str | Path, duration: float) -> Path:
        output = Path(output_path)
        output.write_bytes(b"kenburns")
        self.calls.append(("aplicar_ken_burns", str(input_path), str(output_path)))
        return output

    def ajustar_duracao_video(self, input_path: str | Path, output_path: str | Path, duration: float) -> Path:
        output = Path(output_path)
        output.write_bytes(b"adjusted")
        self.calls.append(("ajustar_duracao_video", str(input_path), str(output_path)))
        return output


class CaptureGridFilterEngine(FFmpegEngine):
    def __init__(self) -> None:
        super().__init__(width=1080, height=1920, fps=25)
        self.captured_args: list[str] = []

    def _run_ffmpeg(self, args, step_name):  # type: ignore[no-untyped-def]
        self.captured_args = list(args)
        return None


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        fake_session = FakePexelsSession()
        fetcher = MockDownloadPexelsFetcher(api_key="mock-key", session=fake_session)
        media = fetcher.obter_midia_para_cena(
            query="ancient ruins",
            midias_usadas=set(),
            storage_path=tmp_dir,
        )

        editor = SpyEditor()
        pipeline = VideoPipeline(editor=editor)  # type: ignore[arg-type]
        pipeline.temp_dir = tmp_dir
        pipeline.output_dir = tmp_dir / "render"
        pipeline.output_dir.mkdir(exist_ok=True)
        pipeline._renderizar_cena(
            indice=1,
            midia=media,
            cena={"duracao": 2.5},
        )

        capture_engine = CaptureGridFilterEngine()
        horizontal_input = tmp_dir / "horizontal.mp4"
        horizontal_input.write_bytes(b"fake")
        capture_engine.aplicar_grid_1x3(horizontal_input, tmp_dir / "grid.mp4")
        filter_complex = capture_engine.captured_args[
            capture_engine.captured_args.index("-filter_complex") + 1
        ]

    print("PEXELS_CALLS")
    print(json.dumps(fake_session.calls, indent=2, ensure_ascii=False))
    print("\nMEDIA_RETURNED")
    print(json.dumps(media, indent=2, ensure_ascii=False))
    print("\nPIPELINE_EDITOR_CALLS")
    print(json.dumps(editor.calls, indent=2, ensure_ascii=False))
    print("\nGRID_FILTER_COMPLEX")
    print(filter_complex)

    print("\nASSERTIONS")
    print(f"orientacao == landscape: {media.get('orientacao') == 'landscape'}")
    print(f"precisa_de_grid is True: {media.get('precisa_de_grid') is True}")
    print(
        "pipeline called aplicar_grid_1x3: "
        f"{any(call[0] == 'aplicar_grid_1x3' for call in editor.calls)}"
    )
    print(
        "pipeline avoided fullscreen: "
        f"{not any(call[0] == 'aplicar_fullscreen_9x16' for call in editor.calls)}"
    )
    print(f"filter has split=3: {'split=3' in filter_complex}")
    print(f"filter has boxblur: {'boxblur=' in filter_complex}")
    print(f"filter has vstack=inputs=3: {'vstack=inputs=3' in filter_complex}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
