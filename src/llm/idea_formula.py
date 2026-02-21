"""Extract idea-making formula from existing campaign notes.

Reads campaigns/ notes, sends summary+overview to LLM,
and inserts a ## アイデアの作り方 section into the note.

Usage:
    python -m src.llm.idea_formula --vault <vault_path>
    python -m src.llm.idea_formula --vault <vault_path> --limit 5   # test batch
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter

from src.config import settings
from src.llm.processor import create_provider, load_prompt_template, render_prompt

logger = logging.getLogger(__name__)

SECTION_HEADING = "## アイデアの作り方"
OLD_SECTION_HEADING = "## 戦略構造"


@dataclass
class FormulaProgress:
    total: int = 0
    completed: int = 0
    skipped: int = 0
    failed: int = 0
    current_file: str = ""
    errors: list[str] = field(default_factory=list)


def _has_idea_section(content: str) -> bool:
    """Check if note already has a ## アイデアの作り方 section."""
    return f"\n{SECTION_HEADING}" in content or content.startswith(SECTION_HEADING)


def _extract_context(content: str) -> str:
    """Extract summary + overview sections from campaign note content.

    These sections provide enough context for strategic analysis
    without overwhelming the LLM with full details.
    """
    parts = []

    # Extract ## 概要
    m = re.search(r"## 概要\n(.+?)(?=\n## |\Z)", content, re.DOTALL)
    if m:
        parts.append(f"## 概要\n{m.group(1).strip()}")

    # Extract ## 全体像
    m = re.search(r"## 全体像\n(.+?)(?=\n---|\n## |\Z)", content, re.DOTALL)
    if m:
        parts.append(f"## 全体像\n{m.group(1).strip()}")

    return "\n\n".join(parts) if parts else ""


def _remove_old_section(content: str) -> str:
    """Remove old ## 戦略構造 section if present."""
    pattern = r"\n## 戦略構造\n.*?(?=\n## |\n---|\Z)"
    return re.sub(pattern, "", content, flags=re.DOTALL)


def _build_idea_section(dna: dict) -> str:
    """Build the ## アイデアの作り方 markdown section."""
    lines = [SECTION_HEADING]
    pattern = dna.get("pattern", "")
    if pattern:
        lines.append(f"> {pattern}")
    return "\n".join(lines)


def _insert_idea_section(content: str, section: str) -> str:
    """Insert idea section before ## メソッド (or append if not found)."""
    # Remove old ## 戦略構造 section if present
    content = _remove_old_section(content)

    # Insert between ## 全体像 and ## 背景・課題 (before the --- separator)
    # Pattern: end of 全体像 content → --- → ## 背景・課題
    m = re.search(r"(## 全体像\n.+?)(\n---\n)", content, re.DOTALL)
    if m:
        insert_pos = m.end(1)
        return content[:insert_pos] + f"\n\n{section}\n" + content[insert_pos:]

    # Fallback: insert before ## 背景・課題
    marker = "\n## 背景・課題"
    if marker in content:
        return content.replace(marker, f"\n{section}\n{marker}")

    # Fallback: insert before ## メソッド
    marker = "\n## メソッド"
    if marker in content:
        return content.replace(marker, f"\n{section}\n{marker}")

    # Last resort: append
    return content.rstrip() + f"\n\n{section}\n"


async def extract_idea_formula(
    vault_path: Path,
    limit: int | None = None,
    batch_size: int = 5,
    batch_delay: float = 1.5,
):
    """Extract idea-making formula from existing campaign notes."""
    provider = create_provider()
    template = load_prompt_template("idea_formula")
    progress = FormulaProgress()

    campaigns_dir = vault_path / "campaigns"
    if not campaigns_dir.exists():
        logger.error(f"Campaigns directory not found: {campaigns_dir}")
        return progress

    # Collect campaign notes
    md_files = sorted(campaigns_dir.glob("*.md"))
    progress.total = len(md_files)
    logger.info(f"Found {progress.total} campaign notes")

    # Filter: skip notes that already have strategic section
    to_process = []
    for md_file in md_files:
        try:
            post = frontmatter.load(str(md_file))
            if _has_idea_section(post.content):
                progress.skipped += 1
                continue
            context = _extract_context(post.content)
            if not context:
                progress.skipped += 1
                logger.warning(f"No context extracted from {md_file.name}, skipping")
                continue
            to_process.append((md_file, post, context))
        except Exception as e:
            progress.failed += 1
            progress.errors.append(f"Failed to read {md_file.name}: {e}")
            logger.error(f"Failed to read {md_file.name}: {e}")

    if limit:
        to_process = to_process[:limit]

    remaining = len(to_process)
    logger.info(
        f"Processing {remaining} notes "
        f"(skipped {progress.skipped} already done, {progress.failed} errors)"
    )
    progress.total = remaining

    for i, (md_file, post, context) in enumerate(to_process):
        title = post.metadata.get("title", md_file.stem)
        progress.current_file = md_file.name

        try:
            # Render prompt
            campaign_data = {"title": title, "campaign_context": context}
            system_prompt = render_prompt(template["system_prompt"], campaign_data)
            user_prompt = render_prompt(template["user_prompt"], campaign_data)

            # Call LLM
            start = time.monotonic()
            response = await provider.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=template.get("max_tokens", 1024),
                temperature=template.get("temperature", 0.3),
            )
            duration_ms = int((time.monotonic() - start) * 1000)

            # Parse response
            resp_content = response.content.strip()
            if "```json" in resp_content:
                resp_content = resp_content.split("```json")[1].split("```")[0].strip()
            elif "```" in resp_content:
                resp_content = resp_content.split("```")[1].split("```")[0].strip()

            dna = json.loads(resp_content)

            # Build and insert section
            section = _build_idea_section(dna)
            new_content = _insert_idea_section(post.content, section)
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
            error_msg = f"Failed to process {md_file.name}: {e}"
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
            print("Usage: python -m src.llm.idea_formula --vault <path> [--limit N]")
            sys.exit(1)

    async def _main():
        progress = await extract_idea_formula(vault, limit=limit)
        print(f"\nDone: {progress.completed} processed, {progress.skipped} skipped, {progress.failed} failed")
        if progress.errors:
            print("Errors:")
            for err in progress.errors:
                print(f"  - {err}")

    asyncio.run(_main())
