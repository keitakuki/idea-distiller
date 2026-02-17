"""Read Obsidian Markdown notes from the vault.

Reads inbox/ notes (status: raw) for LLM processing,
and campaigns/ notes for index generation.
"""

from __future__ import annotations

import logging
from pathlib import Path

import frontmatter

logger = logging.getLogger(__name__)


def read_inbox_notes(vault_path: Path, status: str = "raw") -> list[dict]:
    """Read all inbox notes matching the given status.

    Returns list of dicts with 'metadata' (frontmatter) and 'content' (body text).
    """
    inbox_dir = vault_path / "inbox"
    if not inbox_dir.exists():
        logger.warning(f"Inbox directory not found: {inbox_dir}")
        return []

    notes = []
    for md_file in sorted(inbox_dir.glob("*.md")):
        try:
            post = frontmatter.load(str(md_file))
            note_status = post.metadata.get("status", "")
            if note_status != status:
                continue
            notes.append({
                "metadata": dict(post.metadata),
                "content": post.content,
                "path": md_file,
            })
        except Exception as e:
            logger.warning(f"Failed to read {md_file}: {e}")

    logger.info(f"Found {len(notes)} inbox notes with status={status}")
    return notes


def read_campaign_notes(vault_path: Path) -> list[dict]:
    """Read all campaign notes from campaigns/ directory.

    Returns list of dicts with 'metadata' (frontmatter) and 'content' (body text).
    """
    campaigns_dir = vault_path / "campaigns"
    if not campaigns_dir.exists():
        logger.warning(f"Campaigns directory not found: {campaigns_dir}")
        return []

    notes = []
    for md_file in sorted(campaigns_dir.glob("*.md")):
        try:
            post = frontmatter.load(str(md_file))
            notes.append({
                "metadata": dict(post.metadata),
                "content": post.content,
                "path": md_file,
            })
        except Exception as e:
            logger.warning(f"Failed to read {md_file}: {e}")

    logger.info(f"Found {len(notes)} campaign notes")
    return notes


def read_tags_yaml(vault_path: Path) -> dict[str, list[str]]:
    """Read _tags.yaml master tag list from vault root.

    Returns dict with keys 'techniques', 'themes', 'tags'.
    Returns empty lists if file doesn't exist.
    """
    import yaml

    tags_path = vault_path / "_tags.yaml"
    if not tags_path.exists():
        return {"techniques": [], "technologies": [], "themes": [], "tags": []}

    try:
        with open(tags_path) as f:
            data = yaml.safe_load(f) or {}
        return {
            "techniques": data.get("techniques", []),
            "technologies": data.get("technologies", []),
            "themes": data.get("themes", []),
            "tags": data.get("tags", []),
        }
    except Exception as e:
        logger.warning(f"Failed to read _tags.yaml: {e}")
        return {"techniques": [], "technologies": [], "themes": [], "tags": []}


def update_tags_yaml(vault_path: Path, new_tags: dict[str, list[str]]) -> None:
    """Merge new tags into _tags.yaml, preserving existing entries."""
    import yaml

    existing = read_tags_yaml(vault_path)

    for key in ["techniques", "technologies", "themes", "tags"]:
        current = set(existing.get(key, []))
        additions = set(new_tags.get(key, []))
        new_entries = additions - current
        if new_entries:
            logger.info(f"New {key}: {new_entries}")
            existing[key] = sorted(current | additions)

    tags_path = vault_path / "_tags.yaml"
    with open(tags_path, "w") as f:
        yaml.dump(existing, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
