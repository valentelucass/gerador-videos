"""Prepara lotes horizontais 16:9 para curadoria e renderizacao no YouTube.

Uso:
    python src/scripts/preparar_horizontal.py entradas_lotes/youtube/lote.json espaco
"""

from __future__ import annotations

import argparse
import copy
import json
import random
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.core.pexels_fetcher import PexelsFetcher, PexelsFetcherError
from src.utils.file_retry import replace_com_retry, unlink_com_retry
from src.utils.logger import get_logger
from src.utils.text_helpers import normalizar_ascii


LOTES_HORIZONTAIS_DIR = ROOT_DIR / "workspace" / "lotes_horizontais"
CONTAINER_KEYS = ("temas", "roteiros", "videos", "itens")
TEMPLATE_MEDIA_COUNTS = {
    1: 1,
    2: 1,
    3: 2,
    4: 0,
    5: 3,
    6: 1,
    7: 2,
    8: 1,
    9: 2,
    10: 2,
    11: 1,
    12: 1,
}
TEMPLATES_COM_TEXTO = {4, 6, 7, 8, 9, 11, 12}
TEMPLATE_11_TOPICOS_EXIGIDOS = 4
LETRAS_SLOT = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
LOCAL_MEDIA_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".mp4", ".mov", ".mkv", ".webm"}
)


@dataclass(frozen=True)
class TemaHorizontal:
    indice: int
    tema: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SlotMidia:
    indice: int
    letra: str | None
    fonte_midia: str
    # ``None`` e permitido somente para um slot ``local`` sem tag suficiente
    # em ``buscas_locais``. O preparo escolhe entao um arquivo ainda nao usado
    # naquela cena, mantendo os slots fisicos distintos.
    prompt_ou_busca: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepara um JSON horizontal em workspace/lotes_horizontais."
    )
    parser.add_argument("json_entrada", type=Path, help="Caminho do JSON de entrada.")
    parser.add_argument("nicho", help="Nome do nicho. Ex: espaco, historia, negocios.")
    return parser.parse_args()


def preparar_horizontal(
    json_entrada: str | Path,
    nicho: str,
    *,
    fetcher: PexelsFetcher | None = None,
    output_root: str | Path = LOTES_HORIZONTAIS_DIR,
) -> dict[str, Any]:
    """Prepara todos os temas horizontais contidos no JSON de entrada."""

    logger = get_logger(__name__)
    entrada_path = Path(json_entrada)
    payload = _ler_json(entrada_path)
    temas = _extrair_temas(payload, entrada_path)
    nicho_slug = _slug(nicho, fallback="nicho")
    output_base = Path(output_root) / nicho_slug
    pexels = fetcher or PexelsFetcher()

    resultados: list[dict[str, Any]] = []
    total_pexels = 0
    total_prompts = 0

    for tema in temas:
        resultado = _preparar_tema(
            tema=tema,
            output_base=output_base,
            diretorio_origem=entrada_path.resolve().parent,
            fetcher=pexels,
            logger=logger,
        )
        total_pexels += int(resultado.get("midias_pexels", 0))
        total_prompts += int(resultado.get("prompts_ia", 0))
        resultados.append(resultado)

    resumo = {
        "entrada": str(entrada_path.resolve()),
        "nicho": nicho_slug,
        "saida": str(output_base.resolve()),
        "temas_processados": len(resultados),
        "midias_pexels": total_pexels,
        "prompts_ia": total_prompts,
        "resultados": resultados,
    }
    logger.info(
        "Horizontal: preparo concluido com %s temas, %s midias Pexels e %s prompts IA.",
        resumo["temas_processados"],
        resumo["midias_pexels"],
        resumo["prompts_ia"],
    )
    return resumo


def _preparar_tema(
    *,
    tema: TemaHorizontal,
    output_base: Path,
    diretorio_origem: Path,
    fetcher: PexelsFetcher,
    logger: Any,
) -> dict[str, Any]:
    tema_slug = _slug(tema.tema, fallback=f"tema_{tema.indice:03d}")
    tema_dir = output_base / tema_slug
    tema_dir.mkdir(parents=True, exist_ok=True)

    metadata = copy.deepcopy(tema.metadata)
    metadata["tema"] = tema.tema
    metadata["cenas"] = _cenas_da_metadata(metadata)
    metadata_path = tema_dir / "metadata.json"
    _salvar_metadata(metadata_path, metadata)

    logger.info("Horizontal: preparando tema='%s' em %s", tema.tema, tema_dir)

    try:
        midias_pexels, prompts_ia = _preparar_midias_do_tema(
            cenas=metadata["cenas"],
            destino=tema_dir,
            diretorio_origem=diretorio_origem,
            fetcher=fetcher,
            logger=logger,
        )
    except (PexelsFetcherError, OSError, RuntimeError, ValueError) as exc:
        logger.error("Horizontal: falha no tema='%s': %s", tema.tema, exc)
        return {
            "indice_tema": tema.indice,
            "tema": tema.tema,
            "diretorio": str(tema_dir.resolve()),
            "metadata": str(metadata_path.resolve()),
            "status": "erro",
            "midias_pexels": 0,
            "prompts_ia": 0,
            "erro": str(exc),
        }

    return {
        "indice_tema": tema.indice,
        "tema": tema.tema,
        "diretorio": str(tema_dir.resolve()),
        "metadata": str(metadata_path.resolve()),
        "status": "ok",
        "midias_pexels": midias_pexels,
        "prompts_ia": prompts_ia,
    }


def _preparar_midias_do_tema(
    *,
    cenas: list[dict[str, Any]],
    destino: Path,
    diretorio_origem: Path,
    fetcher: PexelsFetcher,
    logger: Any,
) -> tuple[int, int]:
    midias_usadas: set[str] = set()
    total_pexels = 0
    total_prompts = 0

    for indice_cena, cena in enumerate(cenas, start=1):
        slots = _slots_da_cena(cena)
        midias_locais_usadas: set[Path] = set()
        for slot in slots:
            if slot.fonte_midia == "pexels":
                busca = _exigir_texto_slot(slot, "Pexels")
                media = fetcher.obter_midia_para_cena(
                    query=busca,
                    midias_usadas=midias_usadas,
                    storage_path=destino,
                    orientacao="landscape",
                )
                midias_usadas.add(str(media["id"]))
                destino_final = destino / _nome_arquivo_pexels(
                    indice_cena,
                    slot.letra,
                    Path(str(media["path_local"])).suffix,
                    str(media.get("tipo", "")),
                )
                _mover_midia_baixada(Path(str(media["path_local"])), destino_final)
                total_pexels += 1
                logger.info(
                    "Horizontal: cena %s slot %s Pexels salvo em %s",
                    indice_cena,
                    slot.letra or "-",
                    destino_final.name,
                )
                continue

            if slot.fonte_midia == "ia":
                prompt = _exigir_texto_slot(slot, "IA")
                prompt_path = destino / _nome_arquivo_prompt(indice_cena, slot.letra)
                prompt_path.write_text(prompt.rstrip() + "\n", encoding="utf-8")
                total_prompts += 1
                logger.info(
                    "Horizontal: cena %s slot %s prompt IA salvo em %s",
                    indice_cena,
                    slot.letra or "-",
                    prompt_path.name,
                )
                continue

            if slot.fonte_midia == "local":
                origem = _resolver_midia_local_para_slot(
                    diretorio_origem=diretorio_origem,
                    busca_local=slot.prompt_ou_busca,
                    midias_usadas=midias_locais_usadas,
                )
                midias_locais_usadas.add(origem.resolve())
                destino_final = destino / _nome_arquivo_local(
                    indice_cena,
                    slot.letra,
                    origem.suffix,
                )
                _copiar_midia_local(origem, destino_final)
                logger.info(
                    "Horizontal: cena %s slot %s midia local '%s' copiada para %s",
                    indice_cena,
                    slot.letra or "-",
                    origem.name,
                    destino_final.name,
                )
                continue

            raise ValueError(
                f"cena {indice_cena}: fonte_midia invalida '{slot.fonte_midia}'. "
                "Use 'pexels', 'ia' ou 'local'."
            )

    return total_pexels, total_prompts


def _exigir_texto_slot(slot: SlotMidia, fonte: str) -> str:
    """Retorna o texto obrigatorio de uma fonte que nao aceita fallback local."""

    texto = slot.prompt_ou_busca
    if not isinstance(texto, str) or not texto.strip():
        raise ValueError(
            f"slot {slot.letra or slot.indice} com fonte_midia '{fonte.lower()}' "
            "exige prompt_ou_busca nao vazio."
        )
    return texto


def _slots_da_cena(cena: dict[str, Any]) -> list[SlotMidia]:
    midias = cena.get("midias")
    if midias is None:
        midias = cena.get("medias")

    if isinstance(midias, list) and midias:
        total = len(midias)
        slots = [
            _slot_from_media_item(cena, item, index, total)
            for index, item in enumerate(midias)
        ]
    else:
        total = _quantidade_midias(cena)
        slots = []
        for index in range(total):
            letra = _letra_slot(index, total)
            fonte = _normalizar_fonte(
                _valor_por_slot(cena.get("fonte_midia"), index)
                or _valor_por_slot(cena.get("fonte"), index)
                or _valor_por_slot(cena.get("source_media"), index)
                or _valor_por_slot(cena.get("media_source"), index)
            )
            valor_origem = (
                _busca_local_por_slot(cena, index)
                if fonte == "local"
                else _texto_por_slot(cena, index)
            )
            slots.append(SlotMidia(index + 1, letra, fonte, valor_origem))

    template_id = _template_id(cena)
    esperado = TEMPLATE_MEDIA_COUNTS[template_id]
    if len(slots) != esperado:
        raise ValueError(
            f"template {template_id} exige exatamente {esperado} midias; "
            f"cena declarou {len(slots)}."
        )

    return slots


def _slot_from_media_item(
    cena: dict[str, Any],
    item: Any,
    index: int,
    total: int,
) -> SlotMidia:
    letra = _letra_slot(index, total)
    if isinstance(item, dict):
        fonte = _normalizar_fonte(
            item.get("fonte_midia")
            or item.get("fonte")
            or item.get("source_media")
            or item.get("media_source")
            or _valor_por_slot(cena.get("fonte_midia"), index)
            or _valor_por_slot(cena.get("fonte"), index)
            or _valor_por_slot(cena.get("source_media"), index)
            or _valor_por_slot(cena.get("media_source"), index)
        )
        if fonte == "local":
            return SlotMidia(
                index + 1,
                letra,
                fonte,
                _busca_local_por_slot(cena, index, item.get("busca_local")),
            )
        prompt = _normalizar_prompt(
            item.get("prompt_ou_busca")
            or item.get("busca")
            or item.get("prompt")
            or _texto_por_slot(cena, index)
        )
        return SlotMidia(index + 1, letra, fonte, prompt)

    fonte = _normalizar_fonte(
        _valor_por_slot(cena.get("fonte_midia"), index)
        or _valor_por_slot(cena.get("fonte"), index)
        or _valor_por_slot(cena.get("source_media"), index)
        or _valor_por_slot(cena.get("media_source"), index)
    )
    valor_origem = (
        _busca_local_por_slot(cena, index, item)
        if fonte == "local"
        else _normalizar_prompt(item)
    )
    return SlotMidia(index + 1, letra, fonte, valor_origem)


def _quantidade_midias(cena: dict[str, Any]) -> int:
    for key in (
        "quantidade_midias",
        "qtd_midias",
        "numero_midias",
        "num_midias",
        "midias_exigidas",
        "midias_necessarias",
        "slots_midia",
        "media_count",
    ):
        quantidade = _int_positivo(cena.get(key))
        if quantidade is not None:
            return quantidade

    for key in (
        "prompt_ou_busca",
        "busca_local",
        "fonte_midia",
        "fonte",
        "source_media",
        "media_source",
    ):
        value = cena.get(key)
        if isinstance(value, list) and value:
            return len(value)
        if isinstance(value, dict) and value:
            return len(value)

    return TEMPLATE_MEDIA_COUNTS[_template_id(cena)]


def _texto_por_slot(cena: dict[str, Any], index: int) -> str:
    value = (
        _valor_por_slot(cena.get("prompt_ou_busca"), index)
        or _valor_por_slot(cena.get("busca"), index)
        or _valor_por_slot(cena.get("prompt"), index)
    )
    return _normalizar_prompt(value)


def _busca_local_por_slot(
    cena: dict[str, Any],
    index: int,
    valor_preferencial: Any = None,
) -> str | None:
    """Resolve a tag local explicita, sem repetir a ultima tag da lista.

    ``buscas_locais`` e o contrato atual para cenas com mais de um slot. Ao
    contrario de ``_valor_por_slot``, sua ausencia em um slot nao replica a
    ultima busca: o chamador completa esse buraco com um arquivo local ainda
    nao utilizado. ``busca_local`` continua aceito por compatibilidade, mas
    segue a mesma regra para impedir clonagem acidental de fotos.
    """

    if valor_preferencial is not None:
        return _normalizar_busca_local(valor_preferencial)

    if "buscas_locais" in cena:
        return _busca_local_explicita_por_slot(cena["buscas_locais"], index)
    if "busca_local" in cena:
        return _busca_local_explicita_por_slot(cena["busca_local"], index)
    raise ValueError(
        "busca_local ou buscas_locais e obrigatoria para cada cena com "
        "fonte_midia 'local'."
    )


def _busca_local_explicita_por_slot(value: Any, index: int) -> str | None:
    """Le uma busca local por slot sem fallback para o ultimo elemento."""

    if isinstance(value, list):
        if not all(isinstance(item, str) and item.strip() for item in value):
            raise ValueError(
                "buscas_locais deve ser uma lista de tags locais nao vazias."
            )
        if index >= len(value):
            return None
        return _normalizar_busca_local(value[index])

    if isinstance(value, dict):
        letra = LETRAS_SLOT[index] if index < len(LETRAS_SLOT) else str(index + 1)
        for key in (letra, letra.lower(), str(index + 1), index):
            if key in value:
                return _normalizar_busca_local(value[key])
        return None

    # A chave legada singular identifica somente o primeiro slot. Os demais
    # recebem um fallback aleatorio e distinto, em vez de clonar a mesma foto.
    if index > 0:
        return None
    return _normalizar_busca_local(value)


def _valor_por_slot(value: Any, index: int) -> Any:
    if isinstance(value, list):
        if index < len(value):
            return value[index]
        return value[-1] if value else None

    if isinstance(value, dict):
        letra = LETRAS_SLOT[index] if index < len(LETRAS_SLOT) else str(index + 1)
        for key in (letra, letra.lower(), str(index + 1), index):
            if key in value:
                return value[key]
        return None

    return value


def _normalizar_fonte(value: Any) -> str:
    fonte = str(value or "").strip().lower()
    if not fonte:
        raise ValueError("fonte_midia e obrigatoria para cada slot de midia.")
    if fonte in {"ai", "ia", "image_ai", "imagem_ia"}:
        return "ia"
    if fonte in {"pexels", "pexel"}:
        return "pexels"
    if fonte == "local":
        return "local"
    return fonte


def _normalizar_prompt(value: Any) -> str:
    prompt = str(value or "").strip()
    if not prompt:
        raise ValueError("prompt_ou_busca nao pode ser vazio.")
    return prompt


def _normalizar_busca_local(value: Any) -> str:
    busca_local = str(value or "").strip()
    if not busca_local:
        raise ValueError(
            "busca_local e obrigatoria para cada slot com fonte_midia 'local'."
        )
    return busca_local


def _template_id(cena: dict[str, Any]) -> int:
    for key in ("template_id", "template", "layout_id", "layout"):
        if key not in cena:
            continue
        value = _int_positivo(cena.get(key))
        if value not in TEMPLATE_MEDIA_COUNTS:
            raise ValueError("template_id deve ser um inteiro entre 1 e 12.")
        return value
    raise ValueError("template_id e obrigatorio.")


def _nome_arquivo_pexels(indice_cena: int, letra: str | None, suffix: str, tipo: str) -> str:
    extensao = ".mp4" if tipo == "video" else (suffix or ".jpg")
    slot = f"_{letra}" if letra else ""
    return f"cena_{indice_cena:02d}{slot}_pexels{extensao}"


def _nome_arquivo_local(indice_cena: int, letra: str | None, suffix: str) -> str:
    extensao = suffix.lower()
    if extensao not in LOCAL_MEDIA_EXTENSIONS:
        raise ValueError(
            f"Extensao de midia local nao suportada: {suffix or '(sem extensao)'}.")
    slot = f"_{letra}" if letra else ""
    return f"cena_{indice_cena:02d}{slot}_local{extensao}"


def _nome_arquivo_prompt(indice_cena: int, letra: str | None) -> str:
    slot = f"_{letra}" if letra else ""
    return f"cena_{indice_cena:02d}{slot}_PROMPT_IA.txt"


def _letra_slot(index: int, total: int) -> str | None:
    if total <= 1:
        return None
    if index < len(LETRAS_SLOT):
        return LETRAS_SLOT[index]
    return str(index + 1)


def _mover_midia_baixada(origem: Path, destino: Path) -> None:
    if not origem.exists() or origem.stat().st_size == 0:
        raise RuntimeError(f"Download invalido ou vazio: {origem}")
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists():
        unlink_com_retry(destino)
    replace_com_retry(origem, destino)


def _buscar_midia_local_por_substring(
    diretorio_origem: Path,
    palavra_chave: str,
    *,
    midias_excluidas: set[Path] | None = None,
) -> Path:
    """Localiza uma midia local por substring, opcionalmente excluindo slots ja usados."""

    if not diretorio_origem.is_dir():
        raise FileNotFoundError(
            f"Diretorio de midias locais nao encontrado: {diretorio_origem}"
        )

    chave_normalizada = palavra_chave.strip().lower().replace(" ", "_")
    if not chave_normalizada:
        raise FileNotFoundError("busca_local nao pode ser vazia.")

    excluidas = {path.resolve() for path in (midias_excluidas or set())}
    for path in _midias_locais_disponiveis(diretorio_origem):
        if path.resolve() in excluidas:
            continue
        nome_normalizado = path.name.lower().replace(" ", "_")
        if chave_normalizada in nome_normalizado:
            return path

    raise FileNotFoundError(
        f"Nenhuma midia local encontrada para busca_local '{palavra_chave}' em "
        f"{diretorio_origem}."
    )


def _resolver_midia_local_para_slot(
    *,
    diretorio_origem: Path,
    busca_local: str | None,
    midias_usadas: set[Path],
) -> Path:
    """Escolhe uma midia local distinta para um slot de uma mesma cena."""

    if busca_local:
        try:
            return _buscar_midia_local_por_substring(
                diretorio_origem,
                busca_local,
                midias_excluidas=midias_usadas,
            )
        except FileNotFoundError:
            # Se uma tag repetida ja consumiu seu unico candidato, ainda e
            # preferivel preencher o slot com outra midia local a duplicar uma
            # foto no template multi-slot. Uma tag que nunca existiu continua
            # produzindo um erro claro, sem mascarar problema de entrada.
            if _existe_midia_local_para_busca(diretorio_origem, busca_local):
                return _sortear_midia_local_nao_usada(diretorio_origem, midias_usadas)
            raise

    return _sortear_midia_local_nao_usada(diretorio_origem, midias_usadas)


def _midias_locais_disponiveis(diretorio_origem: Path) -> list[Path]:
    if not diretorio_origem.is_dir():
        raise FileNotFoundError(
            f"Diretorio de midias locais nao encontrado: {diretorio_origem}"
        )
    return [
        path
        for path in sorted(diretorio_origem.iterdir(), key=lambda item: item.name.lower())
        if path.is_file() and path.suffix.lower() in LOCAL_MEDIA_EXTENSIONS
    ]


def _existe_midia_local_para_busca(
    diretorio_origem: Path,
    busca_local: str,
) -> bool:
    chave = busca_local.strip().lower().replace(" ", "_")
    return any(
        chave in path.name.lower().replace(" ", "_")
        for path in _midias_locais_disponiveis(diretorio_origem)
    )


def _sortear_midia_local_nao_usada(
    diretorio_origem: Path,
    midias_usadas: set[Path],
) -> Path:
    usadas_resolvidas = _caminhos_resolvidos(midias_usadas)
    candidatos = [
        path
        for path in _midias_locais_disponiveis(diretorio_origem)
        if path.resolve() not in usadas_resolvidas
    ]
    if not candidatos:
        raise FileNotFoundError(
            "Nao existem midias locais nao utilizadas suficientes para preencher os "
            "slots desta cena. Adicione mais arquivos visuais locais."
        )
    return random.choice(candidatos)


def _caminhos_resolvidos(midias: set[Path]) -> set[Path]:
    return {midia.resolve() for midia in midias}


def _copiar_midia_local(origem: Path, destino: Path) -> None:
    """Copia a midia local sem consumir o arquivo original da pasta de entrada."""

    if not origem.is_file() or origem.stat().st_size <= 0:
        raise FileNotFoundError(f"Midia local invalida ou vazia: {origem}")
    destino.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(origem, destino)
    except OSError as exc:
        raise RuntimeError(f"Nao foi possivel copiar midia local {origem}: {exc}") from exc
    if not destino.is_file() or destino.stat().st_size <= 0:
        raise RuntimeError(f"Copia de midia local invalida ou vazia: {destino}")


def _ler_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"JSON de entrada nao encontrado: {path}")

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON de entrada invalido: {exc}") from exc


def _extrair_temas(payload: Any, entrada_path: Path) -> list[TemaHorizontal]:
    if isinstance(payload, dict):
        for key in CONTAINER_KEYS:
            container = payload.get(key)
            if isinstance(container, list):
                shared = _campos_compartilhados(payload)
                return _temas_de_lista(container, entrada_path, shared=shared)

        if _tem_cenas(payload):
            return [_tema_from_metadata(payload, 1, entrada_path)]

        temas_por_chave = _temas_de_dict(payload)
        if temas_por_chave:
            return temas_por_chave

    if isinstance(payload, list):
        return _temas_de_lista(payload, entrada_path, shared={})

    raise ValueError("JSON horizontal precisa conter um tema com cenas ou uma lista de temas.")


def _temas_de_lista(
    itens: list[Any],
    entrada_path: Path,
    *,
    shared: dict[str, Any],
) -> list[TemaHorizontal]:
    if not itens:
        raise ValueError("JSON de entrada nao possui temas ou cenas.")

    if all(isinstance(item, dict) and _parece_cena(item) for item in itens):
        metadata = copy.deepcopy(shared)
        metadata.setdefault("tema", entrada_path.stem)
        metadata["cenas"] = copy.deepcopy(itens)
        return [_tema_from_metadata(metadata, 1, entrada_path)]

    temas: list[TemaHorizontal] = []
    for index, item in enumerate(itens, start=1):
        if not isinstance(item, dict) or not _tem_cenas(item):
            raise ValueError(f"item {index} precisa ser um objeto com cenas.")
        metadata = copy.deepcopy(shared)
        metadata.update(copy.deepcopy(item))
        temas.append(_tema_from_metadata(metadata, index, entrada_path))
    return temas


def _temas_de_dict(payload: dict[str, Any]) -> list[TemaHorizontal]:
    temas: list[TemaHorizontal] = []
    for index, (nome, value) in enumerate(payload.items(), start=1):
        if isinstance(value, dict) and _tem_cenas(value):
            metadata = copy.deepcopy(value)
            metadata.setdefault("tema", str(nome))
            temas.append(_tema_from_metadata(metadata, index, Path(f"{nome}.json")))
        elif isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            metadata = {"tema": str(nome), "cenas": copy.deepcopy(value)}
            temas.append(_tema_from_metadata(metadata, index, Path(f"{nome}.json")))
    return temas


def _tema_from_metadata(metadata: dict[str, Any], indice: int, entrada_path: Path) -> TemaHorizontal:
    metadata = copy.deepcopy(metadata)
    metadata["cenas"] = _cenas_da_metadata(metadata)
    tema = _tema_da_metadata(metadata, indice, entrada_path)
    return TemaHorizontal(indice=indice, tema=tema, metadata=metadata)


def _cenas_da_metadata(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    cenas = metadata.get("cenas")
    if cenas is None:
        cenas = metadata.get("scenes")
    if not isinstance(cenas, list) or not cenas:
        raise ValueError("metadata precisa conter uma lista nao vazia em cenas.")
    if not all(isinstance(cena, dict) for cena in cenas):
        raise ValueError("todas as cenas precisam ser objetos JSON.")

    # O contrato externo do T12 pode conter sub-cenas sem introduzir estado no
    # renderer: a normalizacao as transforma em cenas independentes antes de
    # qualquer resolucao de slots, TTS ou alinhamento Whisper.
    cenas_normalizadas = _normalizar_contrato_subcenas(cenas)
    for indice, cena in enumerate(cenas_normalizadas, start=1):
        cena["indice"] = indice
        template_id = _template_id(cena)
        cena["template_id"] = template_id

        texto = cena.get("texto")
        if not isinstance(texto, str) or not texto.strip():
            raise ValueError(f"cena {indice}: texto obrigatorio ausente ou vazio.")

        if template_id in TEMPLATES_COM_TEXTO:
            textos_tela: Any = None
            for key in (
                "textos_tela",
                "textos_na_tela",
                "texto_tela",
                "texto_na_tela",
                "textos",
            ):
                if key in cena:
                    textos_tela = cena[key]
                    break
            if isinstance(textos_tela, str):
                textos_validos = bool(textos_tela.strip())
            elif isinstance(textos_tela, list):
                textos_validos = bool(textos_tela) and all(
                    isinstance(item, str) for item in textos_tela
                )
                if template_id == 12:
                    textos_validos = textos_validos and any(
                        item.strip() for item in textos_tela
                    )
                else:
                    textos_validos = textos_validos and all(
                        item.strip() for item in textos_tela
                    )
            else:
                textos_validos = False
            if not textos_validos:
                raise ValueError(
                    f"cena {indice}: template {template_id} exige textos_tela nao vazio."
                )
            if template_id in {11, 12}:
                _validar_topicos_template_lista(textos_tela, indice, template_id)
            cena["textos_tela"] = textos_tela

        _slots_da_cena(cena)

    return cenas_normalizadas


def _normalizar_contrato_subcenas(
    cenas_brutas: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Expande sub-cenas T12 em cenas planas, cumulativas e indexadas.

    A entrada aninhada e apenas um contrato de autoria. Cada item retornado e
    uma cena autocontida para que preparo, TTS, Whisper, resume e workers de
    FFmpeg continuem stateless.
    """

    cenas_planas: list[dict[str, Any]] = []
    for indice_mae, cena_mae in enumerate(cenas_brutas, start=1):
        sub_cenas = cena_mae.get("sub_cenas")
        if sub_cenas is None:
            cenas_planas.append(copy.deepcopy(cena_mae))
            continue

        template_id = _template_id(cena_mae)
        if template_id != 12:
            raise ValueError(
                f"cena {indice_mae}: 'sub_cenas' e permitida somente no template 12."
            )
        if not isinstance(sub_cenas, list) or not sub_cenas:
            raise ValueError(f"cena {indice_mae}: 'sub_cenas' deve ser uma lista nao vazia.")
        if len(sub_cenas) > TEMPLATE_11_TOPICOS_EXIGIDOS:
            raise ValueError(
                f"cena {indice_mae}: template 12 aceita no maximo "
                f"{TEMPLATE_11_TOPICOS_EXIGIDOS} sub_cenas."
            )

        base = copy.deepcopy(cena_mae)
        base.pop("sub_cenas", None)
        topicos_acumulados: list[str] = []
        for indice_subcena, sub_cena in enumerate(sub_cenas, start=1):
            if not isinstance(sub_cena, dict):
                raise ValueError(
                    f"cena {indice_mae} sub_cena {indice_subcena}: deve ser um objeto JSON."
                )
            texto = sub_cena.get("texto")
            if not isinstance(texto, str) or not texto.strip():
                raise ValueError(
                    f"cena {indice_mae} sub_cena {indice_subcena}: "
                    "'texto' obrigatorio ausente ou vazio."
                )
            topico = sub_cena.get("topico")
            if not isinstance(topico, str) or not topico.strip():
                raise ValueError(
                    f"cena {indice_mae} sub_cena {indice_subcena}: "
                    "'topico' obrigatorio ausente ou vazio."
                )

            cena_plana = copy.deepcopy(base)
            for chave in (
                "busca_local",
                "buscas_locais",
                "prompt_ou_busca",
                "busca",
                "prompt",
                "midias",
                "medias",
            ):
                if chave in sub_cena:
                    cena_plana[chave] = copy.deepcopy(sub_cena[chave])

            topicos_acumulados.append(topico.strip())
            cena_plana["texto"] = texto.strip()
            cena_plana["textos_tela"] = [
                *topicos_acumulados,
                *[""] * (TEMPLATE_11_TOPICOS_EXIGIDOS - len(topicos_acumulados)),
            ]
            cenas_planas.append(cena_plana)

    for indice, cena in enumerate(cenas_planas, start=1):
        cena["indice"] = indice
    return cenas_planas


def _validar_topicos_template_lista(
    textos_tela: Any,
    indice_cena: int,
    template_id: int,
) -> None:
    """Valida a lista de topicos dos templates 11 e 12.

    A factory desenha cada item em uma linha/posicao propria. Aceitar uma
    string multilinha aqui tornaria a quantidade de topicos ambigua e poderia
    quebrar a organizacao visual, por isso o contrato exige quatro strings
    explicitas no JSON.
    """

    if not isinstance(textos_tela, list):
        raise ValueError(
            f"cena {indice_cena}: template {template_id} exige 'textos_tela' como lista "
            f"com exatamente {TEMPLATE_11_TOPICOS_EXIGIDOS} topicos explicitos "
            "(itens 1, 2, 3 e 4)."
        )
    if len(textos_tela) != TEMPLATE_11_TOPICOS_EXIGIDOS:
        raise ValueError(
            f"cena {indice_cena}: template {template_id} exige exatamente "
            f"{TEMPLATE_11_TOPICOS_EXIGIDOS} topicos explicitos em 'textos_tela'; "
            f"recebidos {len(textos_tela)}."
        )
    if template_id == 12:
        encontrou_lacuna = False
        for topico in textos_tela:
            if not topico.strip():
                encontrou_lacuna = True
            elif encontrou_lacuna:
                raise ValueError(
                    f"cena {indice_cena}: template 12 exige topicos acumulados sem lacunas."
                )
        if not textos_tela[0].strip():
            raise ValueError(
                f"cena {indice_cena}: template 12 exige ao menos o primeiro topico."
            )
    elif any(not topico.strip() for topico in textos_tela):
        raise ValueError(
            f"cena {indice_cena}: template {template_id} exige topicos nao vazios."
        )
    if any("\n" in item or "\r" in item for item in textos_tela):
        raise ValueError(
            f"cena {indice_cena}: cada topico do template {template_id} deve ocupar uma unica linha."
        )


def _tem_cenas(value: dict[str, Any]) -> bool:
    cenas = value.get("cenas")
    if cenas is None:
        cenas = value.get("scenes")
    return isinstance(cenas, list) and bool(cenas)


def _parece_cena(value: dict[str, Any]) -> bool:
    chaves_cena = {
        "texto",
        "template_id",
        "fonte_midia",
        "prompt_ou_busca",
        "busca_local",
        "buscas_locais",
        "midias",
    }
    return bool(chaves_cena.intersection(value.keys()))


def _tema_da_metadata(metadata: dict[str, Any], indice: int, entrada_path: Path) -> str:
    for key in ("tema", "nome_do_tema", "titulo", "title", "nome", "assunto"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if entrada_path.stem:
        return entrada_path.stem
    return f"tema_{indice:03d}"


def _campos_compartilhados(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(value) for key, value in payload.items() if key not in CONTAINER_KEYS}


def _salvar_metadata(path: Path, metadata: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _slug(value: str, *, fallback: str) -> str:
    slug = normalizar_ascii(value)
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    slug = slug.strip("_")
    return slug or fallback


def _int_positivo(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        numero = value
    elif isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
        numero = int(value.strip())
    else:
        return None
    return numero if numero > 0 else None


def main() -> int:
    args = parse_args()
    logger = get_logger(__name__)

    try:
        resumo = preparar_horizontal(args.json_entrada, args.nicho)
    except Exception as exc:
        logger.error("Horizontal: falha geral no preparo: %s", exc)
        return 1

    print(json.dumps(resumo, indent=2, ensure_ascii=False))
    return 0 if all(item.get("status") == "ok" for item in resumo["resultados"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
