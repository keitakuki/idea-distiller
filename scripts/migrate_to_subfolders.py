"""One-time migration: move flat inbox/ and campaigns/ files into job_id subfolders.

Reads frontmatter 'festival' + 'year' to determine job_id.
Example: festival="Cannes Lions", year=2025 → cannes2025

Usage:
    python scripts/migrate_to_subfolders.py              # dry-run (preview)
    python scripts/migrate_to_subfolders.py --execute    # actually move files
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import frontmatter  # noqa: E402

from src.config import settings  # noqa: E402


def _infer_job_id(meta: dict) -> str:
    """Infer job_id from frontmatter festival + year.

    "Cannes Lions" + 2025 → "cannes2025"
    """
    festival = meta.get("festival", "")
    year = meta.get("year")

    if not festival or not year:
        return "unknown"

    # Normalize festival name: "Cannes Lions" → "cannes"
    festival_key = re.sub(r"\s+", "", festival).lower()
    # Common mappings
    mappings = {
        "canneslions": "cannes",
        "cannes": "cannes",
        "danda": "danda",
        "d&ad": "danda",
        "oneshow": "oneshow",
        "theoneshow": "oneshow",
        "clios": "clio",
        "clio": "clio",
        "effie": "effie",
        "spikes": "spikes",
        "spikesasia": "spikes",
    }
    short_name = mappings.get(festival_key, festival_key)
    return f"{short_name}{year}"


def _migrate_directory(
    src_dir: Path,
    dry_run: bool = True,
) -> dict[str, list[str]]:
    """Migrate .md files from src_dir into job_id subfolders.

    Returns dict of job_id → list of filenames moved.
    """
    if not src_dir.exists():
        return {}

    moves: dict[str, list[str]] = {}
    # Only process top-level .md files (not already in subfolders)
    for md_file in sorted(src_dir.glob("*.md")):
        try:
            post = frontmatter.load(str(md_file))
            job_id = _infer_job_id(post.metadata)
        except Exception:
            job_id = "unknown"

        moves.setdefault(job_id, []).append(md_file.name)

        if not dry_run:
            dest_dir = src_dir / job_id
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / md_file.name
            if dest.exists():
                print(f"  SKIP (already exists): {md_file.name} → {job_id}/")
                continue
            shutil.move(str(md_file), str(dest))

    return moves


def main() -> None:
    vault = settings.vault_path
    dry_run = "--execute" not in sys.argv

    if not vault.exists():
        print(f"ERROR: Vault not found at {vault}")
        sys.exit(1)

    mode = "DRY RUN" if dry_run else "EXECUTING"
    print(f"=== Migration {mode} ===")
    print(f"Vault: {vault}\n")

    total_moved = 0
    for subdir_name in ("inbox", "campaigns"):
        subdir = vault / subdir_name
        print(f"--- {subdir_name}/ ---")
        moves = _migrate_directory(subdir, dry_run=dry_run)

        if not moves:
            print("  (no top-level files to migrate)")
            continue

        for job_id, files in sorted(moves.items()):
            print(f"  → {job_id}/ ({len(files)} files)")
            total_moved += len(files)

    print(f"\nTotal: {total_moved} files {'would be' if dry_run else ''} moved")
    if dry_run:
        print("\nRun with --execute to perform the migration:")
        print("  python scripts/migrate_to_subfolders.py --execute")


if __name__ == "__main__":
    main()
