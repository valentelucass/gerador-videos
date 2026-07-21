"""Prepara o roteiro curto com os assets atuais e um mix de SFX temático."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "roteiro_animais_fundo_do_mar_1min.json"
ASSETS = ROOT / "assets" / "images"
ASSET_PATTERNS = [
    "Ocean_abyss*", "Tiny_submarine*", "Eye_blinks*", "Colossal_underwater*", "Diver_in*",
    "Predator_emerging*", "Bioluminescent_lure*", "Fish_swims*", "Anglerfish_mouth*", "Abyssal_creatures*",
    "Translucent*", "Eel_folds*", "Giant_isopod_moving*", "Viperfish_with*", "Viperfish_emerging*",
    "Abyssal_predator_montage*", "Row_of_fangs*", "Robotic_vehicle*", "Luminous_sonar*", "Ghostly_jellyfish*",
]


def resolve(pattern: str) -> str:
    matches = list(ASSETS.glob(pattern))
    if len(matches) != 1:
        raise FileNotFoundError(f"Asset esperado para {pattern!r}: {[item.name for item in matches]}")
    return matches[0].name


def main() -> None:
    payload = json.loads(SCRIPT.read_text(encoding="utf-8"))
    scenes = [scene for block in payload["blocks"] for scene in block["scenes"]]
    if len(scenes) != len(ASSET_PATTERNS):
        raise ValueError("O roteiro curto deve ter exatamente 20 cenas.")
    for scene, pattern in zip(scenes, ASSET_PATTERNS, strict=True):
        scene["image"] = resolve(pattern)

    # Clique imediatamente na abertura. Os demais cliques surgem somente quando
    # uma nova seção narrativa entra, acompanhados por whoosh de transição.
    scenes[0]["sounds"] = {"transition": ["whoosh_soft"], "context": {"type": "click", "at": "start"}}
    for index, effect in {4: "whoosh_cinematic", 9: "whoosh_cinematic", 12: "whoosh_fast", 14: "whoosh_cinematic"}.items():
        scenes[index]["sounds"] = {"transition": [effect, "click"], "context": None}
    scenes[6]["sounds"] = {"transition": ["whoosh_soft"], "context": None}
    scenes[7]["sounds"] = {"transition": ["whoosh_fast"], "context": None}
    scenes[18]["sounds"] = {"transition": ["whoosh_soft"], "context": None}

    # Texto só entra quando a narração inicia um tópico específico.
    for scene in scenes:
        scene.pop("annotation", None)
    # A CTA inicial é a exceção deliberada à regra dos dez segundos: a narração
    # pede inscrição aqui, portanto entra texto, emoji e clique.
    scenes[2]["sounds"] = {"transition": [], "context": {"type": "click", "at": "start"}}
    scenes[2]["annotation"] = {
        "lines": ["DEIXE O LIKE", "E SE INSCREVA"],
        "at": "start",
        "emoji": "👍",
    }
    scenes[2]["transition"].pop("impact", None)
    scenes[5]["annotation"] = {"lines": ["PEIXE-PESCADOR"], "at": "start"}
    scenes[10]["annotation"] = {"lines": ["POLVO-DUMBO"], "at": "start"}
    scenes[13]["annotation"] = {"lines": ["PEIXE-VÍBORA"], "at": "start"}
    # Fechamento: clique no início e som de notificação no sino.
    scenes[19]["sounds"] = {"transition": [], "context": {"type": "click", "at": "start"}}
    scenes[19]["annotation"] = {
        "lines": ["SE INSCREVA", "PARA MAIS"],
        "at": "start",
        "emoji": "🔔",
    }

    SCRIPT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Roteiro curto preparado: {SCRIPT}")


if __name__ == "__main__":
    main()
