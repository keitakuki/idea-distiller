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
        await page.goto(
            first_page_url,
            wait_until="domcontentloaded",
            timeout=settings.scraper_timeout,
        )
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
                await page.goto(
                    next_url,
                    wait_until="domcontentloaded",
                    timeout=settings.scraper_timeout,
                )
                await _human_delay(settings.scraper_delay)
                await _scroll_to_load_all(page, max_rounds=10, timeout_s=20)

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
                await page.goto(
                    entry.url,
                    wait_until="networkidle",
                    timeout=settings.scraper_timeout,
                )
                await _human_delay(settings.scraper_delay)

                campaign = await parse_campaign_page(page, entry)

                # Content gate: mark as retry if no real content
                # (mirrors the check in retry_failed() to prevent asymmetric behavior)
                if not campaign.description and not campaign.case_study_text:
                    logger.warning(f"No content scraped for {campaign.slug} — marking as retry")
                    data = campaign.model_dump()
                    data["_scrape_status"] = "no_content"
                    if not settings.export_include_raw_html:
                        data.pop("raw_html", None)
                    save_json(output_dir / f"{campaign.slug}.json", data)
                    if vault_path:
                        write_inbox_note(data, vault_path, job_id=job_id, status_override="retry")
                        copy_images_to_vault(
                            data.get("image_paths", []), output_dir, vault_path
                        )
                    progress.failed += 1
                    progress.errors.append(f"No content: {campaign.slug}")
                    yield None, progress
                    continue

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
                        write_inbox_note(data, vault_path, job_id=job_id)
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


async def retry_failed(
    vault_path: Path,
    job_id: str = "default",
    output_dir: Path | None = None,
    timeout: int = 60000,
) -> AsyncIterator[tuple[ScrapedCampaign | None, ScrapeProgress]]:
    """Re-scrape inbox notes with status: retry.

    Reads source_url from each retry note, visits the detail page directly
    (skipping listing pages), and overwrites the inbox note if successful.
    Uses a longer timeout for pages that failed due to slow loading.
    """
    import frontmatter as fm

    output_dir = output_dir or settings.raw_dir / job_id
    images_dir = output_dir / "images"
    progress = ScrapeProgress()

    # Collect retry notes (check job_id subfolder first, then top-level for back-compat)
    inbox_dir = vault_path / "inbox"
    retry_notes = []
    job_inbox = inbox_dir / job_id if job_id != "default" else None
    if job_inbox and job_inbox.exists():
        glob_files = sorted(job_inbox.glob("*.md"))
    else:
        glob_files = sorted(set(inbox_dir.glob("*.md")) | set(inbox_dir.glob("*/*.md")))
    for md_file in glob_files:
        try:
            post = fm.load(str(md_file))
            if post.metadata.get("status") == "retry":
                retry_notes.append({
                    "path": md_file,
                    "slug": post.metadata.get("slug", md_file.stem),
                    "url": post.metadata.get("source_url", ""),
                    "title": post.metadata.get("title", ""),
                    "year": post.metadata.get("year"),
                })
        except Exception:
            continue

    retry_notes = [n for n in retry_notes if n["url"]]
    progress.total_campaigns = len(retry_notes)
    progress.phase = "scraping"
    logger.info(f"Retrying {progress.total_campaigns} failed campaigns (timeout={timeout}ms)")

    if not retry_notes:
        return

    async with async_playwright() as pw:
        context = await create_authenticated_context(
            pw,
            state_dir=settings.playwright_state_dir,
            headless=settings.scraper_headless,
        )
        page = await context.new_page()
        page.set_default_timeout(timeout)

        for note in retry_notes:
            progress.current_url = note["url"]
            entry = CampaignEntry(
                url=note["url"],
                slug=note["slug"],
                title=note["title"],
                year=note["year"],
            )
            try:
                await page.goto(note["url"], wait_until="networkidle", timeout=timeout)
                await _human_delay(settings.scraper_delay)

                campaign = await parse_campaign_page(page, entry)

                # Check if we actually got content this time
                if not campaign.description and not campaign.case_study_text:
                    progress.failed += 1
                    error_msg = f"Still no content for {note['slug']} (likely paywalled)"
                    progress.errors.append(error_msg)
                    logger.warning(error_msg)
                    yield None, progress
                    continue

                # Download images
                if settings.export_download_images:
                    image_paths = await _download_campaign_images(campaign, images_dir)
                    campaign.image_paths = image_paths

                # Save raw data
                data = campaign.model_dump()
                if not settings.export_include_raw_html:
                    data.pop("raw_html", None)
                save_json(output_dir / f"{campaign.slug}.json", data)

                # Overwrite inbox note
                write_inbox_note(data, vault_path, job_id=job_id if job_id != "default" else None)
                copy_images_to_vault(
                    data.get("image_paths", []), output_dir, vault_path
                )

                progress.completed += 1
                logger.info(
                    f"  [{progress.completed}/{progress.total_campaigns}] "
                    f"Retry OK: {campaign.title}"
                )
                yield campaign, progress

            except Exception as e:
                progress.failed += 1
                error_msg = f"Retry failed for {note['slug']}: {e}"
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
        print("  or:  python -m src.scraper.cannes --retry [job_id]")
        print("  or:  python -m src.scraper.cannes --url <library_url> [job_id]")
        print()
        print("Examples:")
        print("  python -m src.scraper.cannes 2025")
        print("  python -m src.scraper.cannes --retry cannes2025")
        sys.exit(1)

    if sys.argv[1] == "--retry":
        job_id = sys.argv[2] if len(sys.argv) > 2 else "default"

        async def _main_retry():
            v_path = settings.vault_path
            count = 0
            async for campaign, progress in retry_failed(v_path, job_id=job_id):
                if campaign:
                    count += 1
                    print(f"  [{count}/{progress.total_campaigns}] {campaign.title}")
                else:
                    print(f"  [FAILED] {progress.errors[-1]}")
            print(f"\nDone: {progress.completed} recovered, {progress.failed} still failed")

        asyncio.run(_main_retry())

    elif sys.argv[1] == "--url":
        url = sys.argv[2]
        job_id = sys.argv[3] if len(sys.argv) > 3 else "test"
        year = None
        festival = "Cannes Lions"
        max_pages_arg = None

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
