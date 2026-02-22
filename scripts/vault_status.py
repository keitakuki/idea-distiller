"""Vault status check — per-job overview of the pipeline state."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as `python -m scripts.vault_status` or `python scripts/vault_status.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import settings  # noqa: E402


def _count(directory: Path, suffix: str = ".md") -> int:
    if not directory.exists():
        return -1  # directory missing
    return sum(1 for f in directory.iterdir() if f.suffix == suffix)


def _grep_count(directory: Path, pattern: str) -> int:
    if not directory.exists():
        return 0
    count = 0
    for f in directory.iterdir():
        if f.suffix != ".md":
            continue
        try:
            if pattern in f.read_text(encoding="utf-8"):
                count += 1
        except Exception:
            pass
    return count


def _status_distribution(directory: Path) -> dict[str, int]:
    dist: dict[str, int] = {}
    if not directory.exists():
        return dist
    for f in directory.iterdir():
        if f.suffix != ".md":
            continue
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.startswith("status:"):
                    status = line.split(":", 1)[1].strip()
                    dist[status] = dist.get(status, 0) + 1
                    break
        except Exception:
            pass
    return dist


def _discover_jobs(vault: Path) -> list[str]:
    """Discover job_id subfolders from inbox/ and campaigns/."""
    jobs: set[str] = set()
    for subdir_name in ("inbox", "campaigns"):
        subdir = vault / subdir_name
        if subdir.exists():
            for d in subdir.iterdir():
                if d.is_dir() and not d.name.startswith("."):
                    jobs.add(d.name)
    return sorted(jobs)


def _has_toplevel_md(vault: Path) -> bool:
    """Check if there are top-level .md files in inbox/ or campaigns/ (pre-migration)."""
    for subdir_name in ("inbox", "campaigns"):
        subdir = vault / subdir_name
        if subdir.exists():
            for f in subdir.iterdir():
                if f.suffix == ".md":
                    return True
    return False


def _print_job(vault: Path, job_id: str) -> None:
    """Print status for a single job."""
    inbox_dir = vault / "inbox" / job_id
    campaigns_dir = vault / "campaigns" / job_id

    inbox_count = _count(inbox_dir) if inbox_dir.exists() else 0
    camp_count = _count(campaigns_dir) if campaigns_dir.exists() else 0
    total = max(inbox_count, camp_count)

    print(f"\n=== {job_id} ({total} campaigns) ===")

    # Inbox status distribution
    dist = _status_distribution(inbox_dir)
    if dist:
        parts = [f"{status}: {count}" for status, count in sorted(dist.items(), key=lambda x: -x[1])]
        print(f"  inbox:      {', '.join(parts)}")
    else:
        print("  inbox:      (なし)")

    # Campaign section coverage
    if camp_count > 0:
        idea = _grep_count(campaigns_dir, "## アイデアの作り方")
        trans = _grep_count(campaigns_dir, "## 和訳")
        print(f"  campaigns:  {camp_count} (アイデアの作り方: {idea}, 和訳: {trans})")
    else:
        print("  campaigns:  (なし)")

    # Status summary
    raw = dist.get("raw", 0)
    retry = dist.get("retry", 0)
    paywall = dist.get("paywall", 0)

    items = []
    if raw:
        items.append(f"raw: {raw}")
    if retry:
        items.append(f"retry: {retry}")
    if paywall:
        items.append(f"paywall: {paywall}")

    if items:
        print(f"  状態: 処理中（残り {', '.join(items)}）")
    elif camp_count > 0:
        idea_missing = camp_count - _grep_count(campaigns_dir, "## アイデアの作り方")
        trans_missing = camp_count - _grep_count(campaigns_dir, "## 和訳")
        if idea_missing or trans_missing:
            extras = []
            if idea_missing:
                extras.append(f"アイデアの作り方なし: {idea_missing}")
            if trans_missing:
                extras.append(f"和訳なし: {trans_missing}")
            print(f"  状態: ほぼ完了（{', '.join(extras)}）")
        else:
            print("  状態: 完了")
    else:
        print("  状態: 未処理")


def main() -> None:
    vault = settings.vault_path
    if not vault.exists():
        print(f"ERROR: Vault not found at {vault}")
        sys.exit(1)

    print(f"Vault: {vault}")

    # Discover jobs
    jobs = _discover_jobs(vault)
    has_toplevel = _has_toplevel_md(vault)

    if not jobs and not has_toplevel:
        print("\n(ジョブなし)")
        return

    # Print per-job stats
    for job_id in jobs:
        _print_job(vault, job_id)

    # Warn about top-level files (pre-migration)
    if has_toplevel:
        inbox_top = _count(vault / "inbox")
        camp_top = _count(vault / "campaigns")
        print("\n=== (未分類 — トップレベル) ===")
        if inbox_top > 0:
            dist = _status_distribution(vault / "inbox")
            parts = [f"{status}: {count}" for status, count in sorted(dist.items(), key=lambda x: -x[1])]
            print(f"  inbox:      {', '.join(parts)}")
        if camp_top > 0:
            print(f"  campaigns:  {camp_top}")
        print("  ※ `python scripts/migrate_to_subfolders.py` で移行してください")

    # Shared resources
    print("\n=== 共有リソース ===")
    for name in ("methods", "festivals"):
        d = vault / name
        n = _count(d)
        if n >= 0:
            print(f"  {name + '/':<18} {n}")
    att = vault / "attachments"
    att_count = sum(1 for _ in att.iterdir()) if att.exists() else 0
    print(f"  {'attachments/':<18} {att_count}")
    for f in ("_Index.md", "_tags.yaml"):
        exists = (vault / f).exists()
        print(f"  {f:<19} {'OK' if exists else '*** なし ***'}")


if __name__ == "__main__":
    main()
