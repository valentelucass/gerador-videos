"""Retentativas infinitas com backoff exponencial e jitter moderado."""
from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from playwright.async_api import Locator, Page

from .click_feedback import show_click_target

T = TypeVar("T")


class RetryManager:
    def __init__(self, logger: logging.Logger, initial_delay: float, max_delay: float, show_click_highlight: bool = True,
                 click_highlight_duration_ms: int = 550, event_callback: Callable[..., None] | None = None) -> None:
        self.logger = logger
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.show_click_highlight = show_click_highlight
        self.click_highlight_duration_ms = click_highlight_duration_ms
        self.event_callback = event_callback

    def _event(self, event: str, **details: object) -> None:
        if self.event_callback:
            self.event_callback(event, **details)

    def _delay(self, attempt: int) -> float:
        return min(self.initial_delay * (2 ** (attempt - 1)), self.max_delay) + random.uniform(0, 0.8)

    async def retry_until_success(
        self, name: str, operation: Callable[[], Awaitable[T]], recovery: Callable[[Exception], Awaitable[None]] | None = None,
        retry_delay_seconds: float | None = None,
    ) -> T:
        attempt = 0
        while True:
            try:
                current_attempt = attempt + 1
                self.logger.info("Ação iniciada | %s | tentativa %s", name, current_attempt)
                self._event("action_started", action=name, attempt=current_attempt)
                result = await operation()
                self.logger.info("Ação concluída | %s | tentativa %s", name, current_attempt)
                self._event("action_succeeded", action=name, attempt=current_attempt)
                return result
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                attempt += 1
                delay = retry_delay_seconds if retry_delay_seconds is not None else self._delay(attempt)
                self.logger.warning("%s falhou (tentativa %s): %s. Nova tentativa em %.1fs.", name, attempt, exc, delay)
                self._event("action_failed", action=name, attempt=attempt, error_type=type(exc).__name__, error=str(exc), retry_in_seconds=delay)
                if recovery:
                    try:
                        await recovery(exc)
                    except Exception as recovery_exc:
                        self.logger.warning("Recuperação de %s falhou: %s", name, recovery_exc)
                        self._event("recovery_failed", action=name, error_type=type(recovery_exc).__name__, error=str(recovery_exc))
                await asyncio.sleep(delay)

    async def retry_click(self, name: str, supplier: Callable[[], Awaitable[Locator]], verify: Callable[[], Awaitable[object]] | None = None,
                          recovery: Callable[[Exception], Awaitable[None]] | None = None) -> None:
        async def operation() -> None:
            locator = await supplier()
            if self.show_click_highlight:
                await show_click_target(locator, self.click_highlight_duration_ms)
            await locator.click()
            if verify:
                await verify()
        await self.retry_until_success(name, operation, recovery)

    async def retry_fill(self, name: str, supplier: Callable[[], Awaitable[Locator]], value: str,
                         recovery: Callable[[Exception], Awaitable[None]] | None = None) -> None:
        async def operation() -> None:
            locator = await supplier()
            await locator.fill(value)
            if (await locator.input_value()).strip() != value.strip():
                raise RuntimeError("O valor preenchido não foi confirmado.")
        await self.retry_until_success(name, operation, recovery)

    async def retry_refresh(self, page: Page) -> None:
        async def operation() -> None:
            await page.reload(wait_until="domcontentloaded")
            # UIs beta frequentemente mantêm polling/websockets; networkidle
            # poderia nunca ocorrer. A próxima ação valida os elementos reais.
            await page.wait_for_load_state("domcontentloaded")
        await self.retry_until_success("Atualização da página", operation)

    async def retry_locator(self, name: str, supplier: Callable[[], Awaitable[Locator]],
                            recovery: Callable[[Exception], Awaitable[None]] | None = None) -> Locator:
        return await self.retry_until_success(name, supplier, recovery)
