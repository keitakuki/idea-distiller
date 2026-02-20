"""Translate source English text and insert into campaign notes.

Reads campaigns/ notes, finds matching inbox/ note,
extracts Description + Case Study sections, translates via GPT-4o-mini,
and inserts a ## 和訳 section into the campaign note.

Usage:
    python -m src.llm.translator --vault <vault_path>
    python -m src.llm.translator --vault <vault_path> --limit 5   # test batch
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter

from src.config import settings
from src.llm.openai_provider import OpenAIProvider
from src.llm.processor import load_prompt_template, render_prompt

logger = logging.getLogger(__name__)


@dataclass
class TranslateProgress:
    total: int = 0
    completed: int = 0
    skipped: int = 0
    failed: int = 0
    current_file: str = ""
    errors: list[str] = field(default_factory=list)


def _has_translation_section(content: str) -> bool:
    """Check if note already has a ## 和訳 section."""
    return "\n## 和訳" in content or content.startswith("## 和訳")


def _clean_text(text: str) -> str:
    """Remove excessive blank lines (3+ consecutive newlines → 2)."""
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _clean_translation(text: str) -> str:
    """Post-process translation output: collapse excessive blank lines."""
    # Collapse 3+ consecutive newlines → 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Remove blank lines between list items (- item\n\n- item → - item\n- item)
    text = re.sub(r"(^- .+)\n\n(?=- )", r"\1\n", text, flags=re.MULTILINE)
    return text.strip()


def _extract_source_text(inbox_content: str) -> str:
    """Extract Description + Case Study sections from inbox note content."""
    parts = []

    # Extract ## Description
    m = re.search(r"## Description\n(.+?)(?=\n## |\Z)", inbox_content, re.DOTALL)
    if m:
        desc_text = m.group(1)
        # Promote **Bold** sub-headings to #### headers
        desc_text = re.sub(r"^\*\*(.+?)\*\*\s*$", r"#### \1", desc_text, flags=re.MULTILINE)
        parts.append("### Description\n" + _clean_text(desc_text))

    # Extract ## Case Study
    m = re.search(r"## Case Study\n(.+?)(?=\n## Media\b|\n## |\Z)", inbox_content, re.DOTALL)
    if m:
        case_text = m.group(1)
        # Promote **Bold** sub-headings to #### headers
        case_text = re.sub(r"^\*\*(.+?)\*\*\s*$", r"#### \1", case_text, flags=re.MULTILINE)
        parts.append("### Case Study\n" + _clean_text(case_text))

    return "\n\n".join(parts)


def _insert_translation_section(content: str, translation: str) -> str:
    """Insert ## 和訳 section before ## メソッド."""
    section = f"## 和訳\n{translation}"

    # Insert before ## メソッド
    marker = "\n## メソッド"
    if marker in content:
        return content.replace(marker, f"\n{section}\n{marker}")

    # Fallback: insert before ## メディア
    marker = "\n## メディア"
    if marker in content:
        return content.replace(marker, f"\n{section}\n{marker}")

    # Fallback: insert before footer ---
    if "\n---\n*Source:" in content:
        return content.replace("\n---\n*Source:", f"\n{section}\n\n---\n*Source:")

    # Last resort: append
    return content.rstrip() + f"\n\n{section}\n"


async def translate_campaigns(
    vault_path: Path,
    limit: int | None = None,
    batch_size: int = 5,
    batch_delay: float = 1.5,
) -> TranslateProgress:
    """Translate source text and insert into campaign notes."""
    provider = OpenAIProvider(
        api_key=settings.openai_api_key,
        model="gpt-4o-mini",
    )
    template = load_prompt_template("translate")
    progress = TranslateProgress()

    campaigns_dir = vault_path / "campaigns"
    inbox_dir = vault_path / "inbox"
    if not campaigns_dir.exists():
        logger.error(f"Campaigns directory not found: {campaigns_dir}")
        return progress

    # Build inbox slug → path lookup
    inbox_lookup: dict[str, Path] = {}
    if inbox_dir.exists():
        for md_file in inbox_dir.glob("*.md"):
            try:
                post = frontmatter.load(str(md_file))
                slug = post.metadata.get("slug", md_file.stem)
                inbox_lookup[slug] = md_file
            except Exception:
                inbox_lookup[md_file.stem] = md_file

    # Collect campaign notes to process
    md_files = sorted(campaigns_dir.glob("*.md"))
    progress.total = len(md_files)
    logger.info(f"Found {progress.total} campaign notes")

    to_process = []
    for md_file in md_files:
        try:
            post = frontmatter.load(str(md_file))
            if _has_translation_section(post.content):
                progress.skipped += 1
                continue

            slug = post.metadata.get("slug", md_file.stem)
            inbox_path = inbox_lookup.get(slug)
            if not inbox_path:
                progress.skipped += 1
                logger.debug(f"No inbox note for {slug}, skipping")
                continue

            inbox_post = frontmatter.load(str(inbox_path))
            source_text = _extract_source_text(inbox_post.content)
            if not source_text:
                progress.skipped += 1
                logger.warning(f"No source text in inbox for {slug}, skipping")
                continue

            to_process.append((md_file, post, source_text))
        except Exception as e:
            progress.failed += 1
            progress.errors.append(f"Failed to read {md_file.name}: {e}")
            logger.error(f"Failed to read {md_file.name}: {e}")

    if limit:
        to_process = to_process[:limit]

    remaining = len(to_process)
    logger.info(
        f"Processing {remaining} notes "
        f"(skipped {progress.skipped} already done/no inbox, {progress.failed} errors)"
    )
    progress.total = remaining

    for i, (md_file, post, source_text) in enumerate(to_process):
        title = post.metadata.get("title", md_file.stem)
        progress.current_file = md_file.name

        try:
            # Render prompt
            prompt_data = {"source_text": source_text}
            system_prompt = render_prompt(template["system_prompt"], prompt_data)
            user_prompt = render_prompt(template["user_prompt"], prompt_data)

            # Call LLM
            start = time.monotonic()
            response = await provider.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=template.get("max_tokens", 3000),
                temperature=template.get("temperature", 0.2),
            )
            duration_ms = int((time.monotonic() - start) * 1000)

            # Insert translation (post-process to fix formatting)
            translation = _clean_translation(response.content)
            new_content = _insert_translation_section(post.content, translation)
            post.content = new_content
            md_file.write_text(frontmatter.dumps(post), encoding="utf-8")

            cost = provider.estimate_cost(response.input_tokens, response.output_tokens)
            progress.completed += 1
            logger.info(
                f"[{progress.completed}/{progress.total}] {title} "
                f"(${cost:.4f}, {duration_ms}ms)"
            )

        except Exception as e:
            progress.failed += 1
            error_msg = f"Failed to translate {md_file.name}: {e}"
            progress.errors.append(error_msg)
            logger.error(error_msg)

        # Batch delay for rate limiting
        if (i + 1) % batch_size == 0 and i + 1 < len(to_process):
            logger.info(f"Batch pause ({batch_delay}s)...")
            await asyncio.sleep(batch_delay)

    return progress


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    vault = settings.vault_path
    limit = None

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--vault" and i + 1 < len(args):
            vault = Path(args[i + 1])
            i += 2
        elif args[i] == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1])
            i += 2
        else:
            print(f"Unknown argument: {args[i]}")
            print("Usage: python -m src.llm.translator --vault <path> [--limit N]")
            sys.exit(1)

    async def _main():
        progress = await translate_campaigns(vault, limit=limit)
        print(f"\nDone: {progress.completed} translated, {progress.skipped} skipped, {progress.failed} failed")
        if progress.errors:
            print("Errors:")
            for err in progress.errors:
                print(f"  - {err}")

    asyncio.run(_main())
