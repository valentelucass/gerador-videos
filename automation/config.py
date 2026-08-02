"""Configuração isolada da automação, carregada de ``automation/.env``."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "sim"}


@dataclass(frozen=True)
class Settings:
    platform_url: str
    resume_url: str
    browser: str
    headless: bool
    color_scheme: str
    show_click_highlight: bool
    click_highlight_duration_ms: int
    max_upload: int
    timeout_ms: int
    retry_initial_delay: float
    retry_max_delay: float
    rate_limit_wait: int
    error_retry_delay: int
    repeated_error_threshold: int
    repeated_error_wait: int
    max_generation_errors_per_round: int
    deferred_round_wait: int
    max_deferred_rounds: int
    image_step_timeout: int
    platform_error_refresh_delay: int
    upload_result_timeout: int
    result_timeout: int
    image_card_selector: str
    firefox_profile_dir: Path | None
    firefox_executable_path: Path | None
    prompt_path: Path = ROOT / "prompts" / "animate.md"
    logs_dir: Path = ROOT / "logs"
    artifacts_dir: Path = ROOT / "artifacts"
    state_path: Path = ROOT / "state" / "checkpoint.json"
    profile_dir: Path = ROOT / "browser-profile"

    @classmethod
    def load(cls) -> "Settings":
        url = os.getenv("PLATFORM_URL", "").strip()
        if not url or url == "https://example.com":
            raise ValueError("Defina PLATFORM_URL no arquivo automation/.env.")
        settings = cls(
            platform_url=url,
            resume_url=os.getenv("RESUME_URL", "").strip(),
            browser=os.getenv("BROWSER", "firefox").strip().lower(),
            headless=_bool("HEADLESS", False),
            color_scheme=os.getenv("BROWSER_COLOR_SCHEME", "dark").strip().lower(),
            show_click_highlight=_bool("SHOW_CLICK_HIGHLIGHT", True),
            click_highlight_duration_ms=int(os.getenv("CLICK_HIGHLIGHT_DURATION_MS", "550")),
            max_upload=int(os.getenv("MAX_UPLOAD", "12")),
            timeout_ms=int(os.getenv("DEFAULT_TIMEOUT_MS", "30000")),
            retry_initial_delay=float(os.getenv("RETRY_INITIAL_DELAY_SECONDS", "2")),
            retry_max_delay=float(os.getenv("RETRY_MAX_DELAY_SECONDS", "30")),
            rate_limit_wait=int(os.getenv("RATE_LIMIT_WAIT_SECONDS", "300")),
            error_retry_delay=int(os.getenv("ERROR_RETRY_DELAY_SECONDS", "5")),
            repeated_error_threshold=int(os.getenv("REPEATED_ERROR_THRESHOLD", "3")),
            repeated_error_wait=int(os.getenv("REPEATED_ERROR_WAIT_SECONDS", "300")),
            max_generation_errors_per_round=int(os.getenv("MAX_GENERATION_ERRORS_PER_ROUND", "5")),
            deferred_round_wait=int(os.getenv("DEFERRED_ROUND_WAIT_SECONDS", "60")),
            max_deferred_rounds=int(os.getenv("MAX_DEFERRED_ROUNDS", "3")),
            image_step_timeout=int(os.getenv("IMAGE_STEP_TIMEOUT_SECONDS", "90")),
            platform_error_refresh_delay=int(os.getenv("PLATFORM_ERROR_REFRESH_DELAY_SECONDS", "5")),
            upload_result_timeout=int(os.getenv("UPLOAD_RESULT_TIMEOUT_SECONDS", "60")),
            result_timeout=int(os.getenv("RESULT_TIMEOUT_SECONDS", "900")),
            image_card_selector=os.getenv("IMAGE_CARD_SELECTOR", "").strip(),
            firefox_profile_dir=Path(os.environ["FIREFOX_PROFILE_DIR"]).expanduser() if os.getenv("FIREFOX_PROFILE_DIR") else None,
            firefox_executable_path=Path(os.environ["FIREFOX_EXECUTABLE_PATH"]).expanduser() if os.getenv("FIREFOX_EXECUTABLE_PATH") else None,
        )
        if settings.browser not in {"firefox", "chromium"}:
            raise ValueError("BROWSER deve ser firefox ou chromium.")
        if settings.color_scheme not in {"dark", "light", "no-preference"}:
            raise ValueError("BROWSER_COLOR_SCHEME deve ser dark, light ou no-preference.")
        if not 1 <= settings.max_upload <= 12:
            raise ValueError("MAX_UPLOAD deve estar entre 1 e 12.")
        if settings.repeated_error_threshold < 1:
            raise ValueError("REPEATED_ERROR_THRESHOLD deve ser no mínimo 1.")
        if settings.repeated_error_wait < 1:
            raise ValueError("REPEATED_ERROR_WAIT_SECONDS deve ser no mínimo 1.")
        if settings.max_generation_errors_per_round <= settings.repeated_error_threshold:
            raise ValueError("MAX_GENERATION_ERRORS_PER_ROUND deve ser maior que REPEATED_ERROR_THRESHOLD.")
        if settings.deferred_round_wait < 0:
            raise ValueError("DEFERRED_ROUND_WAIT_SECONDS não pode ser negativo.")
        if settings.max_deferred_rounds < 1:
            raise ValueError("MAX_DEFERRED_ROUNDS deve ser no mínimo 1.")
        if settings.image_step_timeout < 10:
            raise ValueError("IMAGE_STEP_TIMEOUT_SECONDS deve ser no mínimo 10.")
        return settings

    def ensure_directories(self) -> None:
        profile = self.firefox_profile_dir if self.browser == "firefox" and self.firefox_profile_dir else self.profile_dir
        for path in (self.logs_dir, self.artifacts_dir, self.state_path.parent, profile):
            path.mkdir(parents=True, exist_ok=True)
