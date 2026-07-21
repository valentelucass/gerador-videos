"""Ajusta a narração do roteiro abissal para cinco minutos acústicos."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "roteiro_animais_abissais_assustadores_5min.json"
ADDITIONS = [
    "No abismo, até o silêncio parece esconder algo pronto para atacar.",
    "Ali, poucos segundos podem custar a única refeição possível.",
    "Aquela luz não oferece comida; ela anuncia um fim imediato.",
    "Quem se aproxima entrega ao predador toda chance de escapar.",
    "Cada movimento aparece quando já é tarde demais para fugir.",
    "Sua vantagem nasce onde os outros enxergam apenas vazio.",
    "Uma refeição perdida pode significar semanas sem outra oportunidade.",
    "Até a lentidão desses animais esconde uma eficiência assustadora.",
    "É um sobrevivente antigo, adaptado a lugares onde humanos raramente chegam.",
    "Esse encontro mostra como ainda conhecemos pouco esse mundo.",
    "A fome transforma limites do corpo em soluções aparentemente impossíveis.",
    "Cada mergulho prova que o desconhecido continua depois da luz.",
    "Quantas criaturas ainda não vimos sob essa escuridão absoluta?",
]


def main() -> None:
    payload = json.loads(SCRIPT.read_text(encoding="utf-8"))
    blocks = payload["blocks"]
    if len(blocks) != len(ADDITIONS):
        raise ValueError("A quantidade de blocos diverge das extensões narrativas.")
    for block, addition in zip(blocks, ADDITIONS, strict=True):
        if addition not in block["text"]:
            block["text"] = block["text"].rstrip() + " " + addition
    SCRIPT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Roteiro estendido: {SCRIPT}")


if __name__ == "__main__":
    main()
