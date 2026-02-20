"""Generate Obsidian index/MOC notes from campaigns/ frontmatter.

Reads campaigns/ Markdown notes directly (not JSON) to generate:
  - _Index.md: Master index
  - festivals/*.md: Per-festival indices
  - methods/*.md: Per-method MOC notes
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

import frontmatter

from src.obsidian.reader import read_campaign_notes

logger = logging.getLogger(__name__)


_AWARD_EMOJI = {
    "Grand Prix": "🏆",
    "Titanium Grand Prix": "🏆",
    "Titanium": "🏆",
    "Gold": "🥇",
    "Silver": "🥈",
    "Bronze": "🥉",
}


def _wikilink(title: str) -> str:
    return f"[[{title}]]"


def _campaign_link(c: dict) -> str:
    """Build a wikilink with tagline: [[Title]] — tagline"""
    display_name = c.get("filename", c.get("title", ""))
    tagline = c.get("tagline", "")
    link = _wikilink(display_name)
    if tagline:
        return f"{link} — {tagline}"
    return link


def _get_festival_year(meta: dict) -> tuple[str, int | None]:
    """Extract festival and year from campaign metadata."""
    festival = meta.get("festival", "Unknown")
    year = meta.get("year")
    # Also check awards list as fallback
    awards = meta.get("awards", [])
    if not festival and awards:
        festival = awards[0].get("festival", "Unknown")
    if not year and awards:
        year = awards[0].get("year")
    return festival, year


def _get_primary_award(meta: dict) -> str:
    """Get the highest award level from campaign metadata."""
    awards = meta.get("awards", [])
    if awards:
        order = {"Grand Prix": 0, "Gold": 1, "Silver": 2, "Bronze": 3}
        best = min(awards, key=lambda a: order.get(a.get("level", ""), 99))
        return best.get("level", "")
    return ""


def _get_categories(meta: dict) -> str:
    """Get comma-separated categories from awards."""
    awards = meta.get("awards", [])
    if awards:
        return ", ".join(
            dict.fromkeys(a.get("category", "") for a in awards if a.get("category"))
        )
    return ""


def generate_all_indices(vault_path: Path) -> None:
    """Generate all index/MOC notes from campaigns/ Markdown frontmatter."""
    notes = read_campaign_notes(vault_path)

    if not notes:
        logger.warning("No campaign notes found")
        return

    # Extract metadata list for index generation
    campaigns = []
    for note in notes:
        meta = dict(note["metadata"])
        # Use filename stem as display name (readable title)
        meta["filename"] = note["path"].stem
        # Keep slug from frontmatter for internal tracking
        if "slug" not in meta:
            meta["slug"] = note["path"].stem
        campaigns.append(meta)

    _generate_master_index(campaigns, vault_path)
    _generate_festival_indices(campaigns, vault_path)
    _generate_method_notes(campaigns, vault_path)

    logger.info(f"Generated all index notes in {vault_path}")


def _generate_master_index(campaigns: list[dict], vault_path: Path) -> None:
    lines = ["# Idea Distillery\n"]
    lines.append(f"Total campaigns: {len(campaigns)}\n")

    # Group by festival+year
    by_festival: dict[str, list] = defaultdict(list)
    for c in campaigns:
        fest, yr = _get_festival_year(c)
        key = f"{fest} {yr or ''}".strip()
        by_festival[key].append(c)

    lines.append("## Festivals\n")
    for key in sorted(by_festival.keys(), reverse=True):
        lines.append(f"- {_wikilink(key)} ({len(by_festival[key])} campaigns)")
    lines.append("")

    method_count: dict[str, int] = defaultdict(int)
    for c in campaigns:
        for m in c.get("methods", []):
            method_count[m] += 1

    lines.append("## Methods\n")
    for method, count in sorted(method_count.items(), key=lambda x: -x[1]):
        lines.append(f"- {_wikilink(method)} ({count})")
    lines.append("")

    out_path = vault_path / "_Index.md"
    vault_path.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Generated master index: {out_path}")


def _generate_festival_indices(campaigns: list[dict], vault_path: Path) -> None:
    """Generate festival indices grouped by category, then by award level.

    Structure: Category (h2) → Award Level (h3) → Campaign list.
    Categories are sorted by their highest award level (Grand Prix first).
    Campaigns can appear in multiple categories (one per award entry).
    """
    LEVEL_ORDER = {"Grand Prix": 0, "Gold": 1, "Silver": 2, "Bronze": 3}

    by_festival: dict[str, list] = defaultdict(list)
    for c in campaigns:
        fest, yr = _get_festival_year(c)
        key = f"{fest} {yr or ''}".strip()
        by_festival[key].append(c)

    out_dir = vault_path / "festivals"
    out_dir.mkdir(parents=True, exist_ok=True)

    for festival_key, clist in by_festival.items():
        lines = [f"# {festival_key}\n"]

        # Build (category, level, campaign) tuples from awards list
        # category → level → [campaigns]
        cat_level_campaigns: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))

        # Track seen slugs per (category, level) to deduplicate
        seen: dict[tuple[str, str], set[str]] = defaultdict(set)

        def _add(cat: str, level: str, c: dict) -> None:
            slug = c.get("slug", c.get("filename", ""))
            key = (cat, level)
            if slug not in seen[key]:
                seen[key].add(slug)
                cat_level_campaigns[cat][level].append(c)

        for c in clist:
            awards = c.get("awards", [])
            if awards:
                for a in awards:
                    cat = a.get("category", "")
                    level = a.get("level", "")
                    if cat and level:
                        _add(cat, level, c)
            else:
                # Fallback: no structured awards → use award_categories × best level
                best_level = _get_primary_award(c) or "Other"
                cats = c.get("award_categories", [])
                if cats:
                    for cat in cats:
                        _add(cat, best_level, c)
                else:
                    _add("Other", best_level, c)

        # Sort categories by their highest award level
        def _category_sort_key(cat: str) -> tuple[int, str]:
            levels = cat_level_campaigns[cat]
            best = min(LEVEL_ORDER.get(lv, 99) for lv in levels)
            return (best, cat)

        for cat in sorted(cat_level_campaigns.keys(), key=_category_sort_key):
            levels = cat_level_campaigns[cat]
            lines.append(f"## {cat}\n")

            for level in ["Grand Prix", "Gold", "Silver", "Bronze", "Other"]:
                if level not in levels:
                    continue
                emoji = _AWARD_EMOJI.get(level, "")
                heading = f"{emoji} {level}" if emoji else level
                lines.append(f"### {heading}\n")
                for c in levels[level]:
                    brand = c.get("brand", "")
                    info = f" ({brand})" if brand else ""
                    lines.append(f"- {_campaign_link(c)}{info}")
                lines.append("")

        out_path = out_dir / f"{festival_key}.md"
        out_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"Generated festival index: {out_path}")


def _generate_method_notes(campaigns: list[dict], vault_path: Path) -> None:
    from src.obsidian.reader import read_tags_yaml

    method_campaigns: dict[str, list] = defaultdict(list)
    for c in campaigns:
        for m in c.get("methods", []):
            method_campaigns[m].append(c)

    # Read method definitions from _tags.yaml
    tags_data = read_tags_yaml(vault_path)
    method_definitions: dict[str, str] = tags_data.get("methods", {})

    out_dir = vault_path / "methods"
    out_dir.mkdir(parents=True, exist_ok=True)

    for method, clist in sorted(method_campaigns.items()):
        meta = {"type": "method", "tags": ["method"]}
        lines = [f"# {method}\n"]

        # Add definition if available
        definition = method_definitions.get(method, "")
        if definition:
            lines.append(f"> {definition}\n")

        lines.append("## このメソッドを使ったキャンペーン\n")
        for c in clist:
            brand = c.get("brand", "")
            award = _get_primary_award(c)
            emoji = _AWARD_EMOJI.get(award, "")
            _, yr = _get_festival_year(c)
            info = f" ({brand}, {yr})" if brand else ""
            prefix = f"{emoji} " if emoji else ""
            lines.append(f"- {prefix}{_campaign_link(c)}{info}")
        lines.append("")

        post = frontmatter.Post("\n".join(lines), **meta)
        out_path = out_dir / f"{method}.md"
        out_path.write_text(frontmatter.dumps(post), encoding="utf-8")

    logger.info(f"Generated {len(method_campaigns)} method notes")


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage: python -m src.obsidian.index <vault_path>")
        sys.exit(1)

    generate_all_indices(Path(sys.argv[1]))
