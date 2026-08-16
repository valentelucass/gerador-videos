"""Configuração exclusiva do Google Flow; não reutiliza a URL do Vibes."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class FlowSettings:
    browser: str
    cdp_url: str
    page_url_contains: str
    page_url_exact: bool
    timeout_ms: int
    result_timeout: int
    retry_delay: int
    max_attempts: int
    downloads_dir: Path
    card_selector: str
    card_id_attribute: str
    chat_selector: str
    send_selector: str
    animate_selector: str
    animation_prompt_selector: str
    animation_send_selector: str
    download_selector: str
    delete_selector: str
    confirm_delete_selector: str
    error_selector: str
    firefox_profile_dir: Path | None
    firefox_executable_path: Path | None
    profile_dir: Path
    color_scheme: str

    @classmethod
    def load(cls, state_dir: Path) -> "FlowSettings":
        cdp_url = os.getenv("FLOW_CDP_URL", "").strip()
        browser = os.getenv("FLOW_BROWSER", "chromium").strip().lower()
        if browser == "chromium" and not cdp_url.startswith("http://") and not cdp_url.startswith("https://"):
            raise ValueError("Defina FLOW_CDP_URL (por exemplo http://127.0.0.1:9222) em automation/.env.")
        card_selector = os.getenv("FLOW_CARD_SELECTOR", "[data-testid*='asset'], [data-testid*='media']").strip()
        chat_selector = os.getenv(
            "FLOW_CHAT_SELECTOR",
            "[role='textbox'][data-slate-editor='true'][contenteditable='true']",
        ).strip()
        settings = cls(
            browser=browser,
            cdp_url=cdp_url,
            page_url_contains=os.getenv("FLOW_PAGE_URL_CONTAINS", "labs.google/fx/").strip().lower(),
            page_url_exact=os.getenv("FLOW_PAGE_URL_EXACT", "false").strip().lower() in {"1", "true", "yes", "sim"},
            timeout_ms=int(os.getenv("FLOW_TIMEOUT_MS", "30000")),
            result_timeout=int(os.getenv("FLOW_RESULT_TIMEOUT_SECONDS", "900")),
            retry_delay=int(os.getenv("FLOW_RETRY_DELAY_SECONDS", "12")),
            max_attempts=int(os.getenv("FLOW_MAX_ATTEMPTS_PER_ITEM", "3")),
            downloads_dir=state_dir / "downloads",
            card_selector=card_selector,
            card_id_attribute=os.getenv("FLOW_CARD_ID_ATTRIBUTE", "data-id").strip(),
            chat_selector=chat_selector,
            send_selector=os.getenv("FLOW_SEND_SELECTOR", "button:has(i:text-is('arrow_forward'))").strip(),
            animate_selector=os.getenv("FLOW_ANIMATE_SELECTOR", "button:has-text('Animate'), button:has-text('Animar')").strip(),
            animation_prompt_selector=os.getenv("FLOW_ANIMATION_PROMPT_SELECTOR", chat_selector).strip(),
            animation_send_selector=os.getenv("FLOW_ANIMATION_SEND_SELECTOR", os.getenv("FLOW_SEND_SELECTOR", "button[aria-label*='Send'], button[aria-label*='Enviar']")).strip(),
            download_selector=os.getenv("FLOW_DOWNLOAD_SELECTOR", "button[aria-label*='Download'], button[aria-label*='Baixar']").strip(),
            delete_selector=os.getenv("FLOW_DELETE_SELECTOR", "button[aria-label*='Delete'], button[aria-label*='Excluir'], button[aria-label*='Apagar']").strip(),
            confirm_delete_selector=os.getenv("FLOW_CONFIRM_DELETE_SELECTOR", "button:has-text('Delete'), button:has-text('Excluir'), button:has-text('Apagar')").strip(),
            error_selector=os.getenv("FLOW_ERROR_SELECTOR", "text=/generation failed|falha ao gerar|something went wrong/i").strip(),
            firefox_profile_dir=Path(os.environ["FIREFOX_PROFILE_DIR"]).expanduser() if os.getenv("FIREFOX_PROFILE_DIR") else None,
            firefox_executable_path=Path(os.environ["FIREFOX_EXECUTABLE_PATH"]).expanduser() if os.getenv("FIREFOX_EXECUTABLE_PATH") else None,
            profile_dir=ROOT / "browser-profile",
            color_scheme=os.getenv("BROWSER_COLOR_SCHEME", "dark").strip().lower(),
        )
        if settings.browser not in {"firefox", "chromium"}:
            raise ValueError("FLOW_BROWSER deve ser firefox ou chromium.")
        if settings.color_scheme not in {"dark", "light", "no-preference"}:
            raise ValueError("BROWSER_COLOR_SCHEME deve ser dark, light ou no-preference.")
        if not settings.page_url_contains:
            raise ValueError("FLOW_PAGE_URL_CONTAINS não pode estar vazio.")
        if not 1 <= settings.max_attempts <= 5:
            raise ValueError("FLOW_MAX_ATTEMPTS_PER_ITEM deve estar entre 1 e 5.")
        return settings

    def ensure_directories(self) -> None:
        for directory in (self.downloads_dir, self.profile_dir):
            directory.mkdir(parents=True, exist_ok=True)
