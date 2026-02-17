"""Generate Obsidian index/MOC notes from campaigns/ frontmatter.

Reads campaigns/ Markdown notes directly (not JSON) to generate:
  - _Index.md: Master index
  - festivals/*.md: Per-festival indices
  - techniques/*.md: Per-technique MOC notes
  - themes/*.md: Per-theme MOC notes
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

import frontmatter

from src.obsidian.reader import read_campaign_notes

logger = logging.getLogger(__name__)


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
    _generate_technique_notes(campaigns, vault_path)
    _generate_technology_notes(campaigns, vault_path)
    _generate_theme_notes(campaigns, vault_path)

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

    tech_count: dict[str, int] = defaultdict(int)
    for c in campaigns:
        for t in c.get("techniques", []):
            tech_count[t] += 1

    lines.append("## Top Techniques\n")
    for tech, count in sorted(tech_count.items(), key=lambda x: -x[1])[:20]:
        lines.append(f"- {_wikilink(tech)} ({count})")
    lines.append("")

    theme_count: dict[str, int] = defaultdict(int)
    for c in campaigns:
        for t in c.get("themes", []):
            theme_count[t] += 1

    lines.append("## Top Themes\n")
    for theme, count in sorted(theme_count.items(), key=lambda x: -x[1])[:20]:
        lines.append(f"- {_wikilink(theme)} ({count})")
    lines.append("")

    out_path = vault_path / "_Index.md"
    vault_path.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Generated master index: {out_path}")


def _generate_festival_indices(campaigns: list[dict], vault_path: Path) -> None:
    by_festival: dict[str, list] = defaultdict(list)
    for c in campaigns:
        fest, yr = _get_festival_year(c)
        key = f"{fest} {yr or ''}".strip()
        by_festival[key].append(c)

    out_dir = vault_path / "festivals"
    out_dir.mkdir(parents=True, exist_ok=True)

    for festival_key, clist in by_festival.items():
        lines = [f"# {festival_key}\n"]

        by_award: dict[str, list] = defaultdict(list)
        for c in clist:
            by_award[_get_primary_award(c) or "Other"].append(c)

        for award_level in ["Grand Prix", "Gold", "Silver", "Bronze", "Other"]:
            if award_level not in by_award:
                continue
            lines.append(f"## {award_level}\n")
            for c in by_award[award_level]:
                brand = c.get("brand", "")
                cats = _get_categories(c)
                info = f" ({brand})" if brand else ""
                if cats:
                    info += f" - {cats}"
                lines.append(f"- {_campaign_link(c)}{info}")
            lines.append("")

        out_path = out_dir / f"{festival_key}.md"
        out_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"Generated festival index: {out_path}")


def _generate_technique_notes(campaigns: list[dict], vault_path: Path) -> None:
    tech_campaigns: dict[str, list] = defaultdict(list)
    for c in campaigns:
        for t in c.get("techniques", []):
            tech_campaigns[t].append(c)

    out_dir = vault_path / "techniques"
    out_dir.mkdir(parents=True, exist_ok=True)

    for tech, clist in sorted(tech_campaigns.items()):
        meta = {"type": "technique", "tags": ["technique"]}
        lines = [f"# {tech}\n"]
        lines.append("## このテクニックを使ったキャンペーン\n")
        for c in clist:
            brand = c.get("brand", "")
            award = _get_primary_award(c)
            _, yr = _get_festival_year(c)
            info = f" ({brand}, {award} {yr})" if brand else ""
            lines.append(f"- {_campaign_link(c)}{info}")
        lines.append("")

        post = frontmatter.Post("\n".join(lines), **meta)
        out_path = out_dir / f"{tech}.md"
        out_path.write_text(frontmatter.dumps(post), encoding="utf-8")

    logger.info(f"Generated {len(tech_campaigns)} technique notes")


def _generate_technology_notes(campaigns: list[dict], vault_path: Path) -> None:
    tech_campaigns: dict[str, list] = defaultdict(list)
    for c in campaigns:
        for t in c.get("technologies", []):
            tech_campaigns[t].append(c)

    if not tech_campaigns:
        return

    out_dir = vault_path / "technologies"
    out_dir.mkdir(parents=True, exist_ok=True)

    for tech, clist in sorted(tech_campaigns.items()):
        meta = {"type": "technology", "tags": ["technology"]}
        lines = [f"# {tech}\n"]
        lines.append("## このテクノロジーを使ったキャンペーン\n")
        for c in clist:
            brand = c.get("brand", "")
            award = _get_primary_award(c)
            _, yr = _get_festival_year(c)
            info = f" ({brand}, {award} {yr})" if brand else ""
            lines.append(f"- {_campaign_link(c)}{info}")
        lines.append("")

        post = frontmatter.Post("\n".join(lines), **meta)
        out_path = out_dir / f"{tech}.md"
        out_path.write_text(frontmatter.dumps(post), encoding="utf-8")

    logger.info(f"Generated {len(tech_campaigns)} technology notes")


def _generate_theme_notes(campaigns: list[dict], vault_path: Path) -> None:
    theme_campaigns: dict[str, list] = defaultdict(list)
    for c in campaigns:
        for t in c.get("themes", []):
            theme_campaigns[t].append(c)

    out_dir = vault_path / "themes"
    out_dir.mkdir(parents=True, exist_ok=True)

    for theme, clist in sorted(theme_campaigns.items()):
        meta = {"type": "theme", "tags": ["theme"]}
        lines = [f"# {theme}\n"]
        lines.append("## このテーマのキャンペーン\n")
        for c in clist:
            brand = c.get("brand", "")
            _, yr = _get_festival_year(c)
            info = f" ({brand}, {yr})" if brand else ""
            lines.append(f"- {_campaign_link(c)}{info}")
        lines.append("")

        post = frontmatter.Post("\n".join(lines), **meta)
        out_path = out_dir / f"{theme}.md"
        out_path.write_text(frontmatter.dumps(post), encoding="utf-8")

    logger.info(f"Generated {len(theme_campaigns)} theme notes")


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage: python -m src.obsidian.index <vault_path>")
        sys.exit(1)

    generate_all_indices(Path(sys.argv[1]))
