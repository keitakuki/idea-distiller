"""Scraping engine: orchestrates Campaign Library pagination + detail page scraping.

Flow:
  1. Build Campaign Library URL with filters (festival, year)
  2. Paginate through all listing pages → extract campaign entries
  3. Visit each campaign detail page → scrape full content
  4. Download images locally for Obsidian embedding
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

from playwright.async_api import async_playwright

from src.config import settings
from src.obsidian.writer import copy_images_to_vault, write_inbox_note
from src.scraper.auth import create_authenticated_context
from src.scraper.models import CampaignEntry, ScrapedCampaign
from src.scraper.parser import (
    _scroll_to_load_all,
    build_library_url,
    extract_library_campaigns,
    get_total_pages,
    parse_campaign_page,
)
from src.storage.files import download_image, save_json

logger = logging.getLogger(__name__)


@dataclass
class ScrapeProgress:
    phase: str = "init"        # listing | scraping | done
    total_pages: int = 0
    scraped_pages: int = 0
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


def _image_filename(url: str, slug: str, idx: int) -> str:
    """Generate a deterministic filename for a downloaded image."""
    # Use hash of URL for uniqueness, prefix with slug for readability
    url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
    # Detect extension from URL
    ext = ".webp"
    for e in [".jpg", ".jpeg", ".png", ".gif", ".webp"]:
        if e in url.lower():
            ext = e
            break
    return f"{slug}_{idx}_{url_hash}{ext}"


async def _download_campaign_images(
    campaign: ScrapedCampaign,
    images_dir: Path,
) -> list[str]:
    """Download all images for a campaign. Returns list of relative paths."""
    downloaded_paths = []
    for i, url in enumerate(campaign.image_urls):
        filename = _image_filename(url, campaign.slug, i)
        local_path = await download_image(url, images_dir, filename)
        if local_path:
            # Store relative path (relative to data/raw/{job_id}/)
            downloaded_paths.append(f"images/{filename}")
    return downloaded_paths


async def scrape_campaigns(
    source_url: str | None = None,
    job_id: str = "default",
    festival: str = "Cannes Lions",
    year: int | None = None,
    output_dir: Path | None = None,
    skip_slugs: set[str] | None = None,
    max_pages: int | None = None,
    vault_path: Path | None = None,
) -> AsyncIterator[tuple[ScrapedCampaign | None, ScrapeProgress]]:
    """Scrape all campaigns from the Campaign Library.

    Uses the Campaign Library page with pagination:
      Library listing (paginated) → Campaign detail pages

    Args:
        source_url: If provided, use this URL directly. Otherwise build from festival/year.
        job_id: Job identifier for organizing output files.
        festival: Festival name for URL filter (default: "Cannes Lions").
        year: Year filter (e.g., 2025).
        output_dir: Where to save raw JSON files.
        skip_slugs: Set of slugs to skip (for resume support).
        max_pages: Maximum number of listing pages to scrape (None = all).
        vault_path: Obsidian vault path. If provided, also writes inbox notes.

    Yields (campaign, progress) tuples. campaign is None on failure.
    """
    output_dir = output_dir or settings.raw_dir / job_id
    vault_path = vault_path or (settings.vault_path if settings.obsidian_vault_path else None)
    images_dir = output_dir / "images"
    skip_slugs = skip_slugs or set()
    progress = ScrapeProgress()

    async with async_playwright() as pw:
        context = await create_authenticated_context(
            pw,
            state_dir=settings.playwright_state_dir,
            headless=settings.scraper_headless,
        )
        page = await context.new_page()

        # --- Phase 1: Collect campaign entries from listing pages ---
        progress.phase = "listing"
        all_entries: list[CampaignEntry] = []

        # Build initial URL
        if source_url:
            first_page_url = source_url
        else:
            first_page_url = build_library_url(
                festival=festival.lower(),
                year=year,
                page=1,
            )

        logger.info(f"Phase 1: Collecting campaigns from library: {first_page_url}")
        await page.goto(first_page_url, wait_until="domcontentloaded")
        await _human_delay(3.0)

        # Scroll first to load all content including pagination
        await _scroll_to_load_all(page, max_rounds=10)

        # Get total pages from pagination (must be after scroll)
        progress.total_pages = await get_total_pages(page)
        logger.info(f"Total pages: {progress.total_pages}")

        if max_pages:
            progress.total_pages = min(progress.total_pages, max_pages)

        # Scrape page 1
        entries = await extract_library_campaigns(page)
        all_entries.extend(entries)
        progress.scraped_pages = 1
        logger.info(f"  [Page 1/{progress.total_pages}] {len(entries)} campaigns")

        # Scrape remaining pages
        current_page = 1
        while current_page < progress.total_pages:
            current_page += 1

            # Navigate to next page via URL (more reliable than clicking)
            if source_url and "page=" not in source_url:
                next_url = f"{source_url}&page={current_page}"
            elif source_url:
                # Replace existing page parameter
                next_url = re.sub(r"page=\d+", f"page={current_page}", source_url)
            else:
                next_url = build_library_url(
                    festival=festival.lower(),
                    year=year,
                    page=current_page,
                )

            progress.current_url = next_url
            try:
                await page.goto(next_url, wait_until="domcontentloaded")
                await _human_delay(settings.scraper_delay)
                await _scroll_to_load_all(page, max_rounds=10)

                entries = await extract_library_campaigns(page)
                all_entries.extend(entries)
                progress.scraped_pages = current_page
                logger.info(f"  [Page {current_page}/{progress.total_pages}] {len(entries)} campaigns")

            except Exception as e:
                error_msg = f"Failed to scrape page {current_page}: {e}"
                progress.errors.append(error_msg)
                logger.error(error_msg)

            await _human_delay(1.5)

        # Fill in missing year from job parameter
        for entry in all_entries:
            if entry.year is None and year is not None:
                entry.year = year

        # Filter out already scraped
        all_entries = [e for e in all_entries if e.slug not in skip_slugs]
        progress.total_campaigns = len(all_entries)
        logger.info(f"Phase 1 complete: {progress.total_campaigns} campaigns to scrape (skipped {len(skip_slugs)} existing)")

        # --- Phase 2: Scrape each campaign detail page ---
        progress.phase = "scraping"

        for entry in all_entries:
            progress.current_url = entry.url
            try:
                await page.goto(entry.url, wait_until="domcontentloaded")
                await _human_delay(settings.scraper_delay)

                campaign = await parse_campaign_page(page, entry)

                # Download images
                if settings.export_download_images:
                    image_paths = await _download_campaign_images(campaign, images_dir)
                    campaign.image_paths = image_paths

                # Save raw data (JSON backup)
                data = campaign.model_dump()
                if not settings.export_include_raw_html:
                    data.pop("raw_html", None)
                save_json(output_dir / f"{campaign.slug}.json", data)

                # Write inbox note to Obsidian vault
                if vault_path:
                    try:
                        write_inbox_note(data, vault_path)
                        copy_images_to_vault(
                            data.get("image_paths", []), output_dir, vault_path
                        )
                    except Exception as e:
                        logger.warning(f"Failed to write inbox note for {campaign.slug}: {e}")

                progress.completed += 1
                logger.info(
                    f"  [{progress.completed}/{progress.total_campaigns}] "
                    f"Scraped: {campaign.title}"
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
        print("Usage: python -m src.scraper.cannes <year> [job_id] [max_pages]")
        print("  or:  python -m src.scraper.cannes --url <library_url> [job_id]")
        print()
        print("Examples:")
        print("  python -m src.scraper.cannes 2025")
        print("  python -m src.scraper.cannes 2025 cannes2025 'cannes lions' 3")
        sys.exit(1)

    if sys.argv[1] == "--url":
        url = sys.argv[2]
        job_id = sys.argv[3] if len(sys.argv) > 3 else "test"
        year = None
        festival = "Cannes Lions"
        max_pages_arg = None
    else:
        url = None
        year = int(sys.argv[1])
        job_id = sys.argv[2] if len(sys.argv) > 2 else f"cannes{year}"
        festival = sys.argv[3] if len(sys.argv) > 3 else "Cannes Lions"
        max_pages_arg = int(sys.argv[4]) if len(sys.argv) > 4 else None

    async def _main():
        v_path = settings.vault_path if settings.obsidian_vault_path else None
        count = 0
        async for campaign, progress in scrape_campaigns(
            source_url=url,
            job_id=job_id,
            festival=festival,
            year=year,
            max_pages=max_pages_arg,
            vault_path=v_path,
        ):
            if campaign:
                count += 1
                imgs = len(campaign.image_paths) if campaign.image_paths else 0
                print(f"  [{count}/{progress.total_campaigns}] {campaign.title} ({imgs} images)")
            else:
                print(f"  [FAILED] {progress.errors[-1]}")
        print(f"\nDone: {count} campaigns scraped")

    asyncio.run(_main())
