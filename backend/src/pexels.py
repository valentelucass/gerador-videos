"""Busca e curadoria local de B-roll horizontal do Pexels."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from .config import FFPROBE, VIDEO_DIR

PEXELS_SEARCH_URL = "https://api.pexels.com/v1/videos/search"
MAX_DOWNLOAD_BYTES = 750 * 1024 * 1024


class PexelsError(RuntimeError):
    """Erro seguro para exibir ao operador sem revelar a chave."""


def _api_key() -> str:
    key = os.getenv("API_KEY_PEXELS", "").strip()
    if not key:
        raise PexelsError("A chave API_KEY_PEXELS não foi encontrada no arquivo .env.")
    return key


def _request_json(query: str, per_page: int = 4) -> dict[str, object]:
    params = urlencode({"query": query, "orientation": "landscape", "size": "medium", "per_page": per_page})
    request = Request(f"{PEXELS_SEARCH_URL}?{params}", headers={"Authorization": _api_key(), "User-Agent": "SynthReel/1.0"})
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise PexelsError("O Pexels recusou a chave configurada. Verifique API_KEY_PEXELS no .env.") from exc
        raise PexelsError(f"A busca no Pexels falhou (HTTP {exc.code}).") from exc
    except (URLError, TimeoutError) as exc:
        raise PexelsError("Não foi possível conectar ao Pexels agora. Tente novamente.") from exc


def _best_file(video: dict[str, object]) -> dict[str, object] | None:
    files = video.get("video_files")
    if not isinstance(files, list):
        return None
    candidates = [item for item in files if isinstance(item, dict) and isinstance(item.get("link"), str) and item.get("file_type") == "video/mp4"]
    if not candidates:
        return None
    # Prioriza Full HD horizontal sem baixar, sem necessidade, um arquivo 4K.
    return min(candidates, key=lambda item: (
        0 if int(item.get("width") or 0) >= 1280 and int(item.get("height") or 0) >= 720 else 1,
        abs(int(item.get("width") or 0) - 1920) + abs(int(item.get("height") or 0) - 1080),
    ))


def search_videos(query: str, per_page: int = 4) -> list[dict[str, object]]:
    cleaned = " ".join(query.split())
    if len(cleaned) < 3:
        raise PexelsError("Descreva o B-roll com pelo menos 3 caracteres em inglês.")
    payload = _request_json(cleaned, per_page)
    result: list[dict[str, object]] = []
    for raw in payload.get("videos", []):
        if not isinstance(raw, dict):
            continue
        media = _best_file(raw)
        if media is None:
            continue
        width, height = int(raw.get("width") or 0), int(raw.get("height") or 0)
        if not width or not height or width < height:
            continue
        video_id = raw.get("id")
        link = media.get("link")
        if not isinstance(video_id, int) or not isinstance(link, str):
            continue
        result.append({
            "id": video_id,
            "preview_url": link,
            "thumbnail": raw.get("image"),
            "width": width,
            "height": height,
            "duration": raw.get("duration"),
            "creator": raw.get("user", {}).get("name") if isinstance(raw.get("user"), dict) else None,
            "pexels_url": raw.get("url"),
        })
    return result


def download_selected_video(query: str, video_id: int, destination_name: str) -> dict[str, object]:
    candidates = search_videos(query, per_page=12)
    candidate = next((item for item in candidates if item["id"] == video_id), None)
    if candidate is None:
        raise PexelsError("A opção escolhida não pertence mais à busca atual. Pesquise novamente.")
    url = str(candidate["preview_url"])
    host = (urlparse(url).hostname or "").lower()
    if not host.endswith("pexels.com"):
        raise PexelsError("O Pexels retornou uma origem de vídeo não reconhecida.")
    target = VIDEO_DIR / Path(destination_name).name
    temporary = target.with_name(f".{target.name}.download")
    try:
        request = Request(url, headers={"User-Agent": "SynthReel/1.0"})
        with urlopen(request, timeout=90) as response, temporary.open("wb") as output:
            length = int(response.headers.get("Content-Length") or 0)
            if length and length > MAX_DOWNLOAD_BYTES:
                raise PexelsError("O B-roll selecionado é grande demais para a curadoria local (limite de 750 MiB).")
            copied = 0
            while chunk := response.read(1024 * 1024):
                copied += len(chunk)
                if copied > MAX_DOWNLOAD_BYTES:
                    raise PexelsError("O B-roll selecionado excedeu o limite de 750 MiB.")
                output.write(chunk)
        if temporary.stat().st_size == 0:
            raise PexelsError("O Pexels retornou um arquivo de vídeo vazio.")
        probe = subprocess.run(
            [str(FFPROBE), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", str(temporary)],
            capture_output=True,
            text=True,
            check=False,
        )
        stream = json.loads(probe.stdout).get("streams", [{}])[0] if probe.returncode == 0 else {}
        width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
        if width < height or not width:
            raise PexelsError("O arquivo baixado não é horizontal de verdade e foi recusado.")
        temporary.replace(target)
    except PexelsError:
        raise
    except (OSError, URLError, HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        raise PexelsError("Não foi possível baixar o vídeo do Pexels.") from exc
    finally:
        temporary.unlink(missing_ok=True)

    manifest_path = VIDEO_DIR / "pexels_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    except json.JSONDecodeError:
        manifest = {}
    manifest[target.name] = {"query": query, **candidate}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"filename": target.name, **candidate}


def translate_to_portuguese(text: str, source_language: str) -> str:
    """Traduz sob demanda para a revisão humana, sem salvar texto em serviço externo."""
    if source_language.lower().startswith("pt"):
        return text
    source = source_language.split("-", 1)[0].lower()
    params = urlencode({"q": text, "langpair": f"{source}|pt"})
    try:
        with urlopen(f"https://api.mymemory.translated.net/get?{params}", timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        translated = payload.get("responseData", {}).get("translatedText")
        if not isinstance(translated, str) or not translated.strip():
            raise ValueError("resposta sem tradução")
        return translated.strip()
    except Exception as exc:
        raise PexelsError("A tradução não está disponível agora. O texto original continua visível.") from exc
