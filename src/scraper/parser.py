"""Parsers for lovethework.com pages, based on actual HTML structure.

Page hierarchy:
  Winners page (?tab=cannes-lions)
    → Category pages (/awards/winners-shortlists/cannes-lions/{category})
      → Campaign cards (with award tags)
        → Campaign detail pages (/work/entries/{slug})
"""

from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urlparse

from playwright.async_api import Page

from src.scraper.models import Award, CampaignEntry, ScrapedCampaign

logger = logging.getLogger(__name__)

# Award tag CSS class → human-readable level
_AWARD_LEVEL_MAP = {
    "tag--type_grandPrix": "Grand Prix",
    "tag--type_gold": "Gold",
    "tag--type_silver": "Silver",
    "tag--type_bronze": "Bronze",
    "tag--type_shortlist": "Shortlist",
}

# Only scrape these award levels
_TARGET_LEVELS = {"Grand Prix", "Gold", "Silver", "Bronze"}


def _slug_from_url(url: str) -> str:
    """Extract slug from URL like /work/entries/one-second-ads-741948."""
    path = urlparse(url).path.rstrip("/")
    return path.split("/")[-1]


async def extract_category_links(page: Page, festival_slug: str = "cannes-lions") -> list[dict[str, str]]:
    """Extract category links from the winners page.

    Uses data-testid="button" anchor tags with href pattern
    /awards/winners-shortlists/{festival}/{category}.
    """
    categories = []
    pattern = f"/winners-shortlists/{festival_slug}/"
    anchors = await page.query_selector_all(f'a[data-testid="button"][href*="{pattern}"]')
    seen = set()
    for a in anchors:
        href = await a.get_attribute("href") or ""
        if not href or href in seen:
            continue
        if href.startswith("/"):
            href = f"https://www.lovethework.com{href}"
        name = await a.get_attribute("aria-label") or ""
        if not name:
            name = (await a.inner_text()).strip()
        if href and name:
            seen.add(href)
            categories.append({"name": name, "url": href})

    logger.info(f"Found {len(categories)} category links")
    return categories


async def _clear_filters_and_load(page: Page) -> None:
    """Clear any pre-applied award level filters so all entries are visible.

    The site sometimes defaults to showing only GP + Gold.
    We clear filters to see all entries, then filter in code.
    """
    # Look for "Close" or "Clear" button in the selected filters area
    # The selected filters show as tags with ✕ buttons
    close_btn = await page.query_selector('button:has-text("Close"), button:has-text("Clear")')
    if close_btn:
        logger.info("Clearing pre-applied filters")
        await close_btn.click()
        await asyncio.sleep(2)
        return

    # Alternative: remove individual filter tags by clicking their ✕ buttons
    filter_tags = await page.query_selector_all('[data-testid="tag"] button, [class*="Selected"] button')
    if filter_tags:
        logger.info(f"Removing {len(filter_tags)} individual filters")
        for btn in filter_tags:
            try:
                await btn.click()
                await asyncio.sleep(0.5)
            except Exception:
                pass
        await asyncio.sleep(2)


async def _scroll_to_load_all(page: Page, max_rounds: int = 20) -> None:
    """Scroll down to load all lazy-loaded content."""
    prev_height = 0
    stable_count = 0
    for _ in range(max_rounds):
        height = await page.evaluate("document.body.scrollHeight")
        if height == prev_height:
            stable_count += 1
            if stable_count >= 2:
                break
        else:
            stable_count = 0
        prev_height = height
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1.2)


async def extract_campaign_entries(
    page: Page,
    category_name: str,
    festival: str = "Cannes Lions",
    year: int | None = None,
) -> list[CampaignEntry]:
    """Extract campaign entries from a category listing page.

    Only returns entries with Grand Prix, Gold, Silver, or Bronze awards.
    """
    entries: list[CampaignEntry] = []

    # Campaign cards are inside grid(s) with data-testid="group-grid"
    cards = await page.query_selector_all('[data-testid="group-grid"] > div')
    if not cards:
        # Fallback: find cards by their campaign links
        cards = await page.query_selector_all('div:has(a[href*="/work/entries/"])')

    logger.info(f"Found {len(cards)} campaign cards in {category_name}")

    for card in cards:
        try:
            # Extract award level from tag
            tag_el = await card.query_selector('[data-testid="tag"]')
            if not tag_el:
                continue
            tag_classes = await tag_el.get_attribute("class") or ""
            award_level = ""
            for css_class, level in _AWARD_LEVEL_MAP.items():
                if css_class in tag_classes:
                    award_level = level
                    break

            if award_level not in _TARGET_LEVELS:
                continue

            # Campaign detail link
            link_el = await card.query_selector('a[href*="/work/entries/"]')
            if not link_el:
                continue
            href = await link_el.get_attribute("href") or ""
            if href.startswith("/"):
                href = f"https://www.lovethework.com{href}"

            # Title from h3
            title_el = await card.query_selector("h3")
            title = (await title_el.inner_text()).strip() if title_el else ""

            # Subcategory and Brand+Agency are body-small (NOT body-small-short which is the award tag)
            small_texts = await card.query_selector_all('p[class*="typography--size_body-small"]')
            subcategory = ""
            brand_agency = ""
            text_idx = 0
            for p in small_texts:
                cls = await p.get_attribute("class") or ""
                if "body-small-short" in cls:
                    continue  # skip award tag text
                text = (await p.inner_text()).strip()
                if text_idx == 0:
                    subcategory = text
                elif text_idx == 1:
                    brand_agency = text
                text_idx += 1

            brand = ""
            agency = ""
            if brand_agency:
                parts = [p.strip() for p in brand_agency.split(",", 1)]
                brand = parts[0]
                agency = parts[1].strip() if len(parts) > 1 else ""

            # Image
            img_el = await card.query_selector("img")
            image_url = ""
            if img_el:
                image_url = await img_el.get_attribute("src") or ""

            slug = _slug_from_url(href)
            award = Award(
                level=award_level,
                category=category_name,
                subcategory=subcategory,
                festival=festival,
                year=year,
            )

            entries.append(CampaignEntry(
                url=href,
                slug=slug,
                title=title,
                brand=brand,
                agency=agency,
                image_url=image_url,
                awards=[award],
            ))

        except Exception as e:
            logger.warning(f"Failed to parse a campaign card: {e}")
            continue

    logger.info(f"Extracted {len(entries)} award-winning entries from {category_name}")
    return entries


def merge_campaign_entries(all_entries: list[CampaignEntry]) -> list[CampaignEntry]:
    """Merge duplicate campaigns (same URL) keeping all their awards."""
    by_url: dict[str, CampaignEntry] = {}
    for entry in all_entries:
        if entry.url in by_url:
            existing = by_url[entry.url]
            existing.awards.extend(entry.awards)
        else:
            by_url[entry.url] = entry.model_copy(deep=True)

    merged = list(by_url.values())
    logger.info(f"Merged {len(all_entries)} entries → {len(merged)} unique campaigns")
    return merged


async def parse_campaign_page(page: Page, entry: CampaignEntry) -> ScrapedCampaign:
    """Parse a campaign detail page into full structured data."""
    raw_html = await page.content()

    # Title
    title = entry.title
    h1 = await page.query_selector("h1")
    if h1:
        page_title = (await h1.inner_text()).strip()
        if page_title:
            title = page_title

    # Description / case study text
    description = ""
    case_study_text = ""

    paragraphs = await page.query_selector_all("main p, article p, [class*='content'] p")
    texts = []
    for p in paragraphs:
        t = (await p.inner_text()).strip()
        if t and len(t) > 30:
            texts.append(t)
    if texts:
        case_study_text = "\n\n".join(texts)

    for sel in ["[class*='description']", "[class*='Description']", "article"]:
        el = await page.query_selector(sel)
        if el:
            text = (await el.inner_text()).strip()
            if len(text) > len(description):
                description = text

    # Videos
    video_urls = []
    for sel in ["video source", "iframe[src*='youtube']", "iframe[src*='vimeo']", "iframe[src*='player']"]:
        els = await page.query_selector_all(sel)
        for el in els:
            src = await el.get_attribute("src")
            if src:
                if src.startswith("//"):
                    src = f"https:{src}"
                video_urls.append(src)

    # Images
    image_urls = [entry.image_url] if entry.image_url else []
    imgs = await page.query_selector_all("main img, article img, [class*='media'] img")
    for img in imgs:
        src = await img.get_attribute("src") or ""
        if src and not src.endswith(".svg") and "logo" not in src.lower():
            if src.startswith("/"):
                src = f"https://www.lovethework.com{src}"
            if src not in image_urls:
                image_urls.append(src)

    # Credits
    credits = []
    credit_rows = await page.query_selector_all("[class*='credit'] tr, [class*='Credit'] li, [class*='credits'] li")
    for row in credit_rows:
        text = (await row.inner_text()).strip()
        if ":" in text:
            role, name = text.split(":", 1)
            credits.append({"role": role.strip(), "name": name.strip()})

    # Country
    country = ""
    for sel in ["[class*='country']", "[class*='Country']"]:
        el = await page.query_selector(sel)
        if el:
            country = (await el.inner_text()).strip()
            break

    return ScrapedCampaign(
        url=entry.url,
        slug=entry.slug,
        title=title,
        brand=entry.brand,
        agency=entry.agency,
        country=country,
        awards=entry.awards,
        description=description,
        case_study_text=case_study_text,
        credits=credits,
        video_urls=video_urls,
        image_urls=list(dict.fromkeys(image_urls)),
        raw_html=raw_html,
    )
