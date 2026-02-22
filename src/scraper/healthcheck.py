"""Scan inbox notes and classify scrape failures.

Reads vault/inbox/*.md, checks content quality, and reports issues.
With --fix, sets status to 'retry' for notes that can be re-scraped.

Usage:
    python -m src.scraper.healthcheck <vault_path>
    python -m src.scraper.healthcheck <vault_path> --fix
"""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter

logger = logging.getLogger(__name__)

# Minimum content length to consider a note as having real content
_MIN_CONTENT_LEN = 200


@dataclass
class HealthResult:
    ok: list[str] = field(default_factory=list)
    parser_failure: list[str] = field(default_factory=list)
    paywall: list[str] = field(default_factory=list)
    ghost: list[str] = field(default_factory=list)  # processed but empty content
    already_processed: list[str] = field(default_factory=list)
    already_retry: list[str] = field(default_factory=list)
    # Maps slug → file path for fix_inbox to find files in subfolders
    slug_paths: dict[str, Path] = field(default_factory=dict)


def _has_content(text: str) -> bool:
    """Check if note body has Description or Case Study sections."""
    return "## Description" in text or "## Case Study" in text


def _is_metadata_anomaly(meta: dict) -> bool:
    """Check for metadata anomalies indicating a parse failure.

    - agency is a 4-digit number (year leaked into agency field)
    - awards is empty but festival is present
    """
    agency = str(meta.get("agency", ""))
    if re.fullmatch(r"\d{4}", agency):
        return True
    awards = meta.get("awards", [])
    festival = meta.get("festival", "")
    if not awards and festival:
        return True
    return False


def check_inbox(vault_path: Path, job_id: str | None = None) -> HealthResult:
    """Scan inbox notes and classify their health status.

    Args:
        job_id: If provided, only check inbox/{job_id}/. Otherwise check all subfolders + top-level.
    """
    inbox_dir = vault_path / "inbox"
    result = HealthResult()

    if not inbox_dir.exists():
        logger.error(f"Inbox directory not found: {inbox_dir}")
        return result

    if job_id:
        md_files = sorted((inbox_dir / job_id).glob("*.md")) if (inbox_dir / job_id).exists() else []
    else:
        md_files = sorted(set(inbox_dir.glob("*.md")) | set(inbox_dir.glob("*/*.md")))

    for md_file in md_files:
        try:
            post = frontmatter.load(str(md_file))
        except Exception as e:
            logger.warning(f"Could not parse {md_file.name}: {e}")
            result.parser_failure.append(md_file.name)
            continue

        status = post.metadata.get("status", "")
        slug = md_file.stem
        result.slug_paths[slug] = md_file

        if status == "processed":
            # Check content quality for ghost detection (processed but empty)
            if not _has_content(post.content) or len(post.content.strip()) < _MIN_CONTENT_LEN:
                result.ghost.append(slug)
            else:
                result.already_processed.append(slug)
            continue

        if status == "retry":
            result.already_retry.append(slug)
            continue

        if status == "paywall":
            result.paywall.append(slug)
            continue

        # Check content quality
        has_real_content = _has_content(post.content)
        content_len = len(post.content.strip())

        if has_real_content and content_len >= _MIN_CONTENT_LEN:
            if _is_metadata_anomaly(post.metadata):
                result.parser_failure.append(slug)
            else:
                result.ok.append(slug)
        elif content_len < _MIN_CONTENT_LEN and not has_real_content:
            result.parser_failure.append(slug)
        elif _is_metadata_anomaly(post.metadata):
            result.parser_failure.append(slug)
        else:
            result.ok.append(slug)

    return result


def fix_inbox(vault_path: Path, result: HealthResult, job_id: str | None = None) -> int:
    """Set status to 'retry' for parser_failure and ghost notes.

    For ghost notes, also removes the corresponding campaign note.
    Returns the number of notes updated.
    """
    fixed = 0

    for slug in result.parser_failure + result.ghost:
        md_file = result.slug_paths.get(slug)
        if not md_file or not md_file.exists():
            continue
        try:
            post = frontmatter.load(str(md_file))
            post.metadata["status"] = "retry"
            md_file.write_text(frontmatter.dumps(post), encoding="utf-8")
            fixed += 1
            logger.info(f"Set status=retry: {slug}")
            # For ghosts, also remove the fabricated campaign note
            if slug in result.ghost:
                _remove_ghost_campaign(vault_path, slug, job_id)
        except Exception as e:
            logger.error(f"Failed to fix {slug}: {e}")

    return fixed


def _remove_ghost_campaign(vault_path: Path, slug: str, job_id: str | None = None) -> None:
    """Remove campaign note for a ghost (processed-but-empty inbox note)."""
    campaigns_dir = vault_path / "campaigns"
    if job_id:
        campaigns_dir = campaigns_dir / job_id
    if not campaigns_dir.exists():
        return
    for md_file in campaigns_dir.glob("*.md"):
        try:
            post = frontmatter.load(str(md_file))
            if post.metadata.get("slug") == slug:
                md_file.unlink()
                logger.info(f"Removed ghost campaign: {md_file.name}")
                return
        except Exception:
            continue


def print_report(result: HealthResult) -> None:
    """Print a summary table of health check results."""
    total = (
        len(result.ok)
        + len(result.parser_failure)
        + len(result.paywall)
        + len(result.ghost)
        + len(result.already_processed)
        + len(result.already_retry)
    )

    print(f"\n{'='*50}")
    print(f"Inbox Health Check: {total} notes")
    print(f"{'='*50}")
    print(f"  ok              : {len(result.ok)}")
    print(f"  parser_failure  : {len(result.parser_failure)}")
    print(f"  ghost           : {len(result.ghost)}")
    print(f"  paywall         : {len(result.paywall)}")
    print(f"  already_processed: {len(result.already_processed)}")
    print(f"  already_retry   : {len(result.already_retry)}")
    print(f"{'='*50}")

    if result.parser_failure:
        print(f"\nParser failures ({len(result.parser_failure)}):")
        for slug in result.parser_failure:
            print(f"  - {slug}")

    if result.ghost:
        print(f"\nGhost campaigns ({len(result.ghost)}) — processed but empty content:")
        for slug in result.ghost:
            print(f"  - {slug}")

    if result.paywall:
        print(f"\nPaywalled ({len(result.paywall)}):")
        for slug in result.paywall:
            print(f"  - {slug}")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    args = sys.argv[1:]
    if not args:
        print("Usage: python -m src.scraper.healthcheck <vault_path> [--job JOB] [--fix]")
        sys.exit(1)

    vault = Path(args[0])
    do_fix = "--fix" in args
    job_id = None
    for i, a in enumerate(args):
        if a == "--job" and i + 1 < len(args):
            job_id = args[i + 1]
            break

    result = check_inbox(vault, job_id=job_id)
    print_report(result)

    if do_fix and (result.parser_failure or result.ghost):
        fixed = fix_inbox(vault, result, job_id=job_id)
        print(f"\nFixed {fixed} notes (status → retry)")
    elif do_fix:
        print("\nNo parser failures or ghosts to fix.")
