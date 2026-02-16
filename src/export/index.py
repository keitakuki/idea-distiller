from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

import frontmatter

from src.storage.files import load_json, list_json_files

logger = logging.getLogger(__name__)


def _wikilink(title: str) -> str:
    return f"[[{title}]]"


def generate_all_indices(processed_dir: Path, vault_path: Path) -> None:
    """Generate all index/MOC notes from processed campaign data."""
    campaigns = []
    for f in list_json_files(processed_dir):
        campaigns.append(load_json(f))

    if not campaigns:
        logger.warning("No processed campaigns found")
        return

    _generate_master_index(campaigns, vault_path)
    _generate_festival_indices(campaigns, vault_path)
    _generate_technique_notes(campaigns, vault_path)
    _generate_theme_notes(campaigns, vault_path)

    logger.info(f"Generated all index notes in {vault_path}")


def _generate_master_index(campaigns: list[dict], vault_path: Path) -> None:
    """Generate the master Map of Content."""
    lines = ["# Idea Distillery\n"]
    lines.append(f"Total campaigns: {len(campaigns)}\n")

    # Group by festival+year
    by_festival: dict[str, list] = defaultdict(list)
    for c in campaigns:
        key = f"{c.get('festival', 'Unknown')} {c.get('year', '')}"
        by_festival[key].append(c)

    lines.append("## Festivals\n")
    for key in sorted(by_festival.keys(), reverse=True):
        lines.append(f"- {_wikilink(key)} ({len(by_festival[key])} campaigns)")
    lines.append("")

    # Technique summary
    tech_count: dict[str, int] = defaultdict(int)
    for c in campaigns:
        for t in c.get("techniques", []):
            tech_count[t] += 1

    lines.append("## Top Techniques\n")
    for tech, count in sorted(tech_count.items(), key=lambda x: -x[1])[:20]:
        lines.append(f"- {_wikilink(tech)} ({count})")
    lines.append("")

    # Theme summary
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
    """Generate one note per festival+year."""
    by_festival: dict[str, list] = defaultdict(list)
    for c in campaigns:
        key = f"{c.get('festival', 'Unknown')} {c.get('year', '')}"
        by_festival[key.strip()] = by_festival.get(key.strip(), [])
        by_festival[key.strip()].append(c)

    out_dir = vault_path / "festivals"
    out_dir.mkdir(parents=True, exist_ok=True)

    for festival_key, clist in by_festival.items():
        lines = [f"# {festival_key}\n"]

        # Group by award level
        by_award: dict[str, list] = defaultdict(list)
        for c in clist:
            by_award[c.get("award_level", "Other")].append(c)

        for award_level in ["Grand Prix", "Gold", "Silver", "Bronze", "Shortlist", "Other"]:
            if award_level not in by_award:
                continue
            lines.append(f"## {award_level}\n")
            for c in by_award[award_level]:
                slug = c.get("slug", "")
                title = c.get("title", slug)
                brand = c.get("brand", "")
                category = c.get("category", "")
                info = f" ({brand})" if brand else ""
                if category:
                    info += f" - {category}"
                lines.append(f"- [[{slug}|{title}]]{info}")
            lines.append("")

        out_path = out_dir / f"{festival_key}.md"
        out_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"Generated festival index: {out_path}")


def _generate_technique_notes(campaigns: list[dict], vault_path: Path) -> None:
    """Generate one note per technique, linking to all campaigns using it."""
    tech_campaigns: dict[str, list] = defaultdict(list)
    for c in campaigns:
        for t in c.get("techniques", []):
            tech_campaigns[t].append(c)

    out_dir = vault_path / "techniques"
    out_dir.mkdir(parents=True, exist_ok=True)

    for tech, clist in sorted(tech_campaigns.items()):
        meta = {"type": "technique", "tags": ["technique"]}
        lines = [f"# {tech}\n"]
        lines.append("## Campaigns Using This Technique\n")
        for c in clist:
            slug = c.get("slug", "")
            title = c.get("title", slug)
            brand = c.get("brand", "")
            award = c.get("award_level", "")
            year = c.get("year", "")
            info = f" ({brand}, {award} {year})" if brand else ""
            lines.append(f"- [[{slug}|{title}]]{info}")
        lines.append("")

        content = "\n".join(lines)
        post = frontmatter.Post(content, **meta)
        out_path = out_dir / f"{tech}.md"
        out_path.write_text(frontmatter.dumps(post), encoding="utf-8")

    logger.info(f"Generated {len(tech_campaigns)} technique notes")


def _generate_theme_notes(campaigns: list[dict], vault_path: Path) -> None:
    """Generate one note per theme, linking to all campaigns under it."""
    theme_campaigns: dict[str, list] = defaultdict(list)
    for c in campaigns:
        for t in c.get("themes", []):
            theme_campaigns[t].append(c)

    out_dir = vault_path / "themes"
    out_dir.mkdir(parents=True, exist_ok=True)

    for theme, clist in sorted(theme_campaigns.items()):
        meta = {"type": "theme", "tags": ["theme"]}
        lines = [f"# {theme}\n"]
        lines.append("## Campaigns Under This Theme\n")
        for c in clist:
            slug = c.get("slug", "")
            title = c.get("title", slug)
            brand = c.get("brand", "")
            year = c.get("year", "")
            info = f" ({brand}, {year})" if brand else ""
            lines.append(f"- [[{slug}|{title}]]{info}")
        lines.append("")

        content = "\n".join(lines)
        post = frontmatter.Post(content, **meta)
        out_path = out_dir / f"{theme}.md"
        out_path.write_text(frontmatter.dumps(post), encoding="utf-8")

    logger.info(f"Generated {len(theme_campaigns)} theme notes")


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 3:
        print("Usage: python -m src.export.index <processed_dir> <vault_path>")
        sys.exit(1)

    generate_all_indices(Path(sys.argv[1]), Path(sys.argv[2]))
