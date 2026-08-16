from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from pathlib import Path
import shutil

from playwright.async_api import BrowserContext, Page, async_playwright

from .config import FlowSettings


def _profile_snapshot(source: Path, target: Path) -> Path:
    """Copia a sessão autenticada sem bloquear nem alterar o Firefox do usuário."""
    if not source.is_dir():
        raise FileNotFoundError(f"Perfil Firefox configurado não encontrado: {source}")
    ignored = shutil.ignore_patterns(
        "parent.lock", ".parentlock", "lock", "Cache", "cache2", "startupCache",
        "shader-cache", "thumbnails", "minidumps", "crashes", "sessionstore.jsonlz4",
        "sessionstore-backups",
    )
    shutil.copytree(source, target, ignore=ignored)
    return target


@asynccontextmanager
async def persistent_page(settings: FlowSettings) -> AsyncIterator[tuple[BrowserContext, Page]]:
    """Usa Firefox autenticado ou, opcionalmente, a aba Chrome por CDP."""
    async with async_playwright() as playwright:
        if settings.browser == "firefox":
            dedicated_profile = settings.profile_dir / "firefox-authenticated"
            if not dedicated_profile.is_dir() and settings.firefox_profile_dir is None:
                raise RuntimeError(
                    "Defina FIREFOX_PROFILE_DIR em automation/.env para copiar a sessão autenticada do Firefox."
                )
            profile = dedicated_profile if dedicated_profile.is_dir() else _profile_snapshot(
                settings.firefox_profile_dir, dedicated_profile,
            )
            options: dict[str, object] = {
                "headless": False,
                "viewport": {"width": 1440, "height": 1000},
                "color_scheme": settings.color_scheme,
            }
            if settings.firefox_executable_path:
                options["executable_path"] = str(settings.firefox_executable_path)
            if settings.color_scheme == "dark":
                options["firefox_user_prefs"] = {
                    "ui.systemUsesDarkTheme": 1,
                    "browser.theme.content-theme": 0,
                    "browser.theme.toolbar-theme": 0,
                }
            context = await playwright.firefox.launch_persistent_context(str(profile), **options)
            context.set_default_timeout(settings.timeout_ms)
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto("https://labs.google/fx/pt/tools/flow", wait_until="domcontentloaded")
            await page.bring_to_front()
            try:
                yield context, page
            finally:
                await context.close()
            return
        try:
            browser = await playwright.chromium.connect_over_cdp(settings.cdp_url)
        except Exception as exc:
            raise RuntimeError(
                "Não foi possível conectar ao Chrome já aberto. Inicie o Chrome com "
                "--remote-debugging-port=9222, abra o Google Flow e deixe o chat visível."
            ) from exc
        pages = [page for context in browser.contexts for page in context.pages]
        if settings.page_url_exact:
            expected = settings.page_url_contains.rstrip("/")
            matches = [item for item in pages if item.url.lower().rstrip("/") == expected]
            if not matches:
                # O endereço foi informado explicitamente pelo operador. Em vez
                # de abrir outra guia, reutilizamos a única guia do Flow já
                # aberta e a levamos exatamente até ele.
                flow_pages = [item for item in pages if "labs.google/fx/" in item.url.lower()]
                if len(flow_pages) == 1:
                    await flow_pages[0].goto(settings.page_url_contains, wait_until="domcontentloaded")
                    matches = [flow_pages[0]]
        else:
            matches = [item for item in pages if settings.page_url_contains in item.url.lower()]
        if not matches:
            await browser.close()
            raise RuntimeError(
                "Nenhuma aba do projeto Google Flow já aberto foi encontrada; "
                "a automação não abriu nem navegou para outra página."
            )
        if len(matches) > 1:
            await browser.close()
            raise RuntimeError(
                "Há mais de uma aba compatível com Google Flow. Feche as outras abas ou informe "
                "a URL exata do projeto para impedir o uso do chat errado."
            )
        page = matches[0]
        context = page.context
        context.set_default_timeout(settings.timeout_ms)
        await page.bring_to_front()
        try:
            yield context, page
        finally:
            # A sessão pertence ao usuário. Em uma conexão CDP, Browser.close()
            # pode encerrar a janela remota inteira; deixar o objeto sair do
            # contexto do Playwright apenas desfaz a conexão de automação.
            pass
