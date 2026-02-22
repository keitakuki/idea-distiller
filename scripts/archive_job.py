"""Archive a completed job's vault data as tar.gz.

Creates archives/{job_id}.tar.gz with clear path prefixes for restoration:
  vault/inbox/{job_id}/       → $VAULT/inbox/{job_id}/
  vault/campaigns/{job_id}/   → $VAULT/campaigns/{job_id}/
  project/data/raw/{job_id}/  → $PROJECT/data/raw/{job_id}/

A RESTORE.md is included in the archive with concrete paths.
Does NOT delete vault files (read-only backup).

Usage:
    python scripts/archive_job.py cannes2025
"""

from __future__ import annotations

import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import settings  # noqa: E402


def _images_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    """Exclude images/ subdirectory (too large, storage TBD)."""
    if "/images/" in info.name or info.name.endswith("/images"):
        return None
    return info


def archive_job(vault: Path, job_id: str) -> Path | None:
    """Archive all job data into archives/{job_id}.tar.gz.

    Archive structure (two roots clearly separated):
      vault/inbox/{job_id}/       — raw scraped notes
      vault/campaigns/{job_id}/   — LLM-processed notes
      project/data/raw/{job_id}/  — JSON backup (images excluded)
      RESTORE.md                  — restoration instructions with paths
    """
    inbox_dir = vault / "inbox" / job_id
    campaigns_dir = vault / "campaigns" / job_id
    project_root = Path(__file__).resolve().parent.parent
    raw_dir = project_root / "data" / "raw" / job_id

    # arcname prefix → source dir
    dirs = {
        f"vault/inbox/{job_id}": inbox_dir,
        f"vault/campaigns/{job_id}": campaigns_dir,
        f"project/data/raw/{job_id}": raw_dir,
    }

    existing = {label: d for label, d in dirs.items() if d.exists()}
    if not existing:
        print(f"ERROR: No data found for job '{job_id}'")
        return None

    print(f"Archiving {job_id}:")
    total_files = 0
    for label, d in existing.items():
        count = sum(1 for f in d.rglob("*") if f.is_file())
        total_files += count
        print(f"  {label + '/':<40} {count} files")

    # Generate RESTORE.md
    restore_md = f"""\
# {job_id} Archive — Restoration Guide

## Archive Structure

- `vault/`    → Obsidian vault files
- `project/`  → Project data files

## Restore Commands

```bash
VAULT="{vault}"
PROJECT="{project_root}"

# Vault files (inbox + campaigns)
tar xzf {job_id}.tar.gz --strip-components=1 -C "$VAULT" 'vault/'

# Project data (JSON backup)
tar xzf {job_id}.tar.gz --strip-components=1 -C "$PROJECT" 'project/'
```

## Paths at archive time

| Content | Path |
|---|---|
| Vault | `{vault}` |
| Project | `{project_root}` |
| Inbox | `$VAULT/inbox/{job_id}/` |
| Campaigns | `$VAULT/campaigns/{job_id}/` |
| Raw JSON | `$PROJECT/data/raw/{job_id}/` |

## Notes

- Images (`data/raw/{job_id}/images/`) are excluded (too large).
- Vault attachments (`$VAULT/attachments/`) are shared across jobs and not included.
"""

    # Create archive
    archives_dir = project_root / "archives"
    archives_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archives_dir / f"{job_id}.tar.gz"

    with tarfile.open(str(archive_path), "w:gz") as tar:
        # Add RESTORE.md
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as tmp:
            tmp.write(restore_md)
            tmp.flush()
            tar.add(tmp.name, arcname="RESTORE.md")
            Path(tmp.name).unlink()

        # Add data directories
        for label, d in existing.items():
            tar.add(str(d), arcname=label, filter=_images_filter)

    size_mb = archive_path.stat().st_size / (1024 * 1024)
    print(f"\nArchive created: {archive_path}")
    print(f"  Size: {size_mb:.1f} MB")
    print(f"  Files: {total_files}")
    print("\nVault files are NOT deleted (read-only backup).")

    return archive_path


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/archive_job.py <job_id>")
        print("Example: python scripts/archive_job.py cannes2025")
        sys.exit(1)

    job_id = sys.argv[1]
    vault = settings.vault_path

    if not vault.exists():
        print(f"ERROR: Vault not found at {vault}")
        sys.exit(1)

    archive_job(vault, job_id)


if __name__ == "__main__":
    main()
