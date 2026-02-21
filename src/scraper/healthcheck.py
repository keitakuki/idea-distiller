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
    already_processed: list[str] = field(default_factory=list)
    already_retry: list[str] = field(default_factory=list)


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


def check_inbox(vault_path: Path) -> HealthResult:
    """Scan inbox notes and classify their health status."""
    inbox_dir = vault_path / "inbox"
    result = HealthResult()

    if not inbox_dir.exists():
        logger.error(f"Inbox directory not found: {inbox_dir}")
        return result

    for md_file in sorted(inbox_dir.glob("*.md")):
        try:
            post = frontmatter.load(str(md_file))
        except Exception as e:
            logger.warning(f"Could not parse {md_file.name}: {e}")
            result.parser_failure.append(md_file.name)
            continue

        status = post.metadata.get("status", "")
        slug = md_file.stem

        if status == "processed":
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


def fix_inbox(vault_path: Path, result: HealthResult) -> int:
    """Set status to 'retry' for parser_failure notes.

    Returns the number of notes updated.
    """
    inbox_dir = vault_path / "inbox"
    fixed = 0

    for slug in result.parser_failure:
        md_file = inbox_dir / f"{slug}.md"
        if not md_file.exists():
            continue
        try:
            post = frontmatter.load(str(md_file))
            post.metadata["status"] = "retry"
            md_file.write_text(frontmatter.dumps(post), encoding="utf-8")
            fixed += 1
            logger.info(f"Set status=retry: {slug}")
        except Exception as e:
            logger.error(f"Failed to fix {slug}: {e}")

    return fixed


def print_report(result: HealthResult) -> None:
    """Print a summary table of health check results."""
    total = (
        len(result.ok)
        + len(result.parser_failure)
        + len(result.paywall)
        + len(result.already_processed)
        + len(result.already_retry)
    )

    print(f"\n{'='*50}")
    print(f"Inbox Health Check: {total} notes")
    print(f"{'='*50}")
    print(f"  ok              : {len(result.ok)}")
    print(f"  parser_failure  : {len(result.parser_failure)}")
    print(f"  paywall         : {len(result.paywall)}")
    print(f"  already_processed: {len(result.already_processed)}")
    print(f"  already_retry   : {len(result.already_retry)}")
    print(f"{'='*50}")

    if result.parser_failure:
        print(f"\nParser failures ({len(result.parser_failure)}):")
        for slug in result.parser_failure:
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
        print("Usage: python -m src.scraper.healthcheck <vault_path> [--fix]")
        sys.exit(1)

    vault = Path(args[0])
    do_fix = "--fix" in args

    result = check_inbox(vault)
    print_report(result)

    if do_fix and result.parser_failure:
        fixed = fix_inbox(vault, result)
        print(f"\nFixed {fixed} notes (status → retry)")
    elif do_fix:
        print("\nNo parser failures to fix.")
