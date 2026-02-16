from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

from playwright.async_api import async_playwright

from src.config import settings
from src.scraper.auth import create_authenticated_context
from src.scraper.models import ScrapedCampaign
from src.scraper.parser import extract_campaign_links, parse_campaign_page
from src.storage.files import save_json

logger = logging.getLogger(__name__)


@dataclass
class ScrapeProgress:
    total: int = 0
    completed: int = 0
    failed: int = 0
    current_url: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def percent(self) -> int:
        if self.total == 0:
            return 0
        return int((self.completed + self.failed) / self.total * 100)


async def scrape_campaigns(
    source_url: str,
    job_id: str,
    output_dir: Path | None = None,
    skip_urls: set[str] | None = None,
) -> AsyncIterator[tuple[ScrapedCampaign | None, ScrapeProgress]]:
    """Scrape all campaigns from a winners/shortlist page.

    Yields (campaign, progress) tuples. campaign is None on failure.
    """
    output_dir = output_dir or settings.raw_dir / job_id
    skip_urls = skip_urls or set()
    progress = ScrapeProgress()

    async with async_playwright() as pw:
        context = await create_authenticated_context(
            pw,
            state_dir=settings.playwright_state_dir,
            headless=settings.scraper_headless,
        )

        page = await context.new_page()

        # Navigate to the winners listing page
        logger.info(f"Navigating to {source_url}")
        await page.goto(source_url, wait_until="domcontentloaded")
        # Human-like initial wait
        await page.wait_for_timeout(3000 + int(settings.scraper_delay * 500))

        # Scroll to load all content (some pages use infinite scroll)
        prev_count = 0
        for _ in range(10):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1500)
            links = await extract_campaign_links(page)
            if len(links) == prev_count:
                break
            prev_count = len(links)

        campaign_links = await extract_campaign_links(page)
        # Filter out already scraped URLs
        campaign_links = [u for u in campaign_links if u not in skip_urls]
        progress.total = len(campaign_links)
        logger.info(f"Will scrape {progress.total} campaigns (skipping {len(skip_urls)} already done)")

        for url in campaign_links:
            progress.current_url = url
            try:
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_timeout(int(settings.scraper_delay * 1000))

                campaign = await parse_campaign_page(page, url)

                # Save raw data
                data = campaign.model_dump()
                if not settings.export_include_raw_html:
                    data.pop("raw_html", None)
                save_json(output_dir / f"{campaign.slug}.json", data)

                progress.completed += 1
                logger.info(f"[{progress.completed}/{progress.total}] Scraped: {campaign.title or campaign.slug}")
                yield campaign, progress

            except Exception as e:
                progress.failed += 1
                error_msg = f"Failed to scrape {url}: {e}"
                progress.errors.append(error_msg)
                logger.error(error_msg)
                yield None, progress

        await page.close()
        await context.close()


async def scrape_single(url: str) -> ScrapedCampaign:
    """Scrape a single campaign page. Useful for testing."""
    async with async_playwright() as pw:
        context = await create_authenticated_context(
            pw,
            state_dir=settings.playwright_state_dir,
            headless=settings.scraper_headless,
        )
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        campaign = await parse_campaign_page(page, url)
        await page.close()
        await context.close()
        return campaign


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage: python -m src.scraper.engine <URL> [job_id]")
        sys.exit(1)

    url = sys.argv[1]
    job_id = sys.argv[2] if len(sys.argv) > 2 else "test"

    async def _main():
        async for campaign, progress in scrape_campaigns(url, job_id):
            if campaign:
                print(f"  [{progress.completed}/{progress.total}] {campaign.title}")
            else:
                print(f"  [{progress.failed} failed] {progress.errors[-1]}")

    asyncio.run(_main())
