"""Scraping engine: orchestrates the two-level navigation.

Flow:
  1. Winners page → extract category links
  2. Each category page → extract campaign entries (Grand Prix/Gold/Silver/Bronze only)
  3. Merge duplicates (same campaign winning in multiple categories)
  4. Each unique campaign detail page → scrape full content
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

from playwright.async_api import async_playwright

from src.config import settings
from src.scraper.auth import create_authenticated_context
from src.scraper.models import CampaignEntry, ScrapedCampaign
from src.scraper.parser import (
    _clear_filters_and_load,
    _scroll_to_load_all,
    extract_campaign_entries,
    extract_category_links,
    merge_campaign_entries,
    parse_campaign_page,
)
from src.storage.files import save_json

logger = logging.getLogger(__name__)


@dataclass
class ScrapeProgress:
    phase: str = "init"        # categories | collecting | scraping | done
    total_categories: int = 0
    scraped_categories: int = 0
    total_campaigns: int = 0
    completed: int = 0
    failed: int = 0
    current_url: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def percent(self) -> int:
        if self.total_campaigns == 0:
            return 0
        return int((self.completed + self.failed) / self.total_campaigns * 100)


async def _human_delay(base: float = 2.0) -> None:
    """Random delay to look human-like."""
    delay = base + random.uniform(0.5, base * 0.8)
    await asyncio.sleep(delay)


async def scrape_campaigns(
    source_url: str,
    job_id: str,
    festival: str = "Cannes Lions",
    year: int | None = None,
    output_dir: Path | None = None,
    skip_slugs: set[str] | None = None,
) -> AsyncIterator[tuple[ScrapedCampaign | None, ScrapeProgress]]:
    """Scrape all award-winning campaigns from a winners page.

    Two-level navigation:
      Winners page → Category pages → Campaign detail pages

    Yields (campaign, progress) tuples. campaign is None on failure.
    """
    output_dir = output_dir or settings.raw_dir / job_id
    skip_slugs = skip_slugs or set()
    progress = ScrapeProgress()

    # Determine festival slug from URL
    festival_slug = "cannes-lions"
    if "tab=" in source_url:
        festival_slug = source_url.split("tab=")[-1].split("&")[0]

    async with async_playwright() as pw:
        context = await create_authenticated_context(
            pw,
            state_dir=settings.playwright_state_dir,
            headless=settings.scraper_headless,
        )
        page = await context.new_page()

        # --- Phase 1: Get category links ---
        progress.phase = "categories"
        logger.info(f"Phase 1: Extracting categories from {source_url}")
        await page.goto(source_url, wait_until="domcontentloaded")
        await _human_delay(3.0)

        categories = await extract_category_links(page, festival_slug)
        progress.total_categories = len(categories)

        if not categories:
            logger.warning("No category links found!")
            progress.phase = "done"
            await page.close()
            await context.close()
            return

        # --- Phase 2: Collect campaign entries from all categories ---
        progress.phase = "collecting"
        all_entries: list[CampaignEntry] = []

        for cat in categories:
            progress.current_url = cat["url"]
            try:
                logger.info(f"Collecting from category: {cat['name']}")
                await page.goto(cat["url"], wait_until="domcontentloaded")
                await _human_delay(settings.scraper_delay)

                # Clear any pre-applied filters (site defaults to GP+Gold only)
                await _clear_filters_and_load(page)

                # Scroll to load all content
                await _scroll_to_load_all(page)

                entries = await extract_campaign_entries(
                    page,
                    category_name=cat["name"],
                    festival=festival,
                    year=year,
                )
                all_entries.extend(entries)
                progress.scraped_categories += 1
                logger.info(
                    f"  [{progress.scraped_categories}/{progress.total_categories}] "
                    f"{cat['name']}: {len(entries)} entries"
                )

            except Exception as e:
                error_msg = f"Failed to collect from {cat['name']}: {e}"
                progress.errors.append(error_msg)
                logger.error(error_msg)

            await _human_delay(1.5)

        # Merge duplicates
        unique_entries = merge_campaign_entries(all_entries)

        # Filter out already scraped
        unique_entries = [e for e in unique_entries if e.slug not in skip_slugs]
        progress.total_campaigns = len(unique_entries)
        logger.info(f"Phase 2 complete: {progress.total_campaigns} unique campaigns to scrape")

        # --- Phase 3: Scrape each campaign detail page ---
        progress.phase = "scraping"

        for entry in unique_entries:
            progress.current_url = entry.url
            try:
                await page.goto(entry.url, wait_until="domcontentloaded")
                await _human_delay(settings.scraper_delay)

                campaign = await parse_campaign_page(page, entry)

                # Save raw data
                data = campaign.model_dump()
                if not settings.export_include_raw_html:
                    data.pop("raw_html", None)
                save_json(output_dir / f"{campaign.slug}.json", data)

                progress.completed += 1
                logger.info(
                    f"  [{progress.completed}/{progress.total_campaigns}] "
                    f"Scraped: {campaign.title} ({campaign.primary_award})"
                )
                yield campaign, progress

            except Exception as e:
                progress.failed += 1
                error_msg = f"Failed to scrape {entry.title or entry.url}: {e}"
                progress.errors.append(error_msg)
                logger.error(error_msg)
                yield None, progress

        progress.phase = "done"
        await page.close()
        await context.close()


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if len(sys.argv) < 2:
        print("Usage: python -m src.scraper.engine <winners_url> [job_id] [year]")
        print("Example: python -m src.scraper.engine 'https://www.lovethework.com/en/awards/winners-shortlists?tab=cannes-lions' test 2025")
        sys.exit(1)

    url = sys.argv[1]
    job_id = sys.argv[2] if len(sys.argv) > 2 else "test"
    year = int(sys.argv[3]) if len(sys.argv) > 3 else None

    async def _main():
        count = 0
        async for campaign, progress in scrape_campaigns(url, job_id, year=year):
            if campaign:
                count += 1
                awards_str = ", ".join(f"{a.level} ({a.category})" for a in campaign.awards)
                print(f"  [{count}/{progress.total_campaigns}] {campaign.title} — {awards_str}")
            else:
                print(f"  [FAILED] {progress.errors[-1]}")
        print(f"\nDone: {count} campaigns scraped")

    asyncio.run(_main())
