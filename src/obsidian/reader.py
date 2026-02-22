"""Read Obsidian Markdown notes from the vault.

Reads inbox/ notes (status: raw) for LLM processing,
and campaigns/ notes for index generation.
"""

from __future__ import annotations

import logging
from pathlib import Path

import frontmatter

logger = logging.getLogger(__name__)


def read_inbox_notes(vault_path: Path, status: str = "raw", job_id: str | None = None) -> list[dict]:
    """Read all inbox notes matching the given status.

    Args:
        vault_path: Obsidian vault root path.
        status: Filter by frontmatter status field.
        job_id: If provided, only read from inbox/{job_id}/.
                If None, read from inbox/**/*.md (recursive) + inbox/*.md (back-compat).

    Returns list of dicts with 'metadata' (frontmatter) and 'content' (body text).
    """
    inbox_dir = vault_path / "inbox"
    if not inbox_dir.exists():
        logger.warning(f"Inbox directory not found: {inbox_dir}")
        return []

    if job_id:
        md_files = sorted((inbox_dir / job_id).glob("*.md")) if (inbox_dir / job_id).exists() else []
    else:
        # Recursive glob for subfolders + top-level files (back-compat)
        md_files = sorted(set(inbox_dir.glob("*.md")) | set(inbox_dir.glob("*/*.md")))

    notes = []
    for md_file in md_files:
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


def read_campaign_notes(vault_path: Path, job_id: str | None = None) -> list[dict]:
    """Read all campaign notes from campaigns/ directory.

    Args:
        vault_path: Obsidian vault root path.
        job_id: If provided, only read from campaigns/{job_id}/.
                If None, read from campaigns/**/*.md (recursive) + campaigns/*.md (back-compat).

    Returns list of dicts with 'metadata' (frontmatter) and 'content' (body text).
    """
    campaigns_dir = vault_path / "campaigns"
    if not campaigns_dir.exists():
        logger.warning(f"Campaigns directory not found: {campaigns_dir}")
        return []

    if job_id:
        md_files = sorted((campaigns_dir / job_id).glob("*.md")) if (campaigns_dir / job_id).exists() else []
    else:
        md_files = sorted(set(campaigns_dir.glob("*.md")) | set(campaigns_dir.glob("*/*.md")))

    notes = []
    for md_file in md_files:
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


def read_tags_yaml(vault_path: Path) -> dict:
    """Read _tags.yaml master tag list from vault root.

    Returns dict with keys 'methods' (dict: name→definition) and 'tags' (list).
    """
    import yaml

    tags_path = vault_path / "_tags.yaml"
    if not tags_path.exists():
        return {"methods": {}, "tags": []}

    try:
        with open(tags_path) as f:
            data = yaml.safe_load(f) or {}

        methods_raw = data.get("methods", {})
        # Support both dict {name: definition} and list [name, ...] formats
        if isinstance(methods_raw, list):
            methods = {m: "" for m in methods_raw}
        elif isinstance(methods_raw, dict):
            methods = methods_raw
        else:
            methods = {}

        return {
            "methods": methods,
            "tags": data.get("tags", []),
        }
    except Exception as e:
        logger.warning(f"Failed to read _tags.yaml: {e}")
        return {"methods": {}, "tags": []}


def update_tags_yaml(vault_path: Path, new_tags: dict) -> None:
    """Merge new tags into _tags.yaml.

    methods: auto-add with definitions from method_definitions dict.
    tags: auto-merge as before.
    """
    import yaml

    existing = read_tags_yaml(vault_path)

    # Methods: auto-add new methods with definitions
    existing_method_names = set(existing.get("methods", {}).keys())
    new_methods = set(new_tags.get("methods", []))
    method_definitions = new_tags.get("method_definitions", {})
    unknown_methods = new_methods - existing_method_names
    if unknown_methods:
        for method_name in unknown_methods:
            definition = method_definitions.get(method_name, "")
            existing["methods"][method_name] = definition
            logger.info(f"New method added to _tags.yaml: {method_name}: {definition}")

    # Tags: auto-merge
    current_tags = set(existing.get("tags", []))
    new_tag_entries = set(new_tags.get("tags", []))
    added_tags = new_tag_entries - current_tags
    if added_tags:
        logger.info(f"New tags: {added_tags}")
        existing["tags"] = sorted(current_tags | new_tag_entries)

    tags_path = vault_path / "_tags.yaml"
    with open(tags_path, "w") as f:
        yaml.dump(existing, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
