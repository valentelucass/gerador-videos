"""Coordena grupos de 25 cenas pelo texto e pelos avisos visíveis do Flow."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from .checkpoint import FlowCheckpoint
from .config import FlowSettings
from .job import FlowScene, scene_batch_prompt


class FlowWorkflow:
    """O Flow executa a criação; o robô lê o chat e só libera o próximo grupo com confirmação."""

    GROUP_SIZE = 25

    def __init__(self, page: Page, settings: FlowSettings, checkpoint: FlowCheckpoint, logger: logging.Logger, state_dir: Path, await_selection: bool = False) -> None:
        self.page, self.settings, self.checkpoint, self.logger = page, settings, checkpoint, logger
        self.state_dir, self.await_selection = state_dir, await_selection

    async def run(self, scenes: list[FlowScene]) -> None:
        if self.await_selection:
            await self._await_user_selection()
        await self._await_chat()
        for start in range(0, len(scenes), self.GROUP_SIZE):
            group = scenes[start:start + self.GROUP_SIZE]
            if all(self.checkpoint.status(scene.id) == "batch_complete" for scene in group):
                continue
            await self._run_group(group)
            if group[-1].number != scenes[-1].number:
                await self._await_continue(group[0].number, group[-1].number)

    async def _run_group(self, group: list[FlowScene]) -> None:
        first, last = group[0].number, group[-1].number
        marker = f"FLOW_GROUP_COMPLETE_{first:02d}_{last:02d}"
        statuses = [self.checkpoint.status(scene.id) for scene in group]
        text = await self._chat_text()
        marker_count = text.count(marker)

        if not any(status in {"sent", "batch_complete"} for status in statuses):
            self.logger.info("Enviando grupo %s–%s ao chat do Flow.", first, last)
            await self._paste_and_send(scene_batch_prompt(group))
            for scene in group:
                self.checkpoint.update(scene.id, "sent")
            marker_count = 0
        elif marker_count == 0:
            # Retomada de uma execução antiga: não duplica prompts já enviados.
            await self._paste_and_send(self._resume_instruction(first, last, marker))

        await self._wait_for_group(marker, marker_count, first, last)
        for scene in group:
            self.checkpoint.update(scene.id, "batch_complete")
        self.logger.info("Grupo %s–%s confirmado pelo chat do Flow.", first, last)

    @staticmethod
    def _resume_instruction(first: int, last: int, marker: str) -> str:
        return (
            f"RESUME CONTROL FOR SCENES {first:02d}-{last:02d}: read the existing chat and screen results before acting. "
            "Do not recreate completed videos. Identify any incomplete or failed sub-batch, retry only its failed image/video until it succeeds, "
            "then ensure each completed sub-batch has its five temporary still images deleted while final videos remain. "
            f"When every scene in this group is verified, reply exactly: {marker}."
        )

    @staticmethod
    def _retry_instruction(first: int, last: int, attempt: int) -> str:
        return (
            f"ERROR RECOVERY {attempt} FOR GROUP {first:02d}-{last:02d}: an error is visible in Flow. Read the error and the chat history, "
            "find the exact failed scene/sub-batch, preserve all successful videos, retry only the failed generation, validate it visually, "
            "and remove only temporary stills after the matching videos succeed. Do not advance or claim completion until the error is resolved."
        )

    async def _wait_for_group(self, marker: str, prior_count: int, first: int, last: int) -> None:
        deadline = asyncio.get_running_loop().time() + self.settings.result_timeout
        known_errors = 0
        retries = 0
        # Uma instrução nossa contém o marcador; a resposta do Flow deve gerar a
        # segunda ocorrência. Em retomada, se ainda não havia marcador, também
        # esperamos as duas ocorrências.
        required = 2 if prior_count < 2 else prior_count
        while asyncio.get_running_loop().time() < deadline:
            errors = await self.page.locator(self.settings.error_selector).count()
            if errors > known_errors:
                retries += 1
                await self._snapshot(f"erro_grupo_{first:02d}_{last:02d}_tentativa_{retries}")
                if retries > self.settings.max_attempts:
                    raise RuntimeError(f"O Flow continuou reportando falha no grupo {first:02d}-{last:02d}; nenhuma cena posterior foi enviada.")
                await self._paste_and_send(self._retry_instruction(first, last, retries))
                self.logger.warning("Erro visível no grupo %s–%s; solicitada recuperação %s.", first, last, retries)
                known_errors = errors
            if (await self._chat_text()).count(marker) >= required:
                return
            await asyncio.sleep(3)
        raise RuntimeError(f"O chat do Flow não confirmou a conclusão segura do grupo {first:02d}-{last:02d}.")

    async def _await_continue(self, first: int, last: int) -> None:
        waiting, proceed = self.state_dir / f"awaiting_continue_{first:02d}_{last:02d}.flag", self.state_dir / "continue.flag"
        waiting.write_text("Grupo concluído; aguardando autorização do operador.", encoding="utf-8")
        try:
            while not proceed.exists():
                await asyncio.sleep(1)
            proceed.unlink(missing_ok=True)
        finally:
            waiting.unlink(missing_ok=True)

    async def _await_user_selection(self) -> None:
        ready, waiting = self.state_dir / "flow_ready.flag", self.state_dir / "awaiting_user_selection.flag"
        waiting.write_text("Aguardando confirmação do chat Flow.", encoding="utf-8")
        try:
            while not ready.exists():
                await asyncio.sleep(1)
            ready.unlink(missing_ok=True)
        finally:
            waiting.unlink(missing_ok=True)

    async def _await_chat(self) -> None:
        try:
            await self.page.locator(self.settings.chat_selector).first.wait_for(state="visible", timeout=self.settings.result_timeout * 1000)
        except PlaywrightTimeoutError as exc:
            raise RuntimeError("O Flow não mostrou o editor do chat preparado.") from exc

    async def _chat_text(self) -> str:
        return await self.page.locator("body").inner_text()

    async def _snapshot(self, label: str) -> None:
        directory = self.state_dir / "observations"
        directory.mkdir(parents=True, exist_ok=True)
        await self.page.screenshot(path=str(directory / f"{label}.png"), full_page=False)

    async def _paste_and_send(self, text: str) -> None:
        box = self.page.locator(self.settings.chat_selector).first
        await box.click()
        await box.press("Control+A")
        await box.press("Backspace")
        await self.page.keyboard.insert_text(text)
        send = self.page.locator(self.settings.send_selector)
        deadline = asyncio.get_running_loop().time() + min(self.settings.result_timeout, 180)
        while asyncio.get_running_loop().time() < deadline:
            if await send.count():
                candidate = send.last
                if await candidate.is_visible() and await candidate.get_attribute("aria-disabled") != "true":
                    await candidate.click()
                    return
            await asyncio.sleep(0.25)
        raise RuntimeError("O texto foi colado, mas o botão Criar não voltou a ficar disponível.")
