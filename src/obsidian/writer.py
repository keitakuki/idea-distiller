"""Write Obsidian Markdown notes to the vault.

Two note types:
  - inbox notes: raw scraped data (status: raw)
  - campaign notes: LLM-processed summaries (status: processed)
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

import frontmatter

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


def _sanitize_filename(title: str) -> str:
    """Sanitize a title for use as a filename (no extension)."""
    # Remove filesystem-unsafe characters
    name = re.sub(r'[\\/:*?"<>|]', "", title)
    # Collapse whitespace
    name = re.sub(r"\s+", " ", name).strip()
    # Remove leading/trailing dots
    name = name.strip(".")
    # Truncate to 100 chars
    if len(name) > 100:
        name = name[:100].rstrip()
    return name or "Untitled"


def write_inbox_note(data: dict, vault_path: Path) -> Path:
    """Write a raw inbox note from scraped campaign data.

    Creates vault/inbox/{slug}.md with full metadata in frontmatter
    and all content sections preserved verbatim.
    """
    slug = data.get("slug", "untitled")
    title = data.get("title", slug)

    # Build awards list for frontmatter
    awards_raw = data.get("awards", [])
    awards_fm = []
    for a in awards_raw:
        entry = {"level": a.get("level", "")}
        if a.get("category"):
            entry["category"] = a["category"]
        if a.get("subcategory"):
            entry["subcategory"] = a["subcategory"]
        awards_fm.append(entry)

    meta = {
        "title": title,
        "slug": slug,
        "brand": data.get("brand", ""),
        "agency": data.get("agency", ""),
        "country": data.get("country", ""),
        "festival": data.get("campaign_festival", "") or data.get("festival", ""),
        "year": data.get("campaign_year") or data.get("year"),
        "awards": awards_fm,
        "award_count_text": data.get("award_count_text", ""),
        "source_url": data.get("url", ""),
        "source": "lovethework",
        "video_urls": data.get("video_urls", []),
        "status": "raw",
    }
    # Remove empty values for cleaner frontmatter
    meta = {k: v for k, v in meta.items() if v or k == "status"}

    # Build note body
    lines = [f"# {title}\n"]

    # Description
    if data.get("description"):
        lines.append("## Description")
        lines.append(data["description"] + "\n")

    # Case Study
    if data.get("case_study_text"):
        lines.append("## Case Study")
        lines.append(data["case_study_text"] + "\n")

    # Media section
    has_media = data.get("image_paths") or data.get("image_urls") or data.get("video_urls")
    if has_media:
        lines.append("## Media")
        # Images (prefer local paths)
        if data.get("image_paths"):
            for img_path in data["image_paths"]:
                filename = Path(img_path).name
                lines.append(f"![[{filename}]]")
        elif data.get("image_urls"):
            for img_url in data["image_urls"]:
                lines.append(f"![image]({img_url})")
        # Videos
        for i, v in enumerate(data.get("video_urls", []), 1):
            lines.append(f"- [Video {i}]({v})")
        lines.append("")

    content = "\n".join(lines)
    post = frontmatter.Post(content, **meta)

    out_dir = vault_path / "inbox"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{slug}.md"
    out_path.write_text(frontmatter.dumps(post), encoding="utf-8")

    logger.info(f"Wrote inbox note: {out_path}")
    return out_path


def _image_filename(path_or_name: str) -> str:
    """Extract just the filename from a path or return as-is if already a filename."""
    return Path(path_or_name).name


def write_campaign_note(
    raw_data: dict,
    llm_data: dict,
    vault_path: Path,
) -> Path:
    """Write a processed campaign note from raw + LLM data.

    Creates vault/campaigns/{Title}.md with structured summary.
    Filename uses readable title (not slug).
    Layout: Title → 概要 → Awards → Images → 全体像 → Details → Techniques → Themes → Media
    """
    slug = raw_data.get("slug", llm_data.get("campaign_id", "untitled"))
    title = raw_data.get("title", slug)

    # Build awards data
    awards_raw = raw_data.get("awards", [])
    # Separate levels and categories for searchable frontmatter
    award_levels = sorted(set(a.get("level", "") for a in awards_raw if a.get("level")),
                          key=lambda x: ["Grand Prix", "Gold", "Silver", "Bronze"].index(x)
                          if x in ["Grand Prix", "Gold", "Silver", "Bronze"] else 99)
    award_categories = sorted(set(a.get("category", "") for a in awards_raw if a.get("category")))

    festival = raw_data.get("campaign_festival", "") or raw_data.get("festival", "")
    year = raw_data.get("campaign_year") or raw_data.get("year")
    brand = raw_data.get("brand", "")
    agency = raw_data.get("agency", "")
    country = raw_data.get("country", "")

    # Structured awards list preserving level↔category mapping
    awards_structured = [
        {"level": a.get("level", ""), "category": a.get("category", "")}
        for a in awards_raw
        if a.get("level") and a.get("category")
    ]

    # Merge LLM tags with award category tags
    llm_tags = list(llm_data.get("tags", []))
    for cat in award_categories:
        award_tag = "award/" + re.sub(r"[&:]+", "", cat).strip().lower().replace(" ", "-").replace("--", "-")
        if award_tag not in llm_tags:
            llm_tags.append(award_tag)

    meta = {
        "title": title,
        "slug": slug,
        "brand": brand,
        "agency": agency,
        "country": country,
        "festival": festival,
        "year": year,
        "award_levels": award_levels,
        "award_categories": award_categories,
        "awards": awards_structured,
        "tagline": llm_data.get("tagline", ""),
        "methods": llm_data.get("methods", []),
        "tags": llm_tags,
        "source_url": raw_data.get("url", "") or raw_data.get("source_url", ""),
        "status": "processed",
    }
    meta = {k: v for k, v in meta.items() if v or k == "status"}

    lines = [f"# {title}\n"]

    # Blockquote header: agency/brand/festival + award lines
    header_parts = []
    if agency:
        header_parts.append(agency)
    if brand:
        header_parts.append(brand)
    if festival and year:
        header_parts.append(f"{festival} {year}")
    elif festival:
        header_parts.append(festival)

    if header_parts:
        lines.append(f"> {' / '.join(header_parts)}")

    # Per-award lines with emoji + category
    award_by_level = _group_awards_by_level(awards_raw)
    if award_by_level:
        for level in ["Grand Prix", "Titanium Grand Prix", "Titanium", "Gold", "Silver", "Bronze"]:
            cats = award_by_level.get(level, [])
            if not cats:
                continue
            emoji = _AWARD_EMOJI.get(level, "")
            label = f"{emoji} {level}" if emoji else level
            for cat in cats:
                lines.append(f"> {label}: {cat}")
    elif awards_raw:
        # Fallback: no categories, just show level summary
        award_summary = _build_award_summary(awards_raw)
        if award_summary:
            lines.append(f"> {award_summary}")
    lines.append("")

    # 概要 (Summary)
    if llm_data.get("summary"):
        lines.append("## 概要")
        lines.append(llm_data["summary"] + "\n")

    # Hero image — 1 image after概要, rest in メディア at bottom
    image_paths = raw_data.get("image_paths", [])
    remaining_images = image_paths[1:] if len(image_paths) > 1 else []
    if image_paths:
        lines.append(f"![[{_image_filename(image_paths[0])}]]")
        lines.append("")

    # 全体像 (Overview)
    overview_parts = []
    for key, label in [
        ("overview_background", "背景"),
        ("overview_strategy", "戦略"),
        ("overview_idea", "アイデア"),
        ("overview_outcome", "結果"),
    ]:
        if llm_data.get(key):
            overview_parts.append(f"- **{label}**: {llm_data[key]}")

    if overview_parts:
        lines.append("## 全体像")
        lines.extend(overview_parts)
        lines.append("")
        lines.append("---\n")

    # 詳細セクション
    for key, heading in [
        ("background", "## 背景・課題"),
        ("strategy", "## 戦略"),
        ("idea", "## アイデア"),
        ("outcome", "## 結果・成果"),
    ]:
        if llm_data.get(key):
            lines.append(heading)
            lines.append(llm_data[key] + "\n")

    # メソッド (wikilink化 — グラフビューのハブになる)
    if llm_data.get("methods"):
        lines.append("## メソッド")
        for method in llm_data["methods"]:
            lines.append(f"- {_wikilink(method)}")
        lines.append("")

    # メディア (videos + remaining images)
    video_urls = raw_data.get("video_urls", [])
    if video_urls or remaining_images:
        lines.append("## メディア")
        for i, v in enumerate(video_urls, 1):
            lines.append(f"- [Video {i}]({v})")
        for img_path in remaining_images:
            lines.append(f"![[{_image_filename(img_path)}]]")
        lines.append("")

    # Footer
    source_url = raw_data.get("url", "") or raw_data.get("source_url", "")
    if source_url:
        lines.append("---")
        lines.append(f"*Source: [Love the Work]({source_url})*")
    lines.append("*Generated by Idea Distillery*\n")

    content = "\n".join(lines)
    post = frontmatter.Post(content, **meta)

    # Use readable title as filename (not slug)
    filename = _sanitize_filename(title)
    out_dir = vault_path / "campaigns"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{filename}.md"
    out_path.write_text(frontmatter.dumps(post), encoding="utf-8")

    logger.info(f"Wrote campaign note: {out_path}")
    return out_path


def _build_award_summary(awards: list[dict]) -> str:
    """Build a compact award summary with emoji for the blockquote header.

    Example: "🏆 Grand Prix x3, 🥇 Gold x2, 🥈 Silver x4"
    """
    from collections import Counter

    level_counts = Counter(a.get("level", "") for a in awards if a.get("level"))
    if not level_counts:
        return ""

    order = ["Grand Prix", "Titanium Grand Prix", "Titanium", "Gold", "Silver", "Bronze"]
    parts = []
    seen = set()
    for level in order:
        count = level_counts.get(level, 0)
        if count == 0:
            continue
        seen.add(level)
        emoji = _AWARD_EMOJI.get(level, "")
        label = f"{emoji} {level}" if emoji else level
        if count > 1:
            parts.append(f"{label} x{count}")
        else:
            parts.append(label)

    # Include any levels not in the standard order
    for level, count in level_counts.items():
        if level in seen:
            continue
        emoji = _AWARD_EMOJI.get(level, "")
        label = f"{emoji} {level}" if emoji else level
        parts.append(f"{label} x{count}" if count > 1 else label)

    return ", ".join(parts)


def _group_awards_by_level(awards: list[dict]) -> dict[str, list[str]]:
    """Group award categories by level for display."""
    from collections import defaultdict

    by_level: dict[str, list[str]] = defaultdict(list)
    for a in awards:
        level = a.get("level", "")
        cat = a.get("category", "")
        if level and cat:
            by_level[level].append(cat)
    return dict(by_level)


def copy_images_to_vault(
    image_paths: list[str],
    raw_dir: Path,
    vault_path: Path,
) -> None:
    """Copy downloaded images from raw data dir to Obsidian vault attachments folder."""
    if not image_paths:
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
