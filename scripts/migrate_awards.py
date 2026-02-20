"""One-time migration: add structured `awards` list to campaign notes.

Reads awards from inbox notes (which have level↔category mapping)
and adds them to corresponding campaign notes matched by slug.

Usage:
    python -m scripts.migrate_awards "$VAULT"
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import frontmatter

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def migrate(vault_path: Path) -> None:
    inbox_dir = vault_path / "inbox"
    campaigns_dir = vault_path / "campaigns"

    if not inbox_dir.exists() or not campaigns_dir.exists():
        logger.error("inbox/ or campaigns/ not found")
        return

    # Build slug → awards mapping from inbox notes
    inbox_awards: dict[str, list[dict]] = {}
    for md_file in inbox_dir.glob("*.md"):
        try:
            post = frontmatter.load(str(md_file))
            slug = post.metadata.get("slug", md_file.stem)
            awards = post.metadata.get("awards", [])
            if awards:
                inbox_awards[slug] = [
                    {"level": a.get("level", ""), "category": a.get("category", "")}
                    for a in awards
                    if a.get("level") and a.get("category")
                ]
        except Exception as e:
            logger.warning(f"Failed to read inbox note {md_file.name}: {e}")

    logger.info(f"Found {len(inbox_awards)} inbox notes with awards")

    # Update campaign notes
    updated = 0
    skipped = 0
    no_match = 0

    for md_file in sorted(campaigns_dir.glob("*.md")):
        try:
            post = frontmatter.load(str(md_file))
            # Skip if already has awards
            if post.metadata.get("awards"):
                skipped += 1
                continue

            slug = post.metadata.get("slug", "")
            if not slug or slug not in inbox_awards:
                no_match += 1
                logger.debug(f"No inbox match for {md_file.name} (slug={slug})")
                continue

            post.metadata["awards"] = inbox_awards[slug]
            md_file.write_text(frontmatter.dumps(post), encoding="utf-8")
            updated += 1
        except Exception as e:
            logger.warning(f"Failed to update {md_file.name}: {e}")

    logger.info(f"Migration complete: {updated} updated, {skipped} already had awards, {no_match} no inbox match")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.migrate_awards <vault_path>")
        sys.exit(1)
    migrate(Path(sys.argv[1]))
