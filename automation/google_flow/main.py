from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from .browser import persistent_page
from .checkpoint import FlowCheckpoint
from .config import FlowSettings
from .job import load_scenes
from .workflow import FlowWorkflow


async def run(txt: Path, state_dir: Path, await_selection: bool = False) -> None:
    scenes = load_scenes(txt)
    settings = FlowSettings.load(state_dir)
    settings.ensure_directories()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    checkpoint = FlowCheckpoint(state_dir / "flow_checkpoint.json", source=txt, total=len(scenes))
    async with persistent_page(settings) as (_, page):
        await FlowWorkflow(page, settings, checkpoint, logging.getLogger("google_flow_automation"), state_dir, await_selection).run(scenes)
    print(f"Envio concluído: o próximo sublote disponível foi entregue ao chat do Flow.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Produz Flow em sublotes rígidos de cinco cenas.")
    parser.add_argument("--txt", type=Path, required=True, help="TXT com blocos [[SCENE NN]].")
    parser.add_argument("--state-dir", type=Path, default=Path("automation/google_flow/state"))
    parser.add_argument("--await-user-selection", action="store_true", help="Aguarda a confirmação do chat escolhido no Firefox.")
    args = parser.parse_args()
    asyncio.run(run(args.txt, args.state_dir, args.await_user_selection))
