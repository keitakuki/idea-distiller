"""Parsers for lovethework.com pages, based on actual HTML structure.

Page hierarchy:
  Winners page (?tab=cannes-lions)
    → Category pages (/awards/winners-shortlists/cannes-lions/{category})
      → Campaign cards (with award tags)
        → Campaign detail pages (/work/entries/{slug})
"""

from __future__ import annotations

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

    Returns list of {"name": "Audio & Radio", "url": "https://..."}.
    """
    categories = []
    # Category buttons have href containing /winners-shortlists/{festival}/
    pattern = f"/winners-shortlists/{festival_slug}/"
    anchors = await page.query_selector_all(f'a[href*="{pattern}"]')
    seen = set()
    for a in anchors:
        href = await a.get_attribute("href") or ""
        if not href or href in seen:
            continue
        # Normalize
        if href.startswith("/"):
            href = f"https://www.lovethework.com{href}"
        # Get name from aria-label or inner text
        name = await a.get_attribute("aria-label") or ""
        if not name:
            name = (await a.inner_text()).strip()
        if href and name:
            seen.add(href)
            categories.append({"name": name, "url": href})

    logger.info(f"Found {len(categories)} category links")
    return categories


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

    # Campaign cards are inside a grid with data-testid="group-grid"
    # Each card is a direct child div of the grid
    cards = await page.query_selector_all('[data-testid="group-grid"] > div')
    if not cards:
        # Fallback: try finding cards by their link structure
        cards = await page.query_selector_all('div:has(> div a[href*="/work/entries/"])')

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

            # Skip shortlisted entries
            if award_level not in _TARGET_LEVELS:
                continue

            # Extract campaign link and title
            link_el = await card.query_selector('a[href*="/work/entries/"]')
            if not link_el:
                continue
            href = await link_el.get_attribute("href") or ""
            if href.startswith("/"):
                href = f"https://www.lovethework.com{href}"

            title_el = await card.query_selector("h3")
            title = (await title_el.inner_text()).strip() if title_el else ""

            # Extract subcategory (small text above title)
            # and brand+agency (small text below title)
            small_texts = await card.query_selector_all('p[class*="typography--size_body-small"]')
            subcategory = ""
            brand_agency = ""
            for i, p in enumerate(small_texts):
                text = (await p.inner_text()).strip()
                if i == 0:
                    subcategory = text
                elif i == 1:
                    brand_agency = text

            # Parse brand, agency from comma-separated text
            brand = ""
            agency = ""
            if brand_agency:
                parts = [p.strip() for p in brand_agency.split(",", 1)]
                brand = parts[0]
                agency = parts[1] if len(parts) > 1 else ""

            # Extract image
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
            # Merge awards
            existing = by_url[entry.url]
            existing.awards.extend(entry.awards)
        else:
            by_url[entry.url] = entry.model_copy(deep=True)

    merged = list(by_url.values())
    logger.info(f"Merged {len(all_entries)} entries → {len(merged)} unique campaigns")
    return merged


async def parse_campaign_page(page: Page, entry: CampaignEntry) -> ScrapedCampaign:
    """Parse a campaign detail page into full structured data.

    Uses pre-collected entry data (title, brand, awards) and enriches
    with detail page content (description, credits, videos, etc).
    """
    raw_html = await page.content()

    # Try to get richer title from detail page
    title = entry.title
    h1 = await page.query_selector("h1")
    if h1:
        page_title = (await h1.inner_text()).strip()
        if page_title:
            title = page_title

    # Description / case study - gather all substantial text
    description = ""
    case_study_text = ""

    # Look for main content paragraphs
    paragraphs = await page.query_selector_all("main p, article p, [class*='content'] p")
    texts = []
    for p in paragraphs:
        t = (await p.inner_text()).strip()
        if t and len(t) > 30:
            texts.append(t)
    if texts:
        case_study_text = "\n\n".join(texts)

    # Try specific description containers
    for sel in ["[class*='description']", "[class*='Description']", "article"]:
        el = await page.query_selector(sel)
        if el:
            text = (await el.inner_text()).strip()
            if len(text) > len(description):
                description = text

    # Video URLs
    video_urls = []
    for sel in ["video source", "iframe[src*='youtube']", "iframe[src*='vimeo']", "iframe[src*='player']"]:
        els = await page.query_selector_all(sel)
        for el in els:
            src = await el.get_attribute("src")
            if src:
                if src.startswith("//"):
                    src = f"https:{src}"
                video_urls.append(src)

    # Image URLs (skip tiny icons/logos)
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

    # Country - try to find in metadata
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
