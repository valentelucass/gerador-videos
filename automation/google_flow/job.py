"""Leitura do contrato TXT do Google Flow.

O JSON editorial não participa desta esteira. O TXT é a única fonte de prompts
e precisa identificar cada cena de modo estável para que o checkpoint possa
retomar um lote sem duplicar créditos.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


_SCENE = re.compile(r"^\s*\[\[\s*SCENE\s+(\d{1,4})\s*\]\]\s*$", re.I | re.M)
_END = re.compile(r"^\s*\[\[\s*/SCENE\s*\]\]\s*$", re.I | re.M)
_ANIMATION = re.compile(r"^\s*ANIMATION\s*:\s*(.+)$", re.I | re.M)


@dataclass(frozen=True)
class FlowScene:
    number: int
    image_prompt: str
    animation_prompt: str

    @property
    def id(self) -> str:
        return f"scene_{self.number:02d}"


def load_scenes(path: Path) -> list[FlowScene]:
    """Lê blocos ``[[SCENE NN]]`` sem inferir nem criar texto de contingência."""
    if not path.is_file():
        raise FileNotFoundError(f"TXT do Flow não encontrado: {path}")
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    starts = list(_SCENE.finditer(text))
    if not starts:
        raise ValueError(
            "O TXT precisa usar blocos [[SCENE 01]] ... [[/SCENE]]. "
            "Cada bloco deve conter IMAGE: e ANIMATION:."
        )
    scenes: list[FlowScene] = []
    for index, start in enumerate(starts):
        stop = starts[index + 1].start() if index + 1 < len(starts) else len(text)
        body = text[start.end():stop]
        end = _END.search(body)
        if end is None:
            raise ValueError(f"{path.name}: falta [[/SCENE]] para a cena {start.group(1)}.")
        body = body[:end.start()].strip()
        image_match = re.search(r"^\s*IMAGE\s*:\s*(.+?)(?=^\s*ANIMATION\s*:|\Z)", body, re.I | re.M | re.S)
        animation_match = _ANIMATION.search(body)
        if image_match is None or animation_match is None:
            raise ValueError(f"{path.name}: a cena {start.group(1)} exige IMAGE: e ANIMATION:.")
        image_prompt = image_match.group(1).strip()
        animation_prompt = animation_match.group(1).strip()
        if not image_prompt or not animation_prompt:
            raise ValueError(f"{path.name}: a cena {start.group(1)} possui prompt vazio.")
        scenes.append(FlowScene(int(start.group(1)), image_prompt, animation_prompt))
    numbers = [scene.number for scene in scenes]
    if len(numbers) != len(set(numbers)):
        raise ValueError("O TXT possui números de cena repetidos.")
    return sorted(scenes, key=lambda scene: scene.number)


def scene_batch_prompt(batch: list[FlowScene]) -> str:
    """Monta um pacote operacional de até 25 cenas para o chat já configurado."""
    first, last = batch[0].number, batch[-1].number
    lines = [
        f"PRODUCTION GROUP {first:02d}-{last:02d}. Follow all production instructions already established in this chat.",
        "Process this group as consecutive sub-batches of five scenes. For each sub-batch: generate five stills, generate and visibly validate the five corresponding videos, correct any failure before proceeding, then delete only the five temporary stills and preserve all final videos.",
        "Read your own generation results and any error messages before every decision. Never skip a failed item or advance while an error remains.",
        f"After every scene in this group is finished, every video is visibly successful, and all temporary stills are removed, reply exactly: FLOW_GROUP_COMPLETE_{first:02d}_{last:02d}.",
    ]
    for scene in batch:
        lines.extend(("", f"SCENE {scene.number:02d}", f"IMAGE: {scene.image_prompt}", f"ANIMATION: {scene.animation_prompt}"))
    return "\n".join(lines)
