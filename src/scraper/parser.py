"""Parsers for lovethework.com pages, based on actual HTML structure.

Primary approach: Campaign Library (/work/campaigns)
  → Paginated listing of campaigns (one card per campaign, no duplicates)
  → Campaign detail pages (/work/campaigns/{slug}-{id})

Detail page structure (confirmed from HTML analysis):
  - Title: h1[data-testid="title-block-title"]
  - Subtitle: p[data-testid="page-title-block-subtext-trailing"]
    → "AGENCY, LOCATION / BRAND / YEAR"
  - Festival: p[data-testid="page-title-block-subtext-leading"]
  - Award tags: div[data-testid="page-title-block-tags"] div[data-testid="tag"]
    → Classes: tag--type_gold, tag--type_silver, tag--type_bronze, tag--type_shortlist, tag--type_grandPrix
    → Text: "1 Gold Lion", "2 Silver Lion", etc.
  - Content: h2 sections (Background, Idea, Strategy, Description, Execution, Outcome)
    → Paragraphs: p.typography--size_body-large
  - Videos: URL pattern in HTML: filespin.io/api/v1/video/{id}/1080p-wm-video-CL.mp4
  - Credits/Entries: Dynamically loaded tabs (#tab-1, #tab-2) — require click
"""

from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urlparse

from playwright.async_api import Page

from src.scraper.models import Award, CampaignEntry, ScrapedCampaign

logger = logging.getLogger(__name__)

BASE_URL = "https://www.lovethework.com"


# ---------------------------------------------------------------------------
# Tab clicking helper (Next.js SPA — needs real user-like interaction)
# ---------------------------------------------------------------------------

async def _click_tab_and_wait(
    page: Page,
    tab_selector: str,
    timeout_s: float = 20,
    poll_interval: float = 1.0,
) -> bool:
    """Click a tab and wait for aria-selected to become true.

    The site is a Next.js SPA. Tabs are React-controlled divs with
    role="tab" and aria-selected. The panel content is dynamically
    loaded after the tab switch completes.

    Tries multiple click strategies because Next.js hydration may not
    have attached event listeners when the DOM is first rendered:
      1. Wait for networkidle (JS fully loaded)
      2. Playwright page.click() (real user simulation)
      3. Playwright get_by_role().click()
      4. JS dispatchEvent with pointer/mouse events (bypasses React)

    Returns True if the tab switched successfully.
    """
    import time

    tab_texts = {"#tab-0": "Overview", "#tab-1": "Entries", "#tab-2": "Credits"}

    # First, make sure the page JS is fully loaded (Next.js hydration)
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        # networkidle may never fire on some pages, that's OK
        await asyncio.sleep(3.0)

    strategies = []

    # Strategy 1: Playwright high-level click on the selector
    async def _pw_click():
        await page.click(tab_selector, timeout=5000)

    strategies.append(("page.click", _pw_click))

    # Strategy 2: get_by_role with text
    text = tab_texts.get(tab_selector, "")
    if text:
        async def _role_click():
            await page.get_by_role("tab", name=text).click(timeout=5000)

        strategies.append(("get_by_role", _role_click))

    # Strategy 3: Click the <p> child inside the tab (React may bind there)
    async def _child_click():
        await page.click(f'{tab_selector} p', timeout=5000)

    strategies.append(("child <p> click", _child_click))

    # Strategy 4: JS dispatchEvent with full pointer event sequence
    async def _js_dispatch():
        await page.evaluate(f"""() => {{
            const tab = document.querySelector('{tab_selector}');
            if (!tab) return;
            const rect = tab.getBoundingClientRect();
            const x = rect.left + rect.width / 2;
            const y = rect.top + rect.height / 2;
            const opts = {{ bubbles: true, cancelable: true, clientX: x, clientY: y }};
            tab.dispatchEvent(new PointerEvent('pointerdown', opts));
            tab.dispatchEvent(new MouseEvent('mousedown', opts));
            tab.dispatchEvent(new PointerEvent('pointerup', opts));
            tab.dispatchEvent(new MouseEvent('mouseup', opts));
            tab.dispatchEvent(new MouseEvent('click', opts));
        }}""")

    strategies.append(("JS dispatchEvent", _js_dispatch))

    for name, click_fn in strategies:
        try:
            await click_fn()
            logger.debug(f"Executed click strategy: {name}")
        except Exception as e:
            logger.debug(f"Click strategy {name} failed: {e}")
            continue

        # Poll for aria-selected="true"
        start = time.monotonic()
        while time.monotonic() - start < timeout_s:
            is_selected = await page.evaluate(f"""() => {{
                const tab = document.querySelector('{tab_selector}');
                return tab ? tab.getAttribute('aria-selected') : null;
            }}""")
            if is_selected == "true":
                await asyncio.sleep(1.5)
                return True
            await asyncio.sleep(poll_interval)

        logger.debug(f"Strategy {name}: aria-selected did not change after {timeout_s}s")
        # Reset timeout for next strategy — use shorter timeout
        timeout_s = min(timeout_s, 10)

    logger.warning(f"Tab {tab_selector} did not switch with any strategy")
    return False

# Campaign Library filter URL encoding:
# tag=trophies@@award+level##lions+awards@@entry+type##award+sources@@lions+award@@cannes+lions##publication+dates@@year@@2025
_LIBRARY_BASE = f"{BASE_URL}/en/work/campaigns"

# Award tag CSS class → human-readable level
_AWARD_LEVEL_MAP = {
    "tag--type_grandPrix": "Grand Prix",
    "tag--type_gold": "Gold",
    "tag--type_silver": "Silver",
    "tag--type_bronze": "Bronze",
    "tag--type_shortlist": "Shortlist",
}


def _slug_from_url(url: str) -> str:
    """Extract slug from URL like /work/campaigns/a-tale-as-old-as-websites-1828157."""
    path = urlparse(url).path.rstrip("/")
    return path.split("/")[-1]


def build_library_url(
    festival: str = "cannes lions",
    year: int | None = None,
    award_levels: bool = True,
    page: int = 1,
) -> str:
    """Build a Campaign Library URL with filters.

    Args:
        festival: Festival name (e.g., "cannes lions").
        year: Filter by year (e.g., 2025).
        award_levels: If True, include award level filter (trophies).
        page: Page number for pagination.
    """
    tag_parts = []
    tag_parts.append("lions awards@@entry type")
    if award_levels:
        # Specific levels: GP, Titanium GP, Titanium, Gold, Silver (exclude Bronze & Shortlist)
        for level in ["grand prix", "titanium grand prix", "titanium", "gold", "silver"]:
            tag_parts.append(f"trophies@@award level@@{level}")
    festival_encoded = festival.replace(" ", "+")
    tag_parts.append(f"award sources@@lions award@@{festival_encoded}")
    if year:
        tag_parts.append(f"publication dates@@year@@{year}")

    tag_value = "##".join(tag_parts)
    tag_encoded = tag_value.replace("@@", "%40%40").replace("##", "%23%23").replace(" ", "+")

    url = f"{_LIBRARY_BASE}?tag={tag_encoded}"
    if page > 1:
        url += f"&page={page}"
    return url


# ---------------------------------------------------------------------------
# Campaign Library listing page
# ---------------------------------------------------------------------------

async def _scroll_to_load_all(page: Page, max_rounds: int = 20, timeout_s: float = 30) -> None:
    """Scroll down to load all lazy-loaded content.

    Has a hard timeout to prevent infinite hangs on misbehaving pages.
    """
    import time

    deadline = time.monotonic() + timeout_s
    prev_height = 0
    stable_count = 0
    for _ in range(max_rounds):
        if time.monotonic() > deadline:
            logger.debug(f"_scroll_to_load_all hit {timeout_s}s timeout")
            break
        try:
            height = await page.evaluate("document.body.scrollHeight")
        except Exception:
            break
        if height == prev_height:
            stable_count += 1
            if stable_count >= 2:
                break
        else:
            stable_count = 0
        prev_height = height
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            break
        await asyncio.sleep(1.2)


async def get_total_pages(page: Page, timeout_ms: int = 30000) -> int:
    """Get total number of pages from pagination controls.

    Waits for the pagination nav to appear (lazy-loaded) before reading.
    """
    try:
        await page.wait_for_selector(
            'nav[data-testid="pagination"]', timeout=timeout_ms
        )
    except Exception:
        # Pagination may not exist if only 1 page of results
        return 1

    page_buttons = await page.query_selector_all(
        'nav[data-testid="pagination"] button[aria-label^="Go to page"]'
    )
    max_page = 1
    for btn in page_buttons:
        label = await btn.get_attribute("aria-label") or ""
        match = re.search(r"page\s+(\d+)", label)
        if match:
            p = int(match.group(1))
            if p > max_page:
                max_page = p
    return max_page


async def extract_library_campaigns(page: Page) -> list[CampaignEntry]:
    """Extract campaign entries from a Campaign Library listing page.

    Each card has:
      - a[data-testid="base-link"] with href to /work/campaigns/{slug}-{id}
      - h3 with campaign title
      - p.body-small texts: first is "YEAR, BRAND", second is "AGENCY, Location"
      - div[data-testid="tag"] with award count badge (e.g., "4 Cannes Lions Awards")
      - img with campaign thumbnail
    """
    entries: list[CampaignEntry] = []

    cards = await page.query_selector_all('[data-testid="group-grid"] > div')
    if not cards:
        cards = await page.query_selector_all('div:has(a[href*="/work/campaigns/"])')

    logger.info(f"Found {len(cards)} campaign cards on page")

    for card in cards:
        try:
            # Campaign detail link
            link_el = await card.query_selector('a[data-testid="base-link"]')
            if not link_el:
                link_el = await card.query_selector('a[href*="/work/campaigns/"]')
            if not link_el:
                continue
            href = await link_el.get_attribute("href") or ""
            if not href:
                continue
            if href.startswith("/"):
                href = f"{BASE_URL}{href}"

            # Title from h3
            title_el = await card.query_selector("h3")
            title = (await title_el.inner_text()).strip() if title_el else ""

            # Metadata from body-small paragraphs (skip body-small-short = award badge)
            small_texts = await card.query_selector_all('p[class*="typography--size_body-small"]')
            year_brand = ""
            agency_location = ""
            text_idx = 0
            for p in small_texts:
                cls = await p.get_attribute("class") or ""
                if "body-small-short" in cls:
                    continue
                text = (await p.inner_text()).strip()
                if text_idx == 0:
                    year_brand = text   # e.g., "2025, SQUARESPACE"
                elif text_idx == 1:
                    agency_location = text  # e.g., "AREA 23, AN IPG HEALTH COMPANY, New York"
                text_idx += 1

            # Parse year and brand from "2025, BRAND_NAME"
            year = None
            brand = ""
            if year_brand:
                match = re.match(r"(\d{4}),\s*(.*)", year_brand)
                if match:
                    year = int(match.group(1))
                    brand = match.group(2).strip()
                else:
                    brand = year_brand

            # Parse agency and location
            agency = ""
            agency_loc = ""
            if agency_location:
                parts = agency_location.rsplit(",", 1)
                agency = parts[0].strip()
                agency_loc = parts[1].strip() if len(parts) > 1 else ""

            # Award badge
            award_count_text = ""
            tag_el = await card.query_selector('[data-testid="tag"]')
            if tag_el:
                award_count_text = (await tag_el.inner_text()).strip()

            # Image
            img_el = await card.query_selector("img")
            image_url = ""
            if img_el:
                image_url = await img_el.get_attribute("src") or ""

            slug = _slug_from_url(href)

            entries.append(CampaignEntry(
                url=href,
                slug=slug,
                title=title,
                brand=brand,
                agency=agency,
                agency_location=agency_loc,
                image_url=image_url,
                award_count_text=award_count_text,
                year=year,
            ))

        except Exception as e:
            logger.warning(f"Failed to parse a campaign card: {e}")
            continue

    logger.info(f"Extracted {len(entries)} campaign entries from page")
    return entries


async def has_next_page(page: Page) -> bool:
    """Check if there's a next page button that's not disabled."""
    next_btn = await page.query_selector('button[data-testid="next"]')
    if not next_btn:
        return False
    disabled = await next_btn.get_attribute("aria-disabled")
    return disabled != "true"


# ---------------------------------------------------------------------------
# Campaign detail page
# ---------------------------------------------------------------------------

def _parse_subtitle(subtitle: str) -> dict[str, str]:
    """Parse subtitle like 'AGENCY, LOCATION / BRAND / YEAR'.

    Examples:
      "PUBLICIS LONDON, London / SQUARESPACE / 2025"
      "SQUARESPACE, NEW YORK / SQUARESPACE / 2025"
    """
    parts = [p.strip() for p in subtitle.split("/")]
    result: dict[str, str] = {}

    if len(parts) >= 1:
        # First part: "AGENCY, LOCATION" or just "AGENCY"
        agency_part = parts[0]
        if "," in agency_part:
            agency_parts = agency_part.rsplit(",", 1)
            result["agency"] = agency_parts[0].strip()
            result["location"] = agency_parts[1].strip()
        else:
            result["agency"] = agency_part

    if len(parts) >= 2:
        result["brand"] = parts[1].strip()

    if len(parts) >= 3:
        year_str = parts[2].strip()
        if year_str.isdigit():
            result["year"] = year_str

    return result


def _parse_award_tag_text(text: str) -> tuple[int, str]:
    """Parse award tag text like '1 Gold Lion' or '2 Silver Lion'.

    Returns (count, level).
    """
    match = re.match(r"(\d+)\s+(.*)", text.strip())
    if match:
        count = int(match.group(1))
        rest = match.group(2).strip()
        # Normalize: "Gold Lion" → "Gold", "Silver  Lion" → "Silver"
        for level in ["Grand Prix", "Gold", "Silver", "Bronze", "Shortlist"]:
            if level.lower() in rest.lower():
                return count, level
        # "Shortlisted Cannes Lions" → "Shortlist"
        if "shortlist" in rest.lower():
            return count, "Shortlist"
        return count, rest
    return 1, text.strip()


async def _extract_awards_from_header(page: Page, festival: str = "Cannes Lions") -> list[Award]:
    """Extract award summary from the detail page header tags (count + level only).

    The page header has tags like:
      <div data-testid="tag" class="tag tag--type_gold">1 Gold Lion</div>
      <div data-testid="tag" class="tag tag--type_silver">2 Silver Lion</div>

    Returns awards without category info. Used as fallback when Entries tab fails.
    """
    awards: list[Award] = []

    tag_container = await page.query_selector('[data-testid="page-title-block-tags"]')
    if not tag_container:
        return awards

    tag_els = await tag_container.query_selector_all('[data-testid="tag"]')
    for tag_el in tag_els:
        tag_classes = await tag_el.get_attribute("class") or ""

        # Determine award level from CSS class
        level = ""
        for css_class, lvl in _AWARD_LEVEL_MAP.items():
            if css_class in tag_classes:
                level = lvl
                break

        if not level:
            continue

        # Skip shortlisted entries
        if level == "Shortlist":
            continue

        # Get count from text (e.g., "2 Silver Lion")
        text = (await tag_el.inner_text()).strip()
        count, _ = _parse_award_tag_text(text)

        # Create one Award entry per count
        for _ in range(count):
            awards.append(Award(
                level=level,
                festival=festival,
            ))

    return awards


async def _extract_awards_from_entries_tab(
    page: Page, festival: str = "Cannes Lions"
) -> list[Award]:
    """Click the Entries tab and extract awards with category info.

    The Entries tab (#tab-1) has one <div> block per main category (Film, Media, etc.).
    Each block contains:
      - <h2> with the main category name (e.g. "Film", "PR", "Media")
      - <table> with rows: [Name, Section, Category, Awards]
        where Section is a sub-section and Category is a detail category.

    Returns list of Awards with level + category (main) + subcategory populated.
    Returns empty list if tab switch fails or no awards found.
    Gracefully falls back — never raises.
    """
    awards: list[Award] = []

    try:
        switched = await _click_tab_and_wait(page, "#tab-1", timeout_s=20)
        if not switched:
            logger.info("Entries tab did not switch (slow network or missing tab)")
            return awards

        # Wait a bit for table content to render
        await asyncio.sleep(1.0)

        # Each main category is: <div><h2>Category Name</h2><table>...</table></div>
        tables = await page.query_selector_all("table")
        logger.debug(f"Found {len(tables)} tables in Entries tab")

        for table in tables:
            # Get the main category from the <h2> preceding this table.
            # Structure: <div><h2>Category</h2><table>...</table></div>
            # Try parent's h2 first, then walk up ancestors.
            main_category = await table.evaluate("""el => {
                // Check parent, grandparent, etc. for a child <h2>
                let node = el.parentElement;
                for (let i = 0; i < 3 && node; i++) {
                    const h2 = node.querySelector('h2');
                    if (h2) return h2.innerText.trim();
                    node = node.parentElement;
                }
                // Fallback: find preceding h2 sibling
                let prev = el.previousElementSibling;
                while (prev) {
                    if (prev.tagName === 'H2') return prev.innerText.trim();
                    const h2 = prev.querySelector('h2');
                    if (h2) return h2.innerText.trim();
                    prev = prev.previousElementSibling;
                }
                return '';
            }""") or ""

            rows = await table.query_selector_all("tr")
            for row in rows:
                cells = await row.query_selector_all("td")
                if not cells:
                    continue

                cell_texts = []
                for cell in cells:
                    text = (await cell.inner_text()).strip()
                    cell_texts.append(text)

                # Skip header rows ("Name | Section | Category | Awards")
                if cell_texts and cell_texts[0] == "Name":
                    continue

                if len(cell_texts) < 4:
                    continue

                section = cell_texts[1]
                detail_category = cell_texts[2]
                award_text = cell_texts[3]

                if not award_text:
                    continue

                # Parse award level
                level = ""
                award_lower = award_text.lower()
                for lvl in ["Grand Prix", "Gold", "Silver", "Bronze"]:
                    if lvl.lower() in award_lower:
                        level = lvl
                        break

                if "shortlist" in award_lower:
                    continue

                if not level:
                    continue

                awards.append(Award(
                    level=level,
                    category=main_category or section,
                    subcategory=f"{section}: {detail_category}" if section and detail_category else section or detail_category,
                    festival=festival,
                ))

        logger.info(f"Extracted {len(awards)} awards from Entries tab")

        # Switch back to Overview tab for content scraping
        await _click_tab_and_wait(page, "#tab-0", timeout_s=10)

    except Exception as e:
        logger.debug(f"Could not extract entries tab: {e}")
        try:
            await _click_tab_and_wait(page, "#tab-0", timeout_s=5)
        except Exception:
            pass

    return awards


def _parse_entry_line(text: str, festival: str) -> Award | None:
    """Try to parse a single line of text as an award entry.

    Common patterns:
      "Gold Lion - Film Craft / Sound Design"
      "Grand Prix - Design"
      "Silver - Audio & Radio / Use of Music"
    """
    text = text.strip()
    if not text:
        return None

    # Determine award level
    level = ""
    for lvl in ["Grand Prix", "Gold", "Silver", "Bronze"]:
        if lvl.lower() in text.lower():
            level = lvl
            break

    if not level:
        return None

    # Skip Shortlist
    if "shortlist" in text.lower():
        return None

    # Try to extract category after the level
    # Remove level text and common separators
    remainder = text
    for lvl in ["Grand Prix", "Gold Lion", "Silver Lion", "Bronze Lion", "Gold", "Silver", "Bronze"]:
        remainder = re.sub(re.escape(lvl), "", remainder, flags=re.IGNORECASE).strip()
    remainder = remainder.lstrip("-—–:").strip()

    category = ""
    subcategory = ""
    if remainder:
        if "/" in remainder:
            parts = remainder.split("/", 1)
            category = parts[0].strip()
            subcategory = parts[1].strip()
        else:
            category = remainder

    return Award(level=level, category=category, subcategory=subcategory, festival=festival)


def _parse_entries_text(text: str, festival: str) -> list[Award]:
    """Parse the full text content of the Entries panel into awards.

    Falls back to line-by-line parsing when structured selectors fail.
    """
    awards = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        award = _parse_entry_line(line, festival)
        if award:
            awards.append(award)
    return awards


async def _extract_content_sections(page: Page) -> dict[str, str]:
    """Extract all h2 content sections (Background, Idea, Strategy, etc.).

    Returns dict mapping section name → text content.
    """
    sections: dict[str, str] = {}

    h2_els = await page.query_selector_all("h2")
    for h2 in h2_els:
        section_name = (await h2.inner_text()).strip()
        if not section_name:
            continue

        # Collect all following p siblings until the next h2
        sibling = await h2.evaluate_handle("""
            (el) => {
                const texts = [];
                let node = el.nextElementSibling;
                while (node) {
                    if (node.tagName === 'H2') break;
                    if (node.tagName === 'P') {
                        texts.push(node.innerText.trim());
                    }
                    node = node.nextElementSibling;
                }
                return texts.join('\\n\\n');
            }
        """)
        section_text = await sibling.json_value()
        if section_text:
            sections[section_name] = section_text

    return sections


async def _extract_video_urls_from_html(page: Page) -> list[str]:
    """Extract video URLs from the page HTML.

    Videos are embedded as direct URLs in the HTML:
      https://ascentialcdn.filespin.io/api/v1/video/{id}/1080p-wm-video-CL.mp4
    """
    html = await page.content()
    # Find all video URLs (filespin video pattern)
    video_pattern = r'https://ascentialcdn\.filespin\.io/api/v1/video/[a-f0-9]+/[^"\\]+'
    urls = re.findall(video_pattern, html)
    return list(dict.fromkeys(urls))  # deduplicate, preserve order


async def parse_campaign_page(page: Page, entry: CampaignEntry) -> ScrapedCampaign:
    """Parse a campaign detail page into full structured data.

    Uses confirmed selectors from HTML analysis:
      - data-testid="title-block-title" for title
      - data-testid="page-title-block-subtext-trailing" for agency/brand/year
      - data-testid="page-title-block-subtext-leading" for festival
      - data-testid="page-title-block-tags" for award badges
      - h2 + p.typography--size_body-large for content sections
    """
    raw_html = await page.content()

    # --- Title ---
    title = entry.title
    h1 = await page.query_selector('h1[data-testid="title-block-title"]')
    if not h1:
        h1 = await page.query_selector("h1")
    if h1:
        page_title = (await h1.inner_text()).strip()
        if page_title:
            title = page_title

    # --- Subtitle: "AGENCY, LOCATION / BRAND / YEAR" ---
    brand = entry.brand
    agency = entry.agency
    country = ""

    subtitle_el = await page.query_selector('p[data-testid="page-title-block-subtext-trailing"]')
    if subtitle_el:
        subtitle_text = (await subtitle_el.inner_text()).strip()
        parsed = _parse_subtitle(subtitle_text)
        if parsed.get("brand"):
            brand = parsed["brand"]
        if parsed.get("agency"):
            agency = parsed["agency"]
        if parsed.get("location"):
            country = parsed["location"]

    # --- Festival ---
    festival_name = "Cannes Lions"
    leading_el = await page.query_selector('p[data-testid="page-title-block-subtext-leading"]')
    if leading_el:
        fest_text = (await leading_el.inner_text()).strip()
        if fest_text:
            festival_name = fest_text.title()  # "CANNES LIONS" → "Cannes Lions"

    # --- Awards: try Entries tab first (has categories), fall back to header tags ---
    year = entry.year
    awards = await _extract_awards_from_entries_tab(page, festival=festival_name)
    if awards:
        logger.debug(f"Got {len(awards)} awards from Entries tab")
    else:
        awards = await _extract_awards_from_header(page, festival=festival_name)
        logger.debug(f"Got {len(awards)} awards from header tags (no Entries tab)")
    for award in awards:
        award.year = year

    # --- Content sections ---
    sections = await _extract_content_sections(page)

    # Build description from key sections (preserve section headers)
    description_parts = []
    for key in ["Background", "Idea", "Description"]:
        if key in sections:
            description_parts.append(f"**{key}**\n{sections[key]}")
    description = "\n\n".join(description_parts)

    # Build case study from strategy/execution/outcome
    case_study_parts = []
    for key in ["Strategy", "Execution", "Outcome"]:
        if key in sections:
            case_study_parts.append(f"**{key}**\n{sections[key]}")
    case_study_text = "\n\n".join(case_study_parts)

    # If no structured sections found, fallback to all paragraphs
    if not description and not case_study_text:
        all_paragraphs = await page.query_selector_all("p.typography--size_body-large")
        texts = []
        for p in all_paragraphs:
            t = (await p.inner_text()).strip()
            if t and len(t) > 20:
                texts.append(t)
        if texts:
            description = "\n\n".join(texts)

    # --- Videos ---
    video_urls = await _extract_video_urls_from_html(page)

    # --- Images ---
    image_urls = []
    # Main presentation image
    pres_img = await page.query_selector('img[alt="Presentation Image"]')
    if pres_img:
        src = await pres_img.get_attribute("src") or ""
        if src:
            image_urls.append(src)

    # Add listing thumbnail if different
    if entry.image_url and entry.image_url not in image_urls:
        image_urls.append(entry.image_url)

    # Other content images (skip logos, storyboards of videos, and similar campaign thumbs)
    main_imgs = await page.query_selector_all('img[src*="filespin"]')
    for img in main_imgs:
        src = await img.get_attribute("src") or ""
        alt = await img.get_attribute("alt") or ""
        # Skip video storyboard thumbnails and logos
        if "storyboard" in src:
            continue
        if not src or src in image_urls:
            continue
        if "logo" in alt.lower():
            continue
        image_urls.append(src)

    # --- Credits (try clicking the Credits tab) ---
    credits = []
    try:
        switched = await _click_tab_and_wait(page, "#tab-2", timeout_s=15)
        if switched:
            credit_items = await page.query_selector_all(
                '#panel-2 li, #panel-2 tr, [role="tabpanel"]:last-of-type li'
            )
            for item in credit_items:
                text = (await item.inner_text()).strip()
                if ":" in text:
                    role, name = text.split(":", 1)
                    credits.append({"role": role.strip(), "name": name.strip()})
                elif "\t" in text:
                    parts = text.split("\t", 1)
                    credits.append({"role": parts[0].strip(), "name": parts[1].strip()})
                elif text:
                    credits.append({"role": "", "name": text})

            await _click_tab_and_wait(page, "#tab-0", timeout_s=10)
    except Exception as e:
        logger.debug(f"Could not extract credits: {e}")

    return ScrapedCampaign(
        url=entry.url,
        slug=entry.slug,
        title=title,
        brand=brand,
        agency=agency,
        country=country,
        awards=awards,
        award_count_text=entry.award_count_text,
        campaign_year=year,
        campaign_festival=festival_name,
        description=description,
        case_study_text=case_study_text,
        credits=credits,
        video_urls=video_urls,
        image_urls=list(dict.fromkeys(image_urls)),
        raw_html=raw_html,
    )
