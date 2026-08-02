from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from pathlib import Path
import shutil

from playwright.async_api import BrowserContext, Page, async_playwright

from .config import Settings


def _profile_snapshot(source: Path, target: Path) -> Path:
    """Cria uma cópia de trabalho do perfil Firefox sem tocar no perfil aberto.

    Firefox mantém um lock no perfil original. Copiar preferências, cookies e
    storage local permite que a janela controlada herde a sessão autenticada,
    enquanto cache e locks são deliberadamente descartados.
    """
    if not source.is_dir():
        raise FileNotFoundError(f"Perfil Firefox configurado não encontrado: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    ignored = shutil.ignore_patterns(
        "parent.lock", ".parentlock", "lock", "Cache", "cache2", "startupCache",
        "shader-cache", "thumbnails", "minidumps", "crashes", "sessionstore.jsonlz4",
        "sessionstore-backups",
    )
    shutil.copytree(source, target, ignore=ignored)
    return target


@asynccontextmanager
async def persistent_page(settings: Settings) -> AsyncIterator[tuple[BrowserContext, Page]]:
    """Abre um perfil persistente para que login e sessão sobrevivam a reinícios."""
    async with async_playwright() as playwright:
        browser_type = getattr(playwright, settings.browser)
        if settings.browser == "firefox" and settings.firefox_profile_dir:
            # Não abrimos o perfil regular (ele está bloqueado pelo Firefox
            # do operador). Na primeira execução copiamos a sessão para um
            # perfil dedicado; nas seguintes, preservamos esse perfil, logo
            # um login manual feito aqui permanece disponível.
            dedicated_profile = settings.profile_dir / "firefox-authenticated"
            profile_dir = dedicated_profile if dedicated_profile.is_dir() else _profile_snapshot(
                settings.firefox_profile_dir, dedicated_profile,
            )
        else:
            profile_dir = settings.profile_dir
        options: dict[str, object] = {
            "headless": settings.headless,
            "viewport": {"width": 1440, "height": 1000},
            # Força o site a respeitar o modo escuro via prefers-color-scheme.
            "color_scheme": settings.color_scheme,
        }
        if settings.browser == "firefox" and settings.color_scheme == "dark":
            # Também escurece o chrome do Firefox controlado, não apenas a
            # página. Isso é aplicado ao perfil dedicado da automação.
            options["firefox_user_prefs"] = {
                "ui.systemUsesDarkTheme": 1,
                "browser.theme.content-theme": 0,
                "browser.theme.toolbar-theme": 0,
            }
        if settings.browser == "firefox" and settings.firefox_executable_path:
            options["executable_path"] = str(settings.firefox_executable_path)
        context = await browser_type.launch_persistent_context(str(profile_dir), **options)
        context.set_default_timeout(settings.timeout_ms)
        # O contexto persistente normalmente já cria uma página inicial. Ao
        # reutilizá-la, evitamos abrir uma segunda guia vazia a cada clique no
        # painel. O snapshot também descarta dados de restauração de sessão.
        page = context.pages[0] if context.pages else await context.new_page()
        await page.bring_to_front()
        try:
            yield context, page
        finally:
            await context.close()
