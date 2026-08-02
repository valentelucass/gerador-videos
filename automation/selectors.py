"""Locators semânticos centralizados. Ajuste este arquivo após inspecionar a UI real."""
from __future__ import annotations

import re
import time
from collections.abc import Callable

from playwright.async_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

LocatorFactory = Callable[[Page], Locator]


def _role(role: str, pattern: str) -> LocatorFactory:
    return lambda page: page.get_by_role(role, name=re.compile(pattern, re.IGNORECASE))


CREATE_NEW_BUTTON = (_role("button", r"create new|criar novo"),)
NO_PROJECT_YET = (lambda page: page.get_by_text(re.compile(r"^no project yet$|^nenhum projeto ainda$", re.I)),)
UPLOAD_BUTTON = (_role("button", r"upload media|enviar mídia|enviar midia"),)
CONFIRM_UPLOAD_BUTTON = (
    _role("button", r"^(upload|carregar)$"),
    lambda page: page.locator('button[type="button"]').filter(has_text=re.compile(r"^\s*(upload|carregar)\s*$", re.I)),
)
UPLOAD_INPUT = (lambda page: page.locator('input[type="file"]'),)
UPLOAD_DROPZONE = (lambda page: page.get_by_text(re.compile(r"click to add or drag and drop media", re.I)),)
# Card real da galeria do Vibes. As imagens enviadas têm alt vazio e não
# preservam o nome de arquivo no DOM, portanto a confirmação pós-upload deve
# usar este identificador estável da própria interface.
MEDIA_THUMBNAIL = (
    lambda page: page.locator('[role="button"][data-analytics-id="creation_gallery.thumbnail_click"]'),
)
LOGIN_INDICATOR = (
    _role("button", r"sign in|log in|entrar|fazer login|continue with google"),
    lambda page: page.locator('input[type="password"], input[type="email"]'),
)
MANUAL_ANIMATE_BUTTON = (_role("button", r"manual animate|animar manualmente"),)
PROMPT_TEXTAREA = (
    lambda page: page.locator("textarea:not([disabled])"),
    lambda page: page.get_by_role("textbox"),
)
ANIMATE_BUTTON = (_role("button", r"^animate$|^animar$"),)
CLOSE_POPUP_BUTTON = (_role("button", r"close|fechar|dismiss"),)
EDIT_IMAGE_TITLE = (lambda page: page.get_by_text(re.compile(r"^edit image$|^editar imagem$", re.I)),)
EDIT_VIDEO_TITLE = (lambda page: page.get_by_text(re.compile(r"^edit video$|^editar vídeo$|^editar video$", re.I)),)
# Coluna vertical do editor Vibes; cada botão contém img[alt=<nome do arquivo>].
EDITOR_SIDEBAR_THUMBNAILS = (lambda page: page.locator('button[class*="w_60px"]:has(img[alt])'),)
SUCCESS_ALERT = (lambda page: page.get_by_text(re.compile(r"animação concluída|animacao concluida|animation complete(?:d)?", re.I)),)
ERROR_ALERT = (
    # Toast real do Vibes: "Generation failed" e "Something went wrong.
    # Please try again.". Ambos significam repetir a MESMA imagem.
    lambda page: page.get_by_text(re.compile(
        r"falha na geração|falha na geracao|ocorreu um erro|"
        r"generation failed|something went wrong(?:\.\s*please try again\.)?|"
        r"animation (?:failed|error)|failed to animate",
        re.I,
    )),
)
RATE_LIMIT_ALERT = (lambda page: page.get_by_text(re.compile(r"espere alguns minutos|too many requests|rate limit|tente novamente mais tarde", re.I)),)
PLATFORM_ERROR_PAGE = (
    lambda page: page.get_by_text(re.compile(r"^something went wrong!?$", re.I)),
    lambda page: page.get_by_text(re.compile(r"an error occurred while processing your request", re.I)),
    _role("button", r"^try again$|^tentar novamente$"),
)
UPLOAD_SUCCESS_PATTERN = re.compile(
    r"(?:"
    r"upload(?:ed)?\s+(?:successfully|complete(?:d)?|finished)|"
    r"(?:successfully|success)\s+uploaded(?:\s+\d+)?\s+(?:images?|media)|"
    r"images?\s+(?:were\s+)?(?:successfully\s+)?(?:uploaded|added|ready)(?:\s+successfully)?|"
    r"media\s+(?:was\s+)?(?:successfully\s+)?(?:uploaded|added)(?:\s+successfully)?|"
    r"carregad[oa]s?\s+com\s+sucesso|upload\s+conclu[ií]do|"
    r"m[ií]dia\s+enviada\s+com\s+sucesso"
    r")",
    re.I,
)
UPLOAD_SUCCESS_ALERT = (
    # "Upload"/"Carregar" isolado é o texto do botão, nunca um sucesso.
    # Só uma mensagem completa de confirmação (o toast verde do Vibes) vale.
    lambda page: page.get_by_text(UPLOAD_SUCCESS_PATTERN),
    lambda page: page.locator('[role="alert"], [role="status"]').filter(
        has_text=UPLOAD_SUCCESS_PATTERN,
    ),
)
UPLOAD_ERROR_ALERT = (
    lambda page: page.get_by_text(re.compile(
        r"upload\s+(?:failed|error)|failed\s+to\s+upload|erro\s+ao\s+carregar|falha\s+no\s+upload",
        re.I,
    )),
)
PROCESSING_INDICATOR = (lambda page: page.get_by_text(re.compile(r"generating|gerando|processing|processando", re.I)),)


async def first_visible(page: Page, candidates: tuple[LocatorFactory, ...], timeout_ms: int) -> Locator:
    """Retorna o primeiro locator visível, alternando alternativas sem XPath."""
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for factory in candidates:
            locator = factory(page).first
            try:
                if await locator.is_visible():
                    return locator
            except PlaywrightTimeoutError:
                continue
        await page.wait_for_timeout(250)
    raise PlaywrightTimeoutError("Nenhum seletor alternativo ficou visível dentro do timeout.")


async def first_attached(page: Page, candidates: tuple[LocatorFactory, ...], timeout_ms: int) -> Locator:
    """Localiza elemento presente no DOM, inclusive input de arquivo oculto."""
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for factory in candidates:
            locator = factory(page).first
            try:
                if await locator.count():
                    return locator
            except PlaywrightTimeoutError:
                continue
        await page.wait_for_timeout(250)
    raise PlaywrightTimeoutError("Nenhum input de arquivo foi encontrado no DOM dentro do timeout.")


async def first_enabled(page: Page, candidates: tuple[LocatorFactory, ...], timeout_ms: int) -> Locator:
    """Aguarda o controle visível ficar habilitado antes de confirmar uma ação."""
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for factory in candidates:
            locator = factory(page).first
            try:
                if await locator.is_visible() and await locator.is_enabled():
                    return locator
            except PlaywrightTimeoutError:
                continue
        await page.wait_for_timeout(250)
    raise PlaywrightTimeoutError("Nenhum controle habilitado foi encontrado dentro do timeout.")


async def is_visible(page: Page, candidates: tuple[LocatorFactory, ...]) -> bool:
    try:
        await first_visible(page, candidates, 250)
        return True
    except PlaywrightTimeoutError:
        return False
