from __future__ import annotations

import logging
from pathlib import Path

from playwright.async_api import BrowserContext, Playwright

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


async def create_authenticated_context(
    pw: Playwright,
    state_dir: Path,
    headless: bool = True,
) -> BrowserContext:
    """Create a Playwright browser context using a saved session.

    The session must be created first via:
        python -m src.scraper.setup login

    This avoids fragile automated login and looks more human-like.
    """
    state_file = state_dir / "auth_state.json"

    if not state_file.exists():
        raise RuntimeError(
            "No saved session found. Please run:\n"
            "  python -m src.scraper.setup login\n"
            "to log in manually and save the session first."
        )

    logger.info("Loading saved session state")
    browser = await pw.chromium.launch(headless=headless)
    context = await browser.new_context(
        storage_state=str(state_file),
        viewport={"width": 1280, "height": 900},
        user_agent=_USER_AGENT,
    )
    return context
