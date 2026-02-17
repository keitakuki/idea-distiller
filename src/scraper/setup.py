"""Interactive browser setup: manual login + session save + page inspection.

Usage:
    # Step 1: Manual login - opens browser, you log in, session is saved
    python -m src.scraper.setup login

    # Step 2: Inspect a page - navigates to URL, saves screenshot + HTML
    python -m src.scraper.setup inspect <URL>
    python -m src.scraper.setup inspect <URL> --tab entries   # Click Entries tab first
    python -m src.scraper.setup inspect <URL> --tab credits   # Click Credits tab first

    # Step 3: Check saved session is still valid
    python -m src.scraper.setup check
"""

from __future__ import annotations

import asyncio
import sys

from playwright.async_api import async_playwright

from src.config import settings
from src.scraper.parser import _click_tab_and_wait

STATE_DIR = settings.playwright_state_dir
STATE_FILE = STATE_DIR / "auth_state.json"
DEBUG_DIR = settings.data_dir / "debug"


async def manual_login() -> None:
    """Open browser visibly for manual login, then save session state."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        print("Opening Love the Work...")
        print("Please log in manually in the browser window.")
        print("After logging in and seeing the dashboard, press Enter here to save the session.")
        print()

        await page.goto("https://www.lovethework.com/en", wait_until="domcontentloaded")

        # Wait for user to log in
        input(">>> Press Enter after you have logged in successfully... ")

        # Save session
        await context.storage_state(path=str(STATE_FILE))
        print(f"\nSession saved to {STATE_FILE}")

        # Take a screenshot to confirm
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(DEBUG_DIR / "post_login.png"), full_page=True)
        print(f"Screenshot saved to {DEBUG_DIR / 'post_login.png'}")

        await browser.close()


async def inspect_page(url: str, tab: str | None = None) -> None:
    """Navigate to a URL with saved session, save screenshot + HTML for debugging.

    Args:
        tab: Optional tab to click before saving ("entries" or "credits").
    """
    if not STATE_FILE.exists():
        print("No saved session found. Run 'python -m src.scraper.setup login' first.")
        return

    DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context(
            storage_state=str(STATE_FILE),
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        print(f"Navigating to {url} ...")
        await page.goto(url, wait_until="domcontentloaded")

        # Wait for Next.js hydration (JS event handlers to attach)
        print("Waiting for page + JS to fully load...")
        try:
            await page.wait_for_load_state("networkidle", timeout=30000)
            print("  networkidle reached")
        except Exception:
            print("  networkidle timeout, waiting 10s fallback...")
            await page.wait_for_timeout(10000)

        # Click specific tab if requested
        if tab:
            tab_map = {"entries": "#tab-1", "credits": "#tab-2"}
            tab_selector = tab_map.get(tab.lower())
            if tab_selector:
                print(f"Clicking {tab} tab ({tab_selector})...")
                switched = await _click_tab_and_wait(page, tab_selector, timeout_s=20)
                if switched:
                    print("  Tab switched successfully (aria-selected=true)")
                else:
                    print("  Warning: tab did not switch. Page may still be loading.")

        # Generate safe filename from URL
        safe_name = url.split("//")[-1].replace("/", "_").replace("?", "_")[:80]
        if tab:
            safe_name += f"_tab_{tab}"

        # Save screenshot
        screenshot_path = DEBUG_DIR / f"{safe_name}.png"
        await page.screenshot(path=str(screenshot_path), full_page=True)
        print(f"Screenshot: {screenshot_path}")

        # Save HTML
        html_path = DEBUG_DIR / f"{safe_name}.html"
        html = await page.content()
        html_path.write_text(html, encoding="utf-8")
        print(f"HTML: {html_path}")

        # Print some basic page info
        title = await page.title()
        print(f"\nPage title: {title}")

        # Count key elements
        links = await page.query_selector_all("a[href*='/campaigns/']")
        print(f"Campaign links found: {len(links)}")

        if links:
            print("\nFirst 5 campaign links:")
            for link in links[:5]:
                href = await link.get_attribute("href")
                text = (await link.inner_text()).strip()[:60]
                print(f"  {text} -> {href}")

        # If inspecting entries tab, print table rows
        if tab and tab.lower() == "entries":
            print("\n--- Entries Tab Content ---")
            rows = await page.query_selector_all("table tr")
            print(f"  Table rows found: {len(rows)}")
            for row in rows:
                cells = await row.query_selector_all("td")
                if not cells:
                    continue
                texts = []
                for cell in cells:
                    t = (await cell.inner_text()).strip().replace("ChevronRight", "").strip()
                    texts.append(t)
                if texts and texts[0] != "Name":
                    print(f"  {' | '.join(texts)}")

        print(f"\nFiles saved in {DEBUG_DIR}/")
        print("Share the screenshot and/or HTML with Claude to fix selectors.")

        input("\n>>> Press Enter to close the browser... ")
        await browser.close()


async def check_session() -> None:
    """Check if saved session is still valid."""
    if not STATE_FILE.exists():
        print("No saved session found. Run 'python -m src.scraper.setup login' first.")
        return

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            storage_state=str(STATE_FILE),
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        await page.goto("https://www.lovethework.com/en", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)

        # Check for login indicators
        html = await page.content()
        if "sign in" in html.lower() or "log in" in html.lower():
            print("Session EXPIRED - please run 'python -m src.scraper.setup login' again")
        else:
            print("Session is VALID")

        await browser.close()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]

    if cmd == "login":
        asyncio.run(manual_login())
    elif cmd == "inspect":
        if len(sys.argv) < 3:
            print("Usage: python -m src.scraper.setup inspect <URL> [--tab entries|credits]")
            return
        tab_arg = None
        if "--tab" in sys.argv:
            tab_idx = sys.argv.index("--tab")
            if tab_idx + 1 < len(sys.argv):
                tab_arg = sys.argv[tab_idx + 1]
        asyncio.run(inspect_page(sys.argv[2], tab=tab_arg))
    elif cmd == "check":
        asyncio.run(check_session())
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
