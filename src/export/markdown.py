from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

import frontmatter

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text.strip("-")


def _wikilink(title: str) -> str:
    return f"[[{title}]]"


def _extract_awards_info(data: dict) -> tuple[list[dict], str, str, int | None]:
    """Extract awards list and primary festival/year from data.

    Returns (awards, primary_award_str, festival, year).
    """
    awards = data.get("awards", [])
    festival = ""
    year = None

    if awards:
        # awards is a list of dicts with level, category, subcategory, festival, year
        festival = awards[0].get("festival", "")
        year = awards[0].get("year")
        order = {"Grand Prix": 0, "Gold": 1, "Silver": 2, "Bronze": 3}
        best = min(awards, key=lambda a: order.get(a.get("level", ""), 99))
        primary = best.get("level", "")
    else:
        # Legacy flat format fallback
        festival = data.get("festival", "")
        year = data.get("year")
        primary = data.get("award_level", "")

    return awards, primary, festival, year


def _copy_images_to_vault(data: dict, raw_dir: Path | None, vault_path: Path) -> None:
    """Copy downloaded images from raw data dir to Obsidian vault attachments folder."""
    image_paths = data.get("image_paths", [])
    if not image_paths or not raw_dir:
        return

    attachments_dir = vault_path / "attachments"
    attachments_dir.mkdir(parents=True, exist_ok=True)

    for rel_path in image_paths:
        src = raw_dir / rel_path
        if src.exists():
            dest = attachments_dir / src.name
            if not dest.exists():
                shutil.copy2(src, dest)
                logger.debug(f"Copied image to vault: {dest}")


def generate_campaign_note(data: dict, vault_path: Path, raw_dir: Path | None = None) -> Path:
    """Generate an Obsidian Markdown note for a single campaign."""
    title = data.get("title", data.get("slug", "Untitled"))
    slug = data.get("slug", _slugify(title))
    awards, primary_award, festival, year = _extract_awards_info(data)

    # Collect unique categories from awards
    categories = list(dict.fromkeys(a.get("category", "") for a in awards if a.get("category")))

    # Build YAML frontmatter
    meta = {
        "title": title,
        "brand": data.get("brand", ""),
        "agency": data.get("agency", ""),
        "country": data.get("country", ""),
        "festival": festival,
        "year": year,
        "categories": categories,
        "award": primary_award,
        "awards_detail": [
            f"{a.get('level', '')} - {a.get('category', '')} ({a.get('subcategory', '')})"
            for a in awards
        ] if awards else [],
        "methods": data.get("methods", []),
        "tags": data.get("tags", []),
        "source_url": data.get("url", ""),
    }
    meta = {k: v for k, v in meta.items() if v}

    # Build note body
    lines = []
    lines.append(f"# {title}\n")

    # Header info
    info_parts = []
    if data.get("brand"):
        info_parts.append(f"**Brand:** {data['brand']}")
    if data.get("agency"):
        info_parts.append(f"**Agency:** {data['agency']}")
    if primary_award and festival:
        info_parts.append(f"**Award:** {primary_award}, {festival} {year or ''}")
    if info_parts:
        lines.append(" | ".join(info_parts) + "\n")

    # Awards (all of them)
    if len(awards) > 1:
        lines.append("## Awards\n")
        for a in awards:
            level = a.get("level", "")
            cat = a.get("category", "")
            sub = a.get("subcategory", "")
            detail = f"{level} — {cat}"
            if sub:
                detail += f" / {sub}"
            lines.append(f"- {detail}")
        lines.append("")

    # Summary
    if data.get("summary_ja"):
        lines.append("## 概要\n")
        lines.append(data["summary_ja"] + "\n")
    if data.get("summary"):
        lines.append("## Summary\n")
        lines.append(data["summary"] + "\n")

    # Key Insight
    if data.get("key_insight_ja") or data.get("key_insight"):
        lines.append("## Key Insight\n")
        if data.get("key_insight_ja"):
            lines.append(data["key_insight_ja"] + "\n")
        if data.get("key_insight"):
            lines.append(f"*{data['key_insight']}*\n")

    # Methods
    if data.get("methods"):
        lines.append("## Methods\n")
        for method in data["methods"]:
            lines.append(f"- {_wikilink(method)}")
        lines.append("")

    # Description
    if data.get("description"):
        lines.append("## Description\n")
        lines.append(data["description"] + "\n")

    # Case Study
    if data.get("case_study_text"):
        lines.append("## Case Study\n")
        lines.append(data["case_study_text"] + "\n")

    # Target Audience
    if data.get("target_audience"):
        lines.append("## Target Audience\n")
        lines.append(data["target_audience"] + "\n")

    # Media Channels
    if data.get("media_channels"):
        lines.append("## Media Channels\n")
        for ch in data["media_channels"]:
            lines.append(f"- {ch}")
        lines.append("")

    # Effectiveness
    if data.get("effectiveness_notes"):
        lines.append("## Effectiveness\n")
        lines.append(data["effectiveness_notes"] + "\n")

    # Media links
    has_media = data.get("video_urls") or data.get("image_paths") or data.get("image_urls")
    if has_media:
        lines.append("## Media\n")
        for v in data.get("video_urls", []):
            lines.append(f"- [Video]({v})")
        # Prefer local image paths (downloaded for Obsidian), fall back to URLs
        if data.get("image_paths"):
            for img_path in data["image_paths"]:
                # Obsidian uses relative paths or ![[filename]] syntax
                filename = Path(img_path).name
                lines.append(f"![[{filename}]]")
        else:
            for img in data.get("image_urls", []):
                lines.append(f"- ![]({img})")
        lines.append("")

    # Credits
    if data.get("credits"):
        lines.append("## Credits\n")
        lines.append("| Role | Name |")
        lines.append("|------|------|")
        for c in data["credits"]:
            lines.append(f"| {c.get('role', '')} | {c.get('name', '')} |")
        lines.append("")

    # Festival link
    if festival and year:
        lines.append("## Festival\n")
        lines.append(f"- {_wikilink(f'{festival} {year}')}\n")

    # Source
    if data.get("url"):
        lines.append(f"---\n*Source: [{data['url']}]({data['url']})*\n")
    lines.append("*Generated by Idea Distillery*\n")

    # Copy images to vault
    _copy_images_to_vault(data, raw_dir, vault_path)

    # Write file
    content = "\n".join(lines)
    post = frontmatter.Post(content, **meta)
    out_dir = vault_path / "campaigns"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{slug}.md"
    out_path.write_text(frontmatter.dumps(post), encoding="utf-8")

    logger.info(f"Exported: {out_path}")
    return out_path
