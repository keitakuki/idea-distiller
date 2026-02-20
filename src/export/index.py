from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

import frontmatter

from src.storage.files import load_json, list_json_files

logger = logging.getLogger(__name__)


def _wikilink(title: str) -> str:
    return f"[[{title}]]"


def _get_festival_year(c: dict) -> tuple[str, int | None]:
    """Extract festival and year from campaign data, supporting awards array."""
    awards = c.get("awards", [])
    if awards:
        return awards[0].get("festival", "Unknown"), awards[0].get("year")
    return c.get("festival", "Unknown"), c.get("year")


def _get_primary_award(c: dict) -> str:
    """Get the highest award level from a campaign."""
    awards = c.get("awards", [])
    if awards:
        order = {"Grand Prix": 0, "Gold": 1, "Silver": 2, "Bronze": 3}
        best = min(awards, key=lambda a: order.get(a.get("level", ""), 99))
        return best.get("level", "")
    return c.get("award_level", "")


def _get_categories(c: dict) -> str:
    """Get comma-separated categories from awards."""
    awards = c.get("awards", [])
    if awards:
        return ", ".join(dict.fromkeys(a.get("category", "") for a in awards if a.get("category")))
    return c.get("category", "")


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
                slug = c.get("slug", "")
                title = c.get("title", slug)
                brand = c.get("brand", "")
                cats = _get_categories(c)
                info = f" ({brand})" if brand else ""
                if cats:
                    info += f" - {cats}"
                lines.append(f"- [[{slug}|{title}]]{info}")
            lines.append("")

        out_path = out_dir / f"{festival_key}.md"
        out_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"Generated festival index: {out_path}")


def _generate_method_notes(campaigns: list[dict], vault_path: Path) -> None:
    method_campaigns: dict[str, list] = defaultdict(list)
    for c in campaigns:
        for m in c.get("methods", []):
            method_campaigns[m].append(c)

    out_dir = vault_path / "methods"
    out_dir.mkdir(parents=True, exist_ok=True)

    for method, clist in sorted(method_campaigns.items()):
        meta = {"type": "method", "tags": ["method"]}
        lines = [f"# {method}\n"]
        lines.append("## このメソッドを使ったキャンペーン\n")
        for c in clist:
            slug = c.get("slug", "")
            title = c.get("title", slug)
            brand = c.get("brand", "")
            award = _get_primary_award(c)
            _, yr = _get_festival_year(c)
            info = f" ({brand}, {award} {yr})" if brand else ""
            lines.append(f"- [[{slug}|{title}]]{info}")
        lines.append("")

        post = frontmatter.Post("\n".join(lines), **meta)
        out_path = out_dir / f"{method}.md"
        out_path.write_text(frontmatter.dumps(post), encoding="utf-8")

    logger.info(f"Generated {len(method_campaigns)} method notes")


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 3:
        print("Usage: python -m src.export.index <processed_dir> <vault_path>")
        sys.exit(1)

    generate_all_indices(Path(sys.argv[1]), Path(sys.argv[2]))
