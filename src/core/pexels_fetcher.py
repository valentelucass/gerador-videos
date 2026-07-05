"""Pexels media fetcher for SynthReel B-roll selection."""

from __future__ import annotations

import json
import logging
import random
import sys
from pathlib import Path
from typing import Any

import requests

try:
    from src.config.settings import TEMP_DIR, settings
    from src.utils.logger import get_logger
except ModuleNotFoundError:
    ROOT_DIR = Path(__file__).resolve().parents[2]
    if str(ROOT_DIR) not in sys.path:
        sys.path.append(str(ROOT_DIR))
    from src.config.settings import TEMP_DIR, settings
    from src.utils.logger import get_logger


class PexelsFetcherError(RuntimeError):
    """Base error for clean Pexels failures."""


class PexelsAPIError(PexelsFetcherError):
    """Raised when Pexels returns an invalid or failed API response."""


class PexelsNoResultsError(PexelsFetcherError):
    """Raised when no valid media matches the scene constraints."""


class PexelsFetcher:
    """Fetches Pexels B-roll following the business rules in agents.md."""

    VIDEO_SEARCH_URL = "https://api.pexels.com/v1/videos/search"
    PHOTO_SEARCH_URL = "https://api.pexels.com/v1/search"
    DEFAULT_PER_PAGE = 30
    DEFAULT_TIMEOUT = 20
    DEFAULT_DOWNLOAD_TIMEOUT = 90
    MIN_VIDEO_SHORT_SIDE = 1080
    MIN_PHOTO_LONG_SIDE = 1920

    def __init__(
        self,
        api_key: str | None = None,
        logger: logging.Logger | None = None,
        per_page: int = DEFAULT_PER_PAGE,
        timeout: int = DEFAULT_TIMEOUT,
        download_timeout: int = DEFAULT_DOWNLOAD_TIMEOUT,
        min_video_short_side: int = MIN_VIDEO_SHORT_SIDE,
        min_photo_long_side: int = MIN_PHOTO_LONG_SIDE,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.pexels_api_key
        self.logger = logger or get_logger(__name__)
        self.per_page = per_page
        self.timeout = timeout
        self.download_timeout = download_timeout
        self.min_video_short_side = min_video_short_side
        self.min_photo_long_side = min_photo_long_side
        self.session = session or requests.Session()

    def obter_midia_para_cena(
        self,
        query: str,
        midias_usadas: set[str],
        storage_path: str | Path,
    ) -> dict[str, Any]:
        """Searches, selects and downloads the best media for a scene.

        Priority order:
        1. Portrait video not used in the current job.
           A 30% layout-variation draw skips this step and starts on landscape.
        2. Landscape video not used in the current job.
        3. High-resolution photo not used in the current job.
        """

        query = self._normalizar_query(query)
        storage_dir = Path(storage_path)
        storage_dir.mkdir(parents=True, exist_ok=True)
        usadas = {str(media_id) for media_id in midias_usadas}

        self._require_api_key()
        self.logger.info("Pexels: buscando midia para '%s'", query)

        for orientacao in self._ordem_busca_videos():
            videos = self._buscar_videos(query, orientacao)
            video = self._selecionar_video(videos, usadas, orientacao)
            if video is not None:
                return self._baixar_video(video, orientacao, storage_dir)

        for orientacao in ("portrait", None):
            fotos = self._buscar_fotos(query, orientacao)
            foto = self._selecionar_foto(fotos, usadas)
            if foto is not None:
                return self._baixar_foto(foto, orientacao or self._orientacao(foto), storage_dir)

        raise PexelsNoResultsError(f"Nenhuma midia valida encontrada no Pexels para: {query}")

    def _ordem_busca_videos(self) -> tuple[str, ...]:
        if random.random() < 0.30:
            self.logger.info("Pexels: variacao visual ativada; pulando video portrait e buscando landscape.")
            return ("landscape",)
        return ("portrait", "landscape")

    def buscar_midia(self, termo: str, midias_usadas: set[str]) -> dict[str, Any]:
        """Backward-compatible alias for the previous placeholder method."""

        return self.obter_midia_para_cena(termo, midias_usadas, TEMP_DIR)

    def _buscar_videos(self, query: str, orientacao: str) -> list[dict[str, Any]]:
        self.logger.info("Pexels: tentando video %s", orientacao)
        payload = self._get_json(
            self.VIDEO_SEARCH_URL,
            {
                "query": query,
                "orientation": orientacao,
                "size": "medium",
                "per_page": self.per_page,
                "page": 1,
            },
        )
        videos = payload.get("videos", [])
        if not isinstance(videos, list):
            raise PexelsAPIError("Resposta de videos do Pexels veio em formato inesperado.")
        self.logger.info("Pexels: %s videos %s recebidos", len(videos), orientacao)
        return videos

    def _buscar_fotos(self, query: str, orientacao: str | None) -> list[dict[str, Any]]:
        label = orientacao or "sem orientacao fixa"
        self.logger.info("Pexels: tentando foto %s", label)
        params: dict[str, Any] = {
            "query": query,
            "size": "large",
            "per_page": self.per_page,
            "page": 1,
        }
        if orientacao is not None:
            params["orientation"] = orientacao

        payload = self._get_json(self.PHOTO_SEARCH_URL, params)
        fotos = payload.get("photos", [])
        if not isinstance(fotos, list):
            raise PexelsAPIError("Resposta de fotos do Pexels veio em formato inesperado.")
        self.logger.info("Pexels: %s fotos %s recebidas", len(fotos), label)
        return fotos

    def _selecionar_video(
        self,
        videos: list[dict[str, Any]],
        midias_usadas: set[str],
        orientacao_alvo: str,
    ) -> dict[str, Any] | None:
        for video in videos:
            media_id = str(video.get("id", ""))
            if not media_id:
                continue
            if media_id in midias_usadas:
                self.logger.info("Pexels: pulando video repetido id=%s", media_id)
                continue
            if self._orientacao(video) != orientacao_alvo:
                continue

            arquivo = self._selecionar_arquivo_video(video, orientacao_alvo)
            if arquivo is None:
                self.logger.info("Pexels: video id=%s sem arquivo 1080p valido", media_id)
                continue

            video["_arquivo_escolhido"] = arquivo
            self.logger.info("Pexels: video selecionado id=%s orientacao=%s", media_id, orientacao_alvo)
            return video

        return None

    def _selecionar_arquivo_video(
        self,
        video: dict[str, Any],
        orientacao_alvo: str,
    ) -> dict[str, Any] | None:
        arquivos = video.get("video_files", [])
        if not isinstance(arquivos, list):
            return None

        candidatos: list[dict[str, Any]] = []
        for arquivo in arquivos:
            if arquivo.get("file_type") != "video/mp4":
                continue
            if not arquivo.get("link"):
                continue
            largura = self._to_int(arquivo.get("width"))
            altura = self._to_int(arquivo.get("height"))
            if largura is None or altura is None:
                continue
            if self._orientacao(arquivo) != orientacao_alvo:
                continue
            if min(largura, altura) < self.min_video_short_side:
                continue
            candidatos.append(arquivo)

        if not candidatos:
            return None

        # Pick the smallest file that still satisfies 1080p. It keeps downloads fast
        # while matching the editor output target.
        return sorted(candidatos, key=lambda item: self._area(item))[0]

    def _selecionar_foto(
        self,
        fotos: list[dict[str, Any]],
        midias_usadas: set[str],
    ) -> dict[str, Any] | None:
        for foto in fotos:
            media_id = str(foto.get("id", ""))
            if not media_id:
                continue
            if media_id in midias_usadas:
                self.logger.info("Pexels: pulando foto repetida id=%s", media_id)
                continue

            largura = self._to_int(foto.get("width")) or 0
            altura = self._to_int(foto.get("height")) or 0
            if max(largura, altura) < self.min_photo_long_side:
                self.logger.info("Pexels: foto id=%s abaixo da resolucao minima", media_id)
                continue

            src = foto.get("src", {})
            if not isinstance(src, dict) or not src.get("original"):
                continue

            self.logger.info("Pexels: foto selecionada id=%s", media_id)
            return foto

        return None

    def _baixar_video(
        self,
        video: dict[str, Any],
        orientacao: str,
        storage_dir: Path,
    ) -> dict[str, Any]:
        media_id = str(video["id"])
        arquivo = video["_arquivo_escolhido"]
        output_path = self._unique_path(storage_dir / f"pexels_{media_id}_{orientacao}.mp4")
        self._download_file(arquivo["link"], output_path)

        return {
            "id": media_id,
            "tipo": "video",
            "path_local": str(output_path.resolve()),
            "precisa_de_grid": orientacao == "landscape",
            "is_photo": False,
            "orientacao": orientacao,
            "width": self._to_int(arquivo.get("width")),
            "height": self._to_int(arquivo.get("height")),
            "pexels_url": video.get("url"),
            "autor": self._autor_video(video),
            "download_url": arquivo.get("link"),
        }

    def _baixar_foto(
        self,
        foto: dict[str, Any],
        orientacao: str,
        storage_dir: Path,
    ) -> dict[str, Any]:
        media_id = str(foto["id"])
        src = foto["src"]
        output_path = self._unique_path(storage_dir / f"pexels_{media_id}_{orientacao}.jpg")
        self._download_file(src["original"], output_path)

        return {
            "id": media_id,
            "tipo": "foto",
            "path_local": str(output_path.resolve()),
            "precisa_de_grid": False,
            "is_photo": True,
            "orientacao": orientacao,
            "width": self._to_int(foto.get("width")),
            "height": self._to_int(foto.get("height")),
            "pexels_url": foto.get("url"),
            "autor": foto.get("photographer"),
            "download_url": src.get("original"),
        }

    def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.session.get(
                url,
                headers={"Authorization": self.api_key},
                params=params,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise PexelsAPIError(f"Falha de conexao com Pexels: {exc}") from exc

        remaining = response.headers.get("X-Ratelimit-Remaining")
        if remaining is not None:
            self.logger.info("Pexels: requests restantes no periodo=%s", remaining)

        if response.status_code in {401, 403}:
            raise PexelsAPIError("PEXELS_API_KEY invalida ou sem permissao.")
        if response.status_code == 429:
            raise PexelsAPIError("Limite de requisicoes do Pexels atingido.")
        if not response.ok:
            detail = self._compact_response_text(response.text)
            raise PexelsAPIError(f"Pexels retornou HTTP {response.status_code}: {detail}")

        try:
            data = response.json()
        except ValueError as exc:
            raise PexelsAPIError("Pexels retornou JSON invalido.") from exc

        if not isinstance(data, dict):
            raise PexelsAPIError("Pexels retornou payload inesperado.")
        return data

    def _download_file(self, download_url: str, output_path: Path) -> None:
        self.logger.info("Pexels: baixando %s", output_path.name)
        temp_path = output_path.with_suffix(output_path.suffix + ".part")

        try:
            with requests.get(download_url, stream=True, timeout=self.download_timeout) as response:
                if not response.ok:
                    detail = self._compact_response_text(response.text)
                    raise PexelsAPIError(f"Download retornou HTTP {response.status_code}: {detail}")

                with temp_path.open("wb") as file:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            file.write(chunk)
        except requests.RequestException as exc:
            self._safe_unlink(temp_path)
            raise PexelsAPIError(f"Falha no download da midia: {exc}") from exc
        except Exception:
            self._safe_unlink(temp_path)
            raise

        if not temp_path.exists() or temp_path.stat().st_size == 0:
            self._safe_unlink(temp_path)
            raise PexelsAPIError("Download gerou arquivo vazio.")

        temp_path.replace(output_path)
        self.logger.info("Pexels: arquivo salvo em %s (%s bytes)", output_path, output_path.stat().st_size)

    def _require_api_key(self) -> None:
        if not self.api_key:
            raise PexelsFetcherError("PEXELS_API_KEY nao configurada no .env.")

    @staticmethod
    def _normalizar_query(query: str) -> str:
        query = query.strip()
        if not query:
            raise ValueError("query nao pode ser vazia.")
        return query

    @staticmethod
    def _orientacao(media: dict[str, Any]) -> str:
        largura = PexelsFetcher._to_int(media.get("width")) or 0
        altura = PexelsFetcher._to_int(media.get("height")) or 0
        if altura > largura:
            return "portrait"
        if largura > altura:
            return "landscape"
        return "square"

    @staticmethod
    def _area(media: dict[str, Any]) -> int:
        largura = PexelsFetcher._to_int(media.get("width")) or 0
        altura = PexelsFetcher._to_int(media.get("height")) or 0
        return largura * altura

    @staticmethod
    def _to_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _autor_video(video: dict[str, Any]) -> str | None:
        usuario = video.get("user")
        if isinstance(usuario, dict):
            nome = usuario.get("name")
            return str(nome) if nome else None
        return None

    @staticmethod
    def _unique_path(path: Path) -> Path:
        if not path.exists():
            return path

        stem = path.stem
        suffix = path.suffix
        parent = path.parent
        counter = 1
        while True:
            candidate = parent / f"{stem}_{counter:03d}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    @staticmethod
    def _safe_unlink(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _compact_response_text(text: str | None) -> str:
        if not text:
            return "sem detalhes"
        text = " ".join(text.split())
        return text[:300]


def _teste_isolado() -> None:
    fetcher = PexelsFetcher()
    resultado = fetcher.obter_midia_para_cena(
        query="cinematic rain city",
        midias_usadas=set(),
        storage_path=TEMP_DIR / "pexels_fetcher_test",
    )
    print(json.dumps(resultado, indent=2))


if __name__ == "__main__":
    _teste_isolado()
