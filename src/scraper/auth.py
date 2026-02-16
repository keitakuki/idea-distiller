from __future__ import annotations

import logging
from pathlib import Path

from playwright.async_api import BrowserContext, Playwright

logger = logging.getLogger(__name__)


async def create_authenticated_context(
    pw: Playwright,
    email: str,
    password: str,
    state_dir: Path,
    headless: bool = True,
) -> BrowserContext:
    """Create a Playwright browser context with authenticated session.

    Reuses saved session state if available, otherwise performs fresh login.
    """
    state_file = state_dir / "auth_state.json"
    state_dir.mkdir(parents=True, exist_ok=True)

    browser = await pw.chromium.launch(headless=headless)

    # Try reusing saved session
    if state_file.exists():
        logger.info("Reusing saved session state")
        context = await browser.new_context(storage_state=str(state_file))
        page = await context.new_page()
        await page.goto("https://www.lovethework.com/en", wait_until="domcontentloaded")
        # Check if still authenticated by looking for login button
        login_btn = await page.query_selector('a[href*="login"], button:text("Sign in")')
        if not login_btn:
            logger.info("Saved session is still valid")
            await page.close()
            return context
        logger.info("Saved session expired, performing fresh login")
        await page.close()
        await context.close()

    # Fresh login
    context = await browser.new_context()
    page = await context.new_page()
    logger.info("Navigating to login page")
    await page.goto("https://www.lovethework.com/en/login", wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)

    # Fill login form
    await page.fill('input[type="email"], input[name="email"]', email)
    await page.fill('input[type="password"], input[name="password"]', password)
    await page.click('button[type="submit"]')

    # Wait for navigation after login
    await page.wait_for_url("**/lovethework.com/**", timeout=15000)
    await page.wait_for_timeout(3000)

    # Save session state
    await context.storage_state(path=str(state_file))
    logger.info("Session state saved")
    await page.close()
    return context
