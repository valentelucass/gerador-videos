"""Feedback visual para auditoria humana dos cliques do Playwright."""
from __future__ import annotations

import asyncio

from playwright.async_api import Locator


async def show_click_target(locator: Locator, duration_ms: int) -> None:
    """Destaca o alvo real do próximo clique sem bloquear a interação da página."""
    await locator.scroll_into_view_if_needed()
    await locator.evaluate(
        """(element, duration) => {
            const id = '__automation_click_target__';
            document.getElementById(id)?.remove();
            const rect = element.getBoundingClientRect();
            const marker = document.createElement('div');
            marker.id = id;
            marker.setAttribute('aria-hidden', 'true');
            Object.assign(marker.style, {
                position: 'fixed', zIndex: '2147483647', pointerEvents: 'none',
                left: `${rect.left - 5}px`, top: `${rect.top - 5}px`,
                width: `${Math.max(rect.width + 10, 28)}px`, height: `${Math.max(rect.height + 10, 28)}px`,
                border: '3px solid #ff1744', borderRadius: '8px',
                boxShadow: '0 0 0 3px rgba(255,23,68,.22), 0 0 22px rgba(255,23,68,.95)',
                transition: 'opacity 160ms ease-out', opacity: '1',
            });
            const dot = document.createElement('span');
            Object.assign(dot.style, {
                position: 'absolute', width: '12px', height: '12px', borderRadius: '50%',
                background: '#ff1744', border: '2px solid white', left: '50%', top: '50%',
                transform: 'translate(-50%, -50%)', boxShadow: '0 0 12px #ff1744',
            });
            marker.append(dot);
            document.body.append(marker);
            marker.animate([
                { transform: 'scale(.94)', opacity: .7 }, { transform: 'scale(1.03)', opacity: 1 },
                { transform: 'scale(1)', opacity: 1 },
            ], { duration: Math.min(duration, 700), easing: 'ease-out' });
            window.setTimeout(() => { marker.style.opacity = '0'; }, Math.max(duration - 150, 100));
            window.setTimeout(() => marker.remove(), duration + 100);
        }""",
        duration_ms,
    )
    # A pausa existe apenas para que o operador enxergue o alvo; toda espera
    # funcional continua baseada em locators e eventos da página.
    await asyncio.sleep(duration_ms / 1000)
