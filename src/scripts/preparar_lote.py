"""Prepara lotes de roteiros externos para curadoria e renderizacao.

Uso:
    python src/scripts/preparar_lote.py entradas_lotes/espaco/lote_01.json espaco
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.core.pexels_fetcher import PexelsFetcher, PexelsFetcherError, PexelsNoResultsError
from src.utils.logger import get_logger


LOTES_PREPARADOS_DIR = ROOT_DIR / "workspace" / "lotes_preparados"
VERSAO_KEYS = ("versao_longa", "versao_curta")
VERSAO_CONTAINER_KEYS = ("versoes", "roteiros")
CENA_1_MIN_CLIPES = 3
CENA_1_MAX_CLIPES = 4
DEMAIS_CENAS_MIN_CLIPES = 1
DEMAIS_CENAS_MAX_CLIPES = 2


@dataclass(frozen=True)
class VersaoDoRoteiro:
    """Uma versao isolada pronta para virar metadata.json."""

    indice_roteiro: int
    nome_versao: str
    metadata: dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepara um lote JSON externo em workspace/lotes_preparados."
    )
    parser.add_argument("json_entrada", type=Path, help="Caminho do JSON de entrada.")
    parser.add_argument("nicho", help="Nome do nicho. Ex: espaco, estoicismo.")
    return parser.parse_args()


def preparar_lote(
    json_entrada: str | Path,
    nicho: str,
    *,
    fetcher: PexelsFetcher | None = None,
    output_root: str | Path = LOTES_PREPARADOS_DIR,
) -> dict[str, Any]:
    """Prepara todos os roteiros do lote e baixa midias-base do Pexels."""

    logger = get_logger(__name__)
    entrada_path = Path(json_entrada)
    lote = _ler_lote(entrada_path)
    nicho_slug = _nome_diretorio(nicho, fallback="nicho")
    output_base = Path(output_root) / nicho_slug
    pexels = fetcher or PexelsFetcher()

    resultados: list[dict[str, Any]] = []
    total_midias = 0

    for indice, roteiro in enumerate(lote, start=1):
        try:
            versoes = list(_extrair_versoes(roteiro, indice))
        except ValueError as exc:
            logger.error("Lote: roteiro %s invalido: %s", indice, exc)
            resultados.append(
                {
                    "indice_roteiro": indice,
                    "status": "erro_metadata",
                    "erro": str(exc),
                }
            )
            continue

        for versao in versoes:
            resultado = _preparar_versao(
                versao=versao,
                nicho_slug=nicho_slug,
                output_base=output_base,
                fetcher=pexels,
                logger=logger,
            )
            total_midias += int(resultado.get("midias_baixadas", 0))
            resultados.append(resultado)

    resumo = {
        "entrada": str(entrada_path.resolve()),
        "nicho": nicho_slug,
        "saida": str(output_base.resolve()),
        "roteiros_recebidos": len(lote),
        "versoes_processadas": len(resultados),
        "midias_baixadas": total_midias,
        "resultados": resultados,
    }
    logger.info(
        "Lote: preparo concluido com %s versoes e %s midias.",
        resumo["versoes_processadas"],
        resumo["midias_baixadas"],
    )
    return resumo


def _preparar_versao(
    *,
    versao: VersaoDoRoteiro,
    nicho_slug: str,
    output_base: Path,
    fetcher: PexelsFetcher,
    logger: Any,
) -> dict[str, Any]:
    tema = _tema_da_metadata(versao.metadata, versao.indice_roteiro)
    tema_dir = _nome_diretorio(tema, fallback=f"tema_{versao.indice_roteiro:03d}")
    versao_dir = output_base / versao.nome_versao / tema_dir
    versao_dir.mkdir(parents=True, exist_ok=True)

    roteiro_path = versao_dir / "roteiro.md"
    metadata_path = versao_dir / "metadata.json"
    _salvar_roteiro_md(roteiro_path, tema, versao.nome_versao, versao.metadata)
    _salvar_metadata(metadata_path, versao.metadata)

    logger.info(
        "Lote: preparando %s/%s em %s",
        nicho_slug,
        versao.nome_versao,
        versao_dir,
    )

    midias_baixadas = 0
    try:
        midias_baixadas = _baixar_midias_da_versao(
            metadata=versao.metadata,
            destino=versao_dir,
            fetcher=fetcher,
            logger=logger,
        )
    except (PexelsFetcherError, OSError, RuntimeError, ValueError) as exc:
        logger.error(
            "Lote: Pexels falhou para tema='%s' versao='%s': %s. "
            "metadata.json mantido; avancando sem travar a esteira.",
            tema,
            versao.nome_versao,
            exc,
        )
        return {
            "indice_roteiro": versao.indice_roteiro,
            "tema": tema,
            "versao": versao.nome_versao,
            "diretorio": str(versao_dir.resolve()),
            "metadata": str(metadata_path.resolve()),
            "roteiro": str(roteiro_path.resolve()),
            "status": "erro_pexels",
            "midias_baixadas": midias_baixadas,
            "erro": str(exc),
        }

    return {
        "indice_roteiro": versao.indice_roteiro,
        "tema": tema,
        "versao": versao.nome_versao,
        "diretorio": str(versao_dir.resolve()),
        "metadata": str(metadata_path.resolve()),
        "roteiro": str(roteiro_path.resolve()),
        "status": "ok",
        "midias_baixadas": midias_baixadas,
    }


def _baixar_midias_da_versao(
    *,
    metadata: dict[str, Any],
    destino: Path,
    fetcher: PexelsFetcher,
    logger: Any,
) -> int:
    cenas = _cenas_da_metadata(metadata)
    midias_usadas: set[str] = set()
    total_baixado = 0

    for indice_cena, cena in enumerate(cenas, start=1):
        busca = str(cena.get("busca", "")).strip()
        if not busca:
            raise ValueError(f"cena {indice_cena} precisa do campo busca.")

        if indice_cena == 1:
            minimo = CENA_1_MIN_CLIPES
            maximo = CENA_1_MAX_CLIPES
            orientacoes = ("portrait",)
        else:
            minimo = DEMAIS_CENAS_MIN_CLIPES
            maximo = DEMAIS_CENAS_MAX_CLIPES
            orientacoes = ("portrait", "landscape")

        baixados_na_cena = 0
        for indice_clipe in range(1, maximo + 1):
            try:
                media = _baixar_video_pexels(
                    fetcher=fetcher,
                    query=busca,
                    midias_usadas=midias_usadas,
                    storage_dir=destino,
                    orientacoes=orientacoes,
                )
            except PexelsFetcherError as exc:
                if baixados_na_cena < minimo:
                    raise PexelsNoResultsError(
                        f"cena {indice_cena} busca='{busca}' baixou "
                        f"{baixados_na_cena}/{minimo} videos obrigatorios: {exc}"
                    ) from exc
                logger.warning(
                    "Lote: cena %s busca='%s' ficou com %s videos; "
                    "clipe extra opcional ignorado: %s",
                    indice_cena,
                    busca,
                    baixados_na_cena,
                    exc,
                )
                break

            midias_usadas.add(str(media["id"]))
            destino_final = destino / f"cena_{indice_cena}_{indice_clipe:02d}.mp4"
            _mover_midia_baixada(Path(str(media["path_local"])), destino_final)
            media["path_local"] = str(destino_final.resolve())
            baixados_na_cena += 1
            total_baixado += 1
            logger.info(
                "Lote: cena %s clipe %s salvo em %s",
                indice_cena,
                indice_clipe,
                destino_final.name,
            )

    return total_baixado


def _baixar_video_pexels(
    *,
    fetcher: PexelsFetcher,
    query: str,
    midias_usadas: set[str],
    storage_dir: Path,
    orientacoes: Iterable[str],
) -> dict[str, Any]:
    """Baixa apenas videos, sem fallback para foto, para a curadoria visual."""

    query = fetcher._normalizar_query(query)
    fetcher._require_api_key()
    storage_dir.mkdir(parents=True, exist_ok=True)
    usadas = {str(media_id) for media_id in midias_usadas}

    for orientacao in orientacoes:
        videos = fetcher._buscar_videos(query, orientacao)
        video = fetcher._selecionar_video(videos, usadas, orientacao)
        if video is not None:
            return fetcher._baixar_video(video, orientacao, storage_dir)

    raise PexelsNoResultsError(f"Nenhum video valido encontrado para: {query}")


def _mover_midia_baixada(origem: Path, destino: Path) -> None:
    if not origem.exists() or origem.stat().st_size == 0:
        raise RuntimeError(f"Download invalido ou vazio: {origem}")
    destino.parent.mkdir(parents=True, exist_ok=True)
    origem.replace(destino)


def _ler_lote(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"JSON de entrada nao encontrado: {path}")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON de entrada invalido: {exc}") from exc

    if not isinstance(payload, list):
        raise ValueError("JSON de entrada deve ser um array de roteiros.")
    if not payload:
        raise ValueError("JSON de entrada nao possui roteiros.")

    lote: list[dict[str, Any]] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"roteiro {index} deve ser um objeto JSON.")
        lote.append(item)
    return lote


def _extrair_versoes(roteiro: dict[str, Any], indice_roteiro: int) -> list[VersaoDoRoteiro]:
    versoes: list[VersaoDoRoteiro] = []

    for container_key in VERSAO_CONTAINER_KEYS:
        container = roteiro.get(container_key)
        if isinstance(container, dict):
            shared = _campos_compartilhados(roteiro)
            for nome_versao, dados_versao in container.items():
                versoes.append(
                    _montar_versao(
                        indice_roteiro=indice_roteiro,
                        nome_versao=_normalizar_versao(str(nome_versao)),
                        shared=shared,
                        dados_versao=dados_versao,
                    )
                )

    shared = _campos_compartilhados(roteiro)
    for nome_versao in VERSAO_KEYS:
        if nome_versao in roteiro:
            versoes.append(
                _montar_versao(
                    indice_roteiro=indice_roteiro,
                    nome_versao=nome_versao,
                    shared=shared,
                    dados_versao=roteiro[nome_versao],
                )
            )

    if not versoes and "cenas" in roteiro:
        nome_versao = _normalizar_versao(str(roteiro.get("versao", "versao_longa")))
        versoes.append(
            _montar_versao(
                indice_roteiro=indice_roteiro,
                nome_versao=nome_versao,
                shared={},
                dados_versao=roteiro,
            )
        )

    if not versoes:
        raise ValueError(
            "roteiro precisa conter versao_longa/versao_curta, um container versoes/roteiros "
            "ou um campo cenas para versao unica."
        )

    return versoes


def _montar_versao(
    *,
    indice_roteiro: int,
    nome_versao: str,
    shared: dict[str, Any],
    dados_versao: Any,
) -> VersaoDoRoteiro:
    if isinstance(dados_versao, list):
        metadata = copy.deepcopy(shared)
        metadata["versao"] = nome_versao
        metadata["cenas"] = copy.deepcopy(dados_versao)
    elif isinstance(dados_versao, dict):
        metadata = copy.deepcopy(shared)
        metadata.update(copy.deepcopy(dados_versao))
        metadata.setdefault("versao", nome_versao)
    else:
        raise ValueError(f"{nome_versao} deve ser objeto ou array de cenas.")

    cenas = _cenas_da_metadata(metadata)
    _validar_cenas(cenas, nome_versao)
    return VersaoDoRoteiro(
        indice_roteiro=indice_roteiro,
        nome_versao=_normalizar_versao(str(metadata.get("versao", nome_versao))),
        metadata=metadata,
    )


def _campos_compartilhados(roteiro: dict[str, Any]) -> dict[str, Any]:
    ignorar = set(VERSAO_KEYS) | set(VERSAO_CONTAINER_KEYS)
    return {key: copy.deepcopy(value) for key, value in roteiro.items() if key not in ignorar}


def _cenas_da_metadata(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    cenas = metadata.get("cenas")
    if cenas is None:
        cenas = metadata.get("scenes")
    if not isinstance(cenas, list) or not cenas:
        raise ValueError("metadata da versao precisa conter uma lista nao vazia em cenas.")
    if not all(isinstance(cena, dict) for cena in cenas):
        raise ValueError("todas as cenas precisam ser objetos JSON.")
    return cenas


def _validar_cenas(cenas: list[dict[str, Any]], nome_versao: str) -> None:
    for indice, cena in enumerate(cenas, start=1):
        texto = str(cena.get("texto", "")).strip()
        busca = str(cena.get("busca", "")).strip()
        if not texto or not busca:
            raise ValueError(f"{nome_versao}: cena {indice} precisa de texto e busca.")


def _salvar_metadata(path: Path, metadata: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _salvar_roteiro_md(path: Path, tema: str, nome_versao: str, metadata: dict[str, Any]) -> None:
    cenas = _cenas_da_metadata(metadata)
    linhas = [
        f"# {tema}",
        "",
        f"- Versao: `{nome_versao}`",
        f"- Total de cenas: {len(cenas)}",
        "",
    ]

    for indice, cena in enumerate(cenas, start=1):
        linhas.extend(
            [
                f"## Cena {indice}",
                "",
                str(cena.get("texto", "")).strip(),
                "",
                f"Busca: `{str(cena.get('busca', '')).strip()}`",
                "",
            ]
        )

    path.write_text("\n".join(linhas).rstrip() + "\n", encoding="utf-8")


def _tema_da_metadata(metadata: dict[str, Any], indice_roteiro: int) -> str:
    for key in ("tema", "nome_do_tema", "titulo", "title", "nome", "assunto"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"tema_{indice_roteiro:03d}"


def _normalizar_versao(value: str) -> str:
    value = value.strip().lower()
    value = value.replace("-", "_").replace(" ", "_")
    if value in {"longa", "longo", "long", "tiktok", "kwai"}:
        return "versao_longa"
    if value in {"curta", "curto", "short", "shorts", "reels"}:
        return "versao_curta"
    return value or "versao_longa"


def _nome_diretorio(value: str, *, fallback: str) -> str:
    nome = re.sub(r"\s+", "_", value.strip())
    nome = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", nome)
    nome = nome.strip("._ ")
    return nome or fallback


def main() -> int:
    args = parse_args()
    logger = get_logger(__name__)

    try:
        resumo = preparar_lote(args.json_entrada, args.nicho)
    except Exception as exc:
        logger.error("Lote: falha geral no preparo: %s", exc)
        return 1

    print(json.dumps(resumo, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
