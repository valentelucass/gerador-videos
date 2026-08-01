"""Busca e curadoria local de B-roll horizontal do Pexels."""

from __future__ import annotations

import json
import os
import re
import subprocess
from functools import lru_cache
from html import unescape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from .config import FFPROBE, VIDEO_DIR

PEXELS_SEARCH_URL = "https://api.pexels.com/v1/videos/search"
PEXELS_VIDEO_URL = "https://api.pexels.com/videos/videos"
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


def _request_video(video_id: int) -> dict[str, object]:
    """Resolve o ID escolhido diretamente, sem depender da ordem da busca."""
    request = Request(f"{PEXELS_VIDEO_URL}/{video_id}", headers={"Authorization": _api_key(), "User-Agent": "SynthReel/1.0"})
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise PexelsError("O Pexels recusou a chave configurada. Verifique API_KEY_PEXELS no .env.") from exc
        if exc.code == 404:
            raise PexelsError("O vídeo escolhido não está mais disponível no Pexels. Busque novas opções para esta cena.") from exc
        raise PexelsError(f"Não foi possível consultar o vídeo escolhido no Pexels (HTTP {exc.code}).") from exc
    except (URLError, TimeoutError) as exc:
        raise PexelsError("Não foi possível conectar ao Pexels agora. Tente novamente.") from exc
    if not isinstance(payload, dict):
        raise PexelsError("O Pexels retornou um vídeo inválido.")
    return payload


def _best_file(video: dict[str, object], target_width: int) -> dict[str, object] | None:
    files = video.get("video_files")
    if not isinstance(files, list):
        return None
    candidates = [item for item in files if isinstance(item, dict) and isinstance(item.get("link"), str) and item.get("file_type") == "video/mp4"]
    if not candidates:
        return None
    # A prévia não precisa transportar o mesmo arquivo do render final. Para a
    # curadoria escolhemos perto de 960 px; para download, perto de 1920 px.
    return min(candidates, key=lambda item: (
        0 if int(item.get("width") or 0) >= 854 and int(item.get("height") or 0) >= 480 else 1,
        abs(int(item.get("width") or 0) - target_width),
        abs(int(item.get("height") or 0) - round(target_width * 9 / 16)),
    ))


def _candidate_from_raw(raw: dict[str, object]) -> dict[str, object] | None:
    preview_media = _best_file(raw, 960)
    download_media = _best_file(raw, 1920)
    if preview_media is None or download_media is None:
        return None
    width, height = int(raw.get("width") or 0), int(raw.get("height") or 0)
    if not width or not height or width < height:
        return None
    video_id = raw.get("id")
    preview_link = preview_media.get("link")
    download_link = download_media.get("link")
    if not isinstance(video_id, int) or not isinstance(preview_link, str) or not isinstance(download_link, str):
        return None
    return {
        "id": video_id,
        "preview_url": preview_link,
        "download_url": download_link,
        "thumbnail": raw.get("image"),
        "width": int(preview_media.get("width") or width),
        "height": int(preview_media.get("height") or height),
        "duration": raw.get("duration"),
        "creator": raw.get("user", {}).get("name") if isinstance(raw.get("user"), dict) else None,
        "pexels_url": raw.get("url"),
    }


def search_videos(query: str, per_page: int = 4) -> list[dict[str, object]]:
    cleaned = " ".join(query.split())
    if len(cleaned) < 3:
        raise PexelsError("Descreva o B-roll com pelo menos 3 caracteres em inglês.")
    payload = _request_json(cleaned, per_page)
    return [
        candidate
        for raw in payload.get("videos", [])
        if isinstance(raw, dict)
        if (candidate := _candidate_from_raw(raw)) is not None
    ]


def download_selected_video(query: str, video_id: int, destination_name: str) -> dict[str, object]:
    candidate = _candidate_from_raw(_request_video(video_id))
    if candidate is None:
        raise PexelsError("O vídeo escolhido não possui um MP4 horizontal válido. Busque novas opções para esta cena.")
    url = str(candidate["download_url"])
    host = (urlparse(url).hostname or "").lower()
    if not host.endswith("pexels.com"):
        raise PexelsError("O Pexels retornou uma origem de vídeo não reconhecida.")
    target = VIDEO_DIR / Path(destination_name).name
    temporary = target.with_name(f".{target.name}.download")
    try:
        # O CDN do Pexels ocasionalmente fecha uma conexão isolada. Repetimos
        # o mesmo URL selecionado, sem trocar a escolha do operador.
        download_error: Exception | None = None
        for attempt in range(3):
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
                download_error = None
                break
            except PexelsError:
                raise
            except (OSError, URLError, HTTPError, TimeoutError) as exc:
                download_error = exc
                temporary.unlink(missing_ok=True)
                if attempt == 2:
                    raise
        if download_error is not None:
            raise download_error
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


TRANSLATION_CHUNK_SIZE = 450
GOOGLE_TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
MYMEMORY_TRANSLATE_URL = "https://api.mymemory.translated.net/get"


def _translation_chunks(text: str, maximum: int = TRANSLATION_CHUNK_SIZE) -> list[str]:
    """Divide texto longo em limites seguros, preservando palavras e frases."""
    cleaned = " ".join(text.split())
    if not cleaned:
        return []

    chunks: list[str] = []
    current = ""
    # Primeiro preservamos frases inteiras. Uma frase excepcionalmente longa é
    # então dividida somente entre palavras, nunca no meio de um termo.
    sentences = re.split(r"(?<=[.!?…])\s+", cleaned)
    for sentence in sentences:
        words = sentence.split()
        if not words:
            continue
        parts: list[str] = []
        part = ""
        for word in words:
            candidate = f"{part} {word}".strip()
            if part and len(candidate) > maximum:
                parts.append(part)
                part = word
            else:
                part = candidate
        if part:
            parts.append(part)
        for part in parts:
            candidate = f"{current} {part}".strip()
            if current and len(candidate) > maximum:
                chunks.append(current)
                current = part
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


@lru_cache(maxsize=4_096)
def _translate_chunk_to_portuguese(text: str, source: str) -> str:
    """Traduz um trecho curto, com fallback para o provedor secundário."""
    google_params = urlencode({"client": "gtx", "sl": source, "tl": "pt", "dt": "t", "q": text})
    try:
        with urlopen(f"{GOOGLE_TRANSLATE_URL}?{google_params}", timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], list):
            raise ValueError("resposta principal de tradução inválida")
        translated = "".join(
            part[0]
            for part in payload[0]
            if isinstance(part, list) and part and isinstance(part[0], str)
        ).strip()
        if translated:
            return unescape(translated)
    except Exception:
        # O provedor principal pode impor limitação transitória. Neste caso,
        # tenta-se o serviço secundário antes de informar indisponibilidade.
        pass

    fallback_params = urlencode({"q": text, "langpair": f"{source}|pt"})
    try:
        with urlopen(f"{MYMEMORY_TRANSLATE_URL}?{fallback_params}", timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("responseStatus") not in {None, 200}:
            raise ValueError("resposta secundária de tradução inválida")
        translated = payload.get("responseData", {}).get("translatedText")
        if not isinstance(translated, str) or not translated.strip():
            raise ValueError("resposta secundária sem tradução")
        return unescape(translated).strip()
    except Exception as exc:
        raise PexelsError("A tradução não está disponível agora. O texto original continua visível.") from exc


def translate_to_portuguese(text: str, source_language: str) -> str:
    """Traduz para revisão humana, aceitando blocos narrativos longos."""
    if source_language.lower().startswith("pt"):
        return text
    source = source_language.split("-", 1)[0].lower()
    chunks = _translation_chunks(text)
    if not chunks:
        return text
    return " ".join(_translate_chunk_to_portuguese(chunk, source) for chunk in chunks)
