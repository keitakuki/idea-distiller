from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from playwright.async_api import Page

from src.scraper.models import ScrapedCampaign

logger = logging.getLogger(__name__)


def _slug_from_url(url: str) -> str:
    """Extract slug from campaign URL like /work/campaigns/some-campaign-123456."""
    path = urlparse(url).path.rstrip("/")
    return path.split("/")[-1]


async def extract_campaign_links(page: Page) -> list[str]:
    """Extract all campaign links from a winners/shortlist listing page."""
    links: list[str] = []
    # Look for links to individual campaign pages
    anchors = await page.query_selector_all('a[href*="/campaigns/"], a[href*="/work/"]')
    seen = set()
    for a in anchors:
        href = await a.get_attribute("href")
        if not href:
            continue
        # Normalize to absolute URL
        if href.startswith("/"):
            href = f"https://www.lovethework.com{href}"
        # Filter for campaign detail pages (typically contain /campaigns/ and a slug)
        if "/campaigns/" in href and href not in seen:
            seen.add(href)
            links.append(href)

    logger.info(f"Found {len(links)} campaign links")
    return links


async def parse_campaign_page(page: Page, url: str) -> ScrapedCampaign:
    """Parse a single campaign detail page into structured data."""
    slug = _slug_from_url(url)
    raw_html = await page.content()

    # Title
    title = ""
    title_el = await page.query_selector("h1")
    if title_el:
        title = (await title_el.inner_text()).strip()

    # Description / case study text
    description = ""
    case_study_text = ""
    # Try common content selectors
    for sel in ["[class*='description']", "[class*='Description']", "[class*='campaign-body']", "article", "[class*='case-study']"]:
        el = await page.query_selector(sel)
        if el:
            text = (await el.inner_text()).strip()
            if len(text) > len(description):
                description = text

    # Look for paragraphs in main content area
    paragraphs = await page.query_selector_all("main p, article p, [class*='content'] p")
    texts = []
    for p in paragraphs:
        t = (await p.inner_text()).strip()
        if t and len(t) > 20:
            texts.append(t)
    if texts:
        case_study_text = "\n\n".join(texts)

    # Brand / Agency / metadata
    brand = ""
    agency = ""
    country = ""
    category = ""
    award_level = ""
    festival = ""
    year = None

    # Try extracting metadata from structured elements
    meta_items = await page.query_selector_all("[class*='meta'] span, [class*='Meta'] span, [class*='detail'] span, dl dt, dl dd")
    meta_texts = [await el.inner_text() for el in meta_items]

    # Also try specific patterns common in award sites
    for sel, field in [
        ("[class*='brand'], [class*='Brand']", "brand"),
        ("[class*='agency'], [class*='Agency']", "agency"),
        ("[class*='country'], [class*='Country']", "country"),
        ("[class*='category'], [class*='Category']", "category"),
    ]:
        el = await page.query_selector(sel)
        if el:
            val = (await el.inner_text()).strip()
            if field == "brand":
                brand = val
            elif field == "agency":
                agency = val
            elif field == "country":
                country = val
            elif field == "category":
                category = val

    # Extract all text from page for metadata mining
    all_text = await page.inner_text("body")
    # Try to find award level
    for level in ["Grand Prix", "Gold", "Silver", "Bronze", "Shortlist"]:
        if level.lower() in all_text.lower():
            award_level = level
            break

    # Try to find festival name
    for fest in ["Cannes Lions", "Dubai Lynx", "Eurobest", "Spikes Asia"]:
        if fest.lower() in all_text.lower():
            festival = fest
            break

    # Try to find year
    year_match = re.search(r"20[12]\d", all_text)
    if year_match:
        year = int(year_match.group())

    # Video URLs
    video_urls = []
    for sel in ["video source", "iframe[src*='youtube'], iframe[src*='vimeo']", "[class*='video'] iframe"]:
        els = await page.query_selector_all(sel)
        for el in els:
            src = await el.get_attribute("src")
            if src:
                video_urls.append(src)

    # Image URLs
    image_urls = []
    imgs = await page.query_selector_all("main img, article img, [class*='campaign'] img, [class*='media'] img")
    for img in imgs:
        src = await img.get_attribute("src")
        if src and not src.endswith(".svg") and "logo" not in src.lower():
            if src.startswith("/"):
                src = f"https://www.lovethework.com{src}"
            image_urls.append(src)

    # Credits
    credits = []
    credit_rows = await page.query_selector_all("[class*='credit'] tr, [class*='Credit'] li, [class*='credits'] li")
    for row in credit_rows:
        text = (await row.inner_text()).strip()
        if ":" in text:
            role, name = text.split(":", 1)
            credits.append({"role": role.strip(), "name": name.strip()})

    return ScrapedCampaign(
        url=url,
        slug=slug,
        title=title,
        brand=brand,
        agency=agency,
        country=country,
        category=category,
        award_level=award_level,
        festival=festival,
        year=year,
        description=description,
        case_study_text=case_study_text,
        credits=credits,
        video_urls=video_urls,
        image_urls=list(dict.fromkeys(image_urls)),  # deduplicate preserving order
        raw_html=raw_html,
    )
