from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from pathlib import Path

from .config import BACKGROUND_DIR, DEFAULT_BACKGROUND_NAME, IMAGE_DIR, MUSIC_DIR, SOUND_DIR
from .models import Script

MEDIA_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg"}
# Aceita tanto o nome sugerido "1 - cena" quanto a variação que o Flow
# costuma baixar, como "1_-_cena_01.png_202607...".
IMAGE_ID_PREFIX = re.compile(r"^\s*(\d+)(?:\s*[-_]\s*)+(?:[^\s].*)?$")

ASSET_NAME_STOP_WORDS = frozenset({
    "a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "na", "no", "com", "sem",
    "uma", "um", "para", "por", "the", "and", "of", "in", "with", "on", "at", "from", "image",
    "imagem", "horizontal", "cinematografico", "cinematografica", "documental", "realista", "detalhado",
    # Termos amplos demais para decidir qual foto pertence a uma cena. Eles
    # continuam legíveis no nome, mas não podem vencer uma correspondência
    # específica como "E-Type racing mountain" ou "five cars crossroads".
    "car", "cars", "classic", "british", "english", "road", "street", "country", "body",
    "arqueologia", "archaeology", "arqueologo", "archaeologist",
})

# Vocabulário que permite comparar o brief em português com os nomes
# autodescritivos que o Google Flow costuma baixar em inglês. O pareamento
# nunca usa a ordem de upload.
VISUAL_SYNONYMS = (
    {"farol", "lighthouse"}, {"ilha", "island"}, {"rochosa", "rocky"},
    {"onda", "ondas", "wave"}, {"penhasco", "cliff"}, {"faroleiro", "keeper"},
    {"lanterna", "lantern"}, {"navio", "ship"}, {"tripulacao", "crew"},
    {"luz", "light"}, {"apagado", "dark", "unlit"}, {"abastecimento", "supply"},
    {"foguete", "rocket"}, {"homem", "man"}, {"cozinha", "kitchen"},
    {"mecanismo", "mechanism"}, {"lente", "lens"}, {"corrimao", "railing"},
    {"corda", "rope"}, {"danificado", "damaged"}, {"tres", "three"},
    {"equipamento", "equipment", "equi"}, {"oceano", "ocean"}, {"afundar", "sink"},
    {"abandonado", "abandoned"}, {"brilhando", "shining"},
    {"segurando", "holding"}, {"observa", "observando", "observing", "observam"},
    {"distante", "distant"}, {"prender", "prendem", "proteger", "protect", "securing"},
    {"arqueologo", "archaeologist", "arqueologia", "archaeology"},
    {"artefato", "artifact", "vaso", "vessel"}, {"laboratorio", "lab"},
    {"mesa", "table"}, {"fragmento", "shard"}, {"gobekli"}, {"pilar", "pillar"},
    {"prehistorico", "prehistoric"}, {"comunidade", "community"},
    {"mergulhador", "diver"}, {"naufragio", "wreck"}, {"anticitera", "antikythera"},
    {"engrenagem", "gear"}, {"terracota", "terracotta"}, {"guerreiro", "warrior"},
    {"otzi", "iceman"}, {"gelo", "ice"}, {"geleira", "glacier"},
    {"corpo", "body"}, {"preservado", "preserved"}, {"montagem", "collage"},
    {"galeria", "gallery"}, {"museu", "museum"},
    # Termos recorrentes nos nomes exportados pelo Flow para documentários de
    # lugares reais: o roteiro pode estar em português e o arquivo em inglês.
    {"solo", "terra", "ground", "earth", "soil"},
    {"rachado", "rachada", "rachaduras", "fissura", "fissuras", "cracked", "fissure", "fissures"},
    {"estrada", "rua", "road", "street"},
    {"fumaca", "smoke"}, {"selado", "selada", "fechado", "bloqueado", "sealed", "blocking", "blocked"},
    {"passagem", "corredor", "tunel", "passage", "tunnel"}, {"grade", "grate", "gate"},
    {"osso", "ossos", "cranio", "cranios", "bone", "bones", "skull", "skulls", "femur", "femurs"},
    {"boneca", "bonecas", "doll", "dolls"},
    {"two", "ii", "second"},
    {"turning", "turn", "curve", "cornering"},
    {"monocoque", "chassis", "structure"},
    {"model", "models", "evolution", "later", "beside"},
    {"collection", "rare", "parked", "group"},
    {"show", "displayed", "exhibition", "amsterdam"},
    {"coachbuilder", "workshop", "leaving", "exit"},
    {"arrival", "emerging", "darkness", "hotel"},
)
GENERIC_CONCEPTS = frozenset(
    f"semantic_concept_{index}"
    for index, group in enumerate(VISUAL_SYNONYMS)
    if "archaeology" in group
)


def image_id_from_filename(source_name: str) -> int | None:
    """Extrai o prefixo opcional ``ID - descrição`` de um asset."""
    match = IMAGE_ID_PREFIX.match(Path(source_name).stem)
    return int(match.group(1)) if match else None


def required_asset_name(scene: object) -> str:
    """Nome-modelo mostrado ao operador e usado no prompt do Google Flow."""
    normalized = unicodedata.normalize("NFKD", scene.visual.subject).encode("ascii", "ignore").decode("ascii").casefold()
    terms = [
        term for term in re.findall(r"[a-z0-9]+", normalized)
        if len(term) >= 3 and term not in ASSET_NAME_STOP_WORDS
    ]
    slug = "-".join(terms[:5]) or "imagem-da-cena"
    return f"{scene.image_id} - {slug}.png"


def _normalized_terms(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").casefold()
    terms = {
        term for term in re.findall(r"[a-z0-9]+", normalized)
        if len(term) >= 3 and term not in ASSET_NAME_STOP_WORDS
    }
    terms.update(term[:-1] for term in tuple(terms) if term.endswith("s") and len(term) > 4)
    expanded = set(terms)
    for index, group in enumerate(VISUAL_SYNONYMS):
        if terms.intersection(group):
            expanded.add(f"semantic_concept_{index}")
    return expanded


def _scene_asset_score(block: object, scene: object, source_name: str) -> int:
    visual = scene.visual
    asset_terms = _normalized_terms(Path(source_name).stem)
    primary_terms = _normalized_terms(" ".join((visual.subject, visual.action, visual.setting)))
    asset_key = getattr(scene, "asset_key", None)
    key_terms = _normalized_terms(asset_key.replace("-", " ")) if asset_key else set()
    # A chave é explícita e está no mesmo idioma dos nomes gerados pelo Flow;
    # por isso vale mais que coincidências acidentais do texto narrado.
    score = (
        10 * len(key_terms.intersection(asset_terms))
        + 3 * len(primary_terms.intersection(asset_terms))
    )
    # Uma cena de montagem pode ser exportada como uma lista de lugares no
    # nome do arquivo, sem usar literalmente a palavra "montage". Quando o
    # arquivo confirma três ou mais elementos visuais, ele é uma escolha
    # muito mais segura para a montagem final do que para uma cena isolada.
    if "montage" in key_terms and len(primary_terms.intersection(asset_terms)) >= 3:
        score += 20
    # Um arquivo explicitamente nomeado como montagem não deve roubar a
    # ilustração de uma cena isolada só por compartilhar um elemento (ex.:
    # "bone tunnel" para catacumbas). A cena de montagem possui esse termo em
    # ``asset_key`` e continua sendo a candidata natural.
    if "montage" in asset_terms and "montage" not in key_terms:
        score -= 25
    # "Monocoque" e "chassis" são equivalentes estruturais, mas o primeiro
    # costuma aparecer no roteiro e o segundo no nome exportado pela imagem.
    # Essa ligação específica evita que uma foto comparativa seja confundida
    # com uma foto de modelos estacionados.
    if "monocoque" in key_terms and "chassis" in asset_terms:
        score += 15
    if "coachbuilder" in key_terms and "leaving" in asset_terms:
        score += 15
    if "curve" in key_terms and "turning" in asset_terms:
        score += 15
    return score


def _has_specific_visual_evidence(block: object, scene: object, source_name: str) -> bool:
    visual = scene.visual
    asset_terms = _normalized_terms(Path(source_name).stem)
    asset_key = getattr(scene, "asset_key", None)
    if asset_key and _normalized_terms(asset_key.replace("-", " ")).intersection(asset_terms):
        return True
    primary_terms = _normalized_terms(" ".join((visual.subject, visual.action, visual.setting)))
    evidence = primary_terms.intersection(asset_terms)
    return bool(evidence - GENERIC_CONCEPTS - {"arqueologia", "archaeology", "arqueologo", "archaeologist"})


def _assign_closed_batch(
    scenes: list[tuple[object, object]], source_names: list[str],
) -> dict[str, str]:
    """Seleciona as melhores imagens de um lote completo, com sobras opcionais."""
    scene_count = len(scenes)
    source_count = len(source_names)
    if source_count < scene_count:
        raise ValueError("O pareamento global exige ao menos uma imagem por cena.")
    scores = [
        [_scene_asset_score(block, scene, source_name) for source_name in source_names]
        for block, scene in scenes
    ]
    maximum = max(max(row) for row in scores)
    # Algoritmo húngaro: escolhe o melhor conjunto global, em vez de deixar
    # uma decisão local reservar uma imagem para a cena errada.
    u = [0] * (scene_count + 1)
    v = [0] * (source_count + 1)
    matching = [0] * (source_count + 1)
    previous = [0] * (source_count + 1)
    for scene_index in range(1, scene_count + 1):
        matching[0] = scene_index
        column = 0
        minimum = [float("inf")] * (source_count + 1)
        used = [False] * (source_count + 1)
        while True:
            used[column] = True
            row = matching[column]
            delta = float("inf")
            next_column = 0
            for candidate in range(1, source_count + 1):
                if used[candidate]:
                    continue
                cost = (maximum - scores[row - 1][candidate - 1]) * 1000 + candidate
                value = cost - u[row] - v[candidate]
                if value < minimum[candidate]:
                    minimum[candidate] = value
                    previous[candidate] = column
                if minimum[candidate] < delta:
                    delta = minimum[candidate]
                    next_column = candidate
            for candidate in range(source_count + 1):
                if used[candidate]:
                    u[matching[candidate]] += delta
                    v[candidate] -= delta
                else:
                    minimum[candidate] -= delta
            column = next_column
            if matching[column] == 0:
                break
        while column:
            parent = previous[column]
            matching[column] = matching[parent]
            column = parent

    return {
        scenes[matching[source_index] - 1][1].image: source_names[source_index - 1]
        for source_index in range(1, source_count + 1)
        if matching[source_index]
    }


def semantic_image_bindings(
    script: Script,
    image_bindings: Mapping[str, str] | None,
    uploaded_images: list[str],
) -> dict[str, str]:
    """Completa vínculos pelo ID opcional ou pelo conteúdo, nunca pela ordem."""
    completed = dict(image_bindings or {})
    scenes = [(block, scene) for block in script.blocks for scene in block.scenes]
    used_sources = set(completed.values())

    candidates = sorted({
        name for name in uploaded_images
        if name not in used_sources and (IMAGE_DIR / name).is_file() and Path(name).suffix.lower() in MEDIA_EXTENSIONS
    })

    # Quando o operador/Flow preserva o prefixo, ele é uma associação direta.
    for _, scene in scenes:
        if scene.image in completed:
            continue
        matches = [name for name in candidates if image_id_from_filename(name) == scene.image_id]
        if len(matches) == 1:
            completed[scene.image] = matches[0]
            used_sources.add(matches[0])

    remaining_scenes = [(block, scene) for block, scene in scenes if scene.image not in completed]
    remaining_sources = [name for name in candidates if name not in used_sources]
    if remaining_scenes and len(remaining_sources) >= len(remaining_scenes):
        completed.update(_assign_closed_batch(remaining_scenes, remaining_sources))
        return completed

    # O Flow pode salvar com seu próprio nome. Nesse caso usamos somente a
    # descrição autogerada e o brief visual, com associação um-para-um.
    assigned_scenes = set(completed)
    assigned_sources = set(used_sources)
    while True:
        # Recalcular após cada decisão impede que uma cena secundária reserve
        # uma foto que outra cena descreve muito melhor.
        scores: dict[tuple[str, str], int] = {}
        scenes_by_image = {scene.image: (block, scene) for block, scene in scenes if scene.image not in assigned_scenes}
        for expected_name, (block, scene) in scenes_by_image.items():
            for source_name in candidates:
                if source_name in assigned_sources or not _has_specific_visual_evidence(block, scene, source_name):
                    continue
                score = _scene_asset_score(block, scene, source_name)
                if score >= 3:
                    scores[(expected_name, source_name)] = score
        if not scores:
            break

        proposed: list[tuple[int, str, str]] = []
        for expected_name in scenes_by_image:
            scene_scores = [(score, source_name) for (image, source_name), score in scores.items() if image == expected_name]
            if not scene_scores:
                continue
            top_score = max(score for score, _ in scene_scores)
            top_sources = [source_name for score, source_name in scene_scores if score == top_score]
            if len(top_sources) != 1:
                continue
            source_name = top_sources[0]
            source_scores = [(score, image) for (image, source), score in scores.items() if source == source_name]
            source_top = max(score for score, _ in source_scores)
            if source_top == top_score and sum(score == source_top for score, _ in source_scores) == 1:
                proposed.append((top_score, expected_name, source_name))
        if not proposed:
            break
        score, expected_name, source_name = max(proposed, key=lambda item: (item[0], item[1], item[2]))
        completed[expected_name] = source_name
        assigned_scenes.add(expected_name)
        assigned_sources.add(source_name)

    # Em um lote fechado, depois das associações inequívocas, podem sobrar
    # cenas visualmente próximas (por exemplo, três planos de barraca na neve).
    # Se o número de arquivos e de cenas restantes for idêntico, distribuímos
    # os melhores candidatos em ordem narrativa. Isso evita bloquear toda a
    # renderização por empates nominais, sem recorrer à ordem de upload.
    remaining_scenes = [
        (block, scene) for block, scene in scenes
        if scene.image not in assigned_scenes
    ]
    remaining_sources = [name for name in candidates if name not in assigned_sources]
    if len(remaining_scenes) == len(remaining_sources):
        for block, scene in remaining_scenes:
            ranked = sorted(
                (
                    (_scene_asset_score(block, scene, source_name), source_name)
                    for source_name in remaining_sources
                    if _has_specific_visual_evidence(block, scene, source_name)
                    and _scene_asset_score(block, scene, source_name) >= 3
                ),
                key=lambda item: (-item[0], item[1]),
            )
            if not ranked:
                break
            _, source_name = ranked[0]
            completed[scene.image] = source_name
            assigned_sources.add(source_name)
            remaining_sources.remove(source_name)

    return completed


def list_media(directory: Path, extensions: set[str]) -> list[str]:
    if not directory.exists():
        return []
    return sorted(item.name for item in directory.iterdir() if item.is_file() and item.suffix.lower() in extensions)


def default_background_name() -> str | None:
    """Retorna o fundo aprovado, com fallback para o primeiro fundo disponível."""
    approved = BACKGROUND_DIR / DEFAULT_BACKGROUND_NAME
    if approved.is_file() and approved.suffix.lower() in MEDIA_EXTENSIONS:
        return approved.name
    available = list_media(BACKGROUND_DIR, MEDIA_EXTENSIONS)
    return available[0] if available else None


def catalog() -> dict[str, object]:
    return {
        "images": list_media(IMAGE_DIR, MEDIA_EXTENSIONS),
        "backgrounds": list_media(BACKGROUND_DIR, MEDIA_EXTENSIONS),
        "default_background": default_background_name(),
        "music": list_media(MUSIC_DIR, AUDIO_EXTENSIONS),
        "sounds": list_media(SOUND_DIR, AUDIO_EXTENSIONS),
    }


def words(text: str) -> int:
    return len(re.findall(r"\b[\wÀ-ÿ'-]+\b", text))


def google_flow_prompt(script: Script, block_id: str, scene_id: str) -> str:
    scene = next(scene for block in script.blocks if block.id == block_id for scene in block.scenes if scene.id == scene_id)
    visual = scene.visual
    return (
        f"{visual.subject}. {visual.action}. {visual.setting}. "
        f"{visual.framing}. {visual.details}. "
        "Imagem ilustrativa horizontal para documentário do YouTube, sem palavras, sem legendas, sem logotipos, sem marcas d'água. "
        f"Referência editorial desta imagem: ID {scene.image_id}. "
        + (f"Use estes termos visuais em inglês: {scene.asset_key}. " if scene.asset_key else "")
        + f"Sugestão de nome ao baixar: "
        f"'{required_asset_name(scene)}'; se o Google Flow usar outro nome descritivo, mantenha-o."
    )


def expected_scene_images(script: Script) -> list[str]:
    """Lista os nomes editoriais das imagens na ordem declarada pelo JSON."""
    return [scene.image for block in script.blocks for scene in block.scenes]


def resolve_scene_image_sources(
    script: Script,
    image_bindings: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Resolve nome do JSON -> arquivo físico, sem alterar o roteiro.

    A chave sempre é ``scene.image`` do roteiro. Entradas omitidas continuam
    usando o mesmo nome como fonte, o que conserva o fluxo de arquivos já
    nomeados como ``cena_01.png``. A validação deliberadamente rejeita chaves
    estranhas para nunca aceitar um vínculo que o compositor ignoraria.
    """
    expected = expected_scene_images(script)
    expected_set = set(expected)
    bindings = dict(image_bindings or {})
    unknown = sorted(set(bindings) - expected_set)
    if unknown:
        raise ValueError(
            "image_bindings contém nome(s) que não aparecem no roteiro: "
            + ", ".join(unknown)
        )

    resolved: dict[str, str] = {}
    scenes = [scene for block in script.blocks for scene in block.scenes]
    for scene in scenes:
        expected_name = scene.image
        source_name = bindings.get(expected_name, expected_name)
        if Path(source_name).name != source_name or source_name in {".", ".."}:
            raise ValueError(
                f"O vínculo de {expected_name} precisa apontar somente para o nome de um arquivo."
            )
        if Path(source_name).suffix.lower() not in MEDIA_EXTENSIONS:
            accepted = ", ".join(sorted(MEDIA_EXTENSIONS))
            raise ValueError(
                f"O vínculo de {expected_name} aponta para {source_name}, mas a cena aceita somente {accepted}."
            )
        resolved[expected_name] = source_name
    return resolved


def missing_scene_images(resolved_sources: Mapping[str, str]) -> list[str]:
    """Retorna os nomes editoriais cuja fonte física ainda não foi enviada."""
    return sorted(
        expected_name
        for expected_name, source_name in resolved_sources.items()
        if not (IMAGE_DIR / source_name).is_file()
    )


def validate_script(
    script: Script,
    image_bindings: Mapping[str, str] | None = None,
) -> dict[str, object]:
    block_ids = [block.id for block in script.blocks]
    scene_ids = [scene.id for block in script.blocks for scene in block.scenes]
    errors: list[str] = []
    if len(block_ids) != len(set(block_ids)):
        errors.append("IDs de blocos precisam ser únicos.")
    if len(scene_ids) != len(set(scene_ids)):
        errors.append("IDs de cenas precisam ser únicos no roteiro.")
    image_ids = [scene.image_id for block in script.blocks for scene in block.scenes]
    expected_image_ids = list(range(1, len(image_ids) + 1))
    if image_ids != expected_image_ids:
        errors.append(
            "image_id deve ser sequencial na ordem das cenas: 1, 2, 3, … sem repetição ou salto."
        )

    blocks = []
    for block in script.blocks:
        if len(block.scenes) != 1:
            errors.append(
                f"O bloco {block.id} tem {len(block.scenes)} cenas; "
                "use uma cena por bloco para manter a imagem sincronizada à fala."
            )
        blocks.append({
            "id": block.id,
            "word_count": words(block.text),
            "scene_count": len(block.scenes),
            "status": "ok",
        })

    try:
        resolved_sources = resolve_scene_image_sources(script, image_bindings)
    except ValueError as exc:
        errors.append(str(exc))
        resolved_sources = {
            expected_name: expected_name
            for expected_name in expected_scene_images(script)
        }

    missing_images = missing_scene_images(resolved_sources)
    return {
        "valid": not errors,
        "errors": errors,
        "blocks": blocks,
        # A lista usa o nome do JSON para o painel mostrar exatamente qual
        # cena ainda precisa de um vínculo, mesmo que o arquivo real tenha
        # outro nome.
        "missing_images": missing_images,
        "resolved_image_sources": resolved_sources,
        "required_asset_names": {
            scene.image: required_asset_name(scene)
            for block in script.blocks for scene in block.scenes
        },
    }
