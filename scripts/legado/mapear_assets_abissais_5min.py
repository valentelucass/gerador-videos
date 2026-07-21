"""Associa o roteiro longo aos 65 assets aprovados pelo usuário."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "roteiro_animais_abissais_assustadores_5min.json"
ASSETS = ROOT / "assets" / "images"

PATTERNS = [
    "Ocean_abyss*", "Abyssal_creatures*", "Tiny_submarine*", "Submersible_headlights*", "Eye_blinks*",
    "Submarine_depth*", "Colossal_underwater*", "Diver_in*", "Cold_current*", "Translucent*",
    "Predator_emerging*", "Bioluminescent_lure*", "Anglerfish_teeth*", "Fish_swims*", "Predator_shadow*",
    "Anglerfish_mouth*", "Gigantic_anglerfish*", "Articulated_jaw*", "Particle_cloud*", "Anglerfish_returns*",
    "Viperfish_with*", "Row_of_fangs*", "Viperfish_vanishing*", "Viperfish_emerging*", "Viperfish_shark*",
    "Black_dragonfish*", "Red_light*", "Dragonfish_hunting*", "Small_prey_eye*", "Creature_cornered*",
    "Pelican_eel_in*", "Pelican_eel_opens*", "Jaw_swallowing*", "Eel_folds*", "Eel's_luminous*",
    "Giant_isopod_walks*", "Carapace*", "Isopod_next*", "Giant_isopod_moving*", "Giant_isopods_near*",
    "Snake-shark_slithers*", "Viperfish_teeth_open*", "Snake_shark_attacking*", "Snake-shark_disappearing*", "Predator's_belly*",
    "Giant_Lula*", "Giant_squid_eye*", "Giant_squid_tentacles*", "Submarine_passing_giant_squid*", "Giant_squid_in_deep*",
    "Black_swallower_fish_large*", "Black-swallower_fish_engulfing*", "Dark_predator_body*", "Black_swallower_fish_carrying*", "Small_fish_avoid*",
    "Robotic_vehicle*", "Mechanical_arm*", "Luminous_sonar*", "Unknown_shadow*", "Submarine_emerging*",
    "Abyssal_predator_montage*", "Bioluminescent_eye*", "Ghostly_jellyfish*", "Submarine_silhouette*", "Abyssal_horror_documentary_final*",
]


def resolve(pattern: str) -> str:
    matches = list(ASSETS.glob(pattern))
    if len(matches) != 1:
        raise FileNotFoundError(f"Esperava um asset para {pattern!r}; encontrados: {[item.name for item in matches]}")
    return matches[0].name


def main() -> None:
    payload = json.loads(SCRIPT.read_text(encoding="utf-8"))
    scenes = [scene for block in payload["blocks"] for scene in block["scenes"]]
    if len(scenes) != len(PATTERNS):
        raise ValueError(f"O roteiro possui {len(scenes)} cenas; o mapa possui {len(PATTERNS)} assets.")
    resolved = [resolve(pattern) for pattern in PATTERNS]
    if len(resolved) != len(set(resolved)):
        raise ValueError("O mapa reutiliza um mesmo asset; cada cena precisa de imagem exclusiva.")
    for scene, asset_name in zip(scenes, resolved, strict=True):
        scene["image"] = asset_name
    SCRIPT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(resolved)} assets associados a {SCRIPT.name}.")


if __name__ == "__main__":
    main()
