"""Prompt testing workflow.

Sets up an isolated test vault, runs the processor, and shows results
side-by-side with existing campaign notes for comparison.

Usage:
    # Random 5 notes
    python scripts/test_prompt.py

    # Specific count
    python scripts/test_prompt.py --count 3

    # Specific slugs
    python scripts/test_prompt.py --slugs slug1 slug2 slug3

    # Clean up previous test results
    python scripts/test_prompt.py --clean
"""

from __future__ import annotations

import argparse
import asyncio
import random
import shutil
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import frontmatter

from src.config import settings
from src.llm.processor import process_from_vault

TEST_VAULT = Path(__file__).resolve().parent.parent / "data" / "test-vault"


def setup_test_vault(
    source_vault: Path,
    slugs: list[str] | None = None,
    count: int = 5,
) -> list[str]:
    """Create test vault with a subset of inbox notes (status reset to raw)."""
    # Create structure
    for d in ["inbox", "campaigns"]:
        (TEST_VAULT / d).mkdir(parents=True, exist_ok=True)

    # Copy _tags.yaml for method/tag consistency
    tags_src = source_vault / "_tags.yaml"
    if tags_src.exists():
        shutil.copy2(tags_src, TEST_VAULT / "_tags.yaml")

    inbox_dir = source_vault / "inbox"
    all_notes = sorted(inbox_dir.glob("*.md"))

    if slugs:
        # Find specific slugs
        selected = [f for f in all_notes if f.stem in slugs]
        missing = set(slugs) - {f.stem for f in selected}
        if missing:
            print(f"Warning: slugs not found in inbox: {missing}")
    else:
        # Random selection
        selected = random.sample(all_notes, min(count, len(all_notes)))

    copied_slugs = []
    for src in selected:
        post = frontmatter.load(str(src))
        post.metadata["status"] = "raw"
        dest = TEST_VAULT / "inbox" / src.name
        dest.write_text(frontmatter.dumps(post), encoding="utf-8")
        copied_slugs.append(src.stem)

    # Clear previous test campaigns
    for f in (TEST_VAULT / "campaigns").glob("*.md"):
        f.unlink()

    print(f"Test vault: {TEST_VAULT}")
    print(f"Copied {len(copied_slugs)} inbox notes (status → raw)")
    return copied_slugs


def show_comparison(slugs: list[str], source_vault: Path):
    """Show side-by-side comparison of test vs existing campaign notes."""
    campaigns_dir = source_vault / "campaigns"
    test_campaigns_dir = TEST_VAULT / "campaigns"

    # Build slug→filename mapping for source campaigns
    source_map: dict[str, Path] = {}
    if campaigns_dir.exists():
        for f in campaigns_dir.glob("*.md"):
            try:
                post = frontmatter.load(str(f))
                s = post.metadata.get("slug", f.stem)
                source_map[s] = f
            except Exception:
                pass

    for slug in slugs:
        print(f"\n{'='*80}")
        print(f"  {slug}")
        print(f"{'='*80}")

        # Find test result
        test_file = None
        for f in test_campaigns_dir.glob("*.md"):
            try:
                post = frontmatter.load(str(f))
                if post.metadata.get("slug") == slug:
                    test_file = f
                    break
            except Exception:
                pass

        if not test_file:
            print("  [TEST] No output generated")
            continue

        test_post = frontmatter.load(str(test_file))

        # Show test result
        print(f"\n--- NEW (test) ---")
        print(f"  tagline:  {test_post.metadata.get('tagline', '(none)')}")
        _print_section(test_post.content, "概要")
        _print_section(test_post.content, "全体像")

        # Show existing if available
        source_file = source_map.get(slug)
        if source_file and source_file.exists():
            source_post = frontmatter.load(str(source_file))
            print(f"\n--- OLD (existing) ---")
            print(f"  tagline:  {source_post.metadata.get('tagline', '(none)')}")
            _print_section(source_post.content, "概要")
            _print_section(source_post.content, "全体像")
        else:
            print(f"\n--- OLD (existing) ---")
            print("  (no existing campaign note)")


def _print_section(content: str, heading: str):
    """Extract and print a section from markdown content."""
    import re
    pattern = rf"## {re.escape(heading)}\n(.+?)(?=\n## |\n---|\Z)"
    m = re.search(pattern, content, re.DOTALL)
    if m:
        text = m.group(1).strip()
        # Indent for readability
        for line in text.split("\n"):
            print(f"  {line}")


def clean():
    """Remove test vault."""
    if TEST_VAULT.exists():
        shutil.rmtree(TEST_VAULT)
        print(f"Cleaned: {TEST_VAULT}")
    else:
        print("Nothing to clean")


async def run_test(vault: Path):
    """Run processor on test vault."""
    print(f"\nRunning LLM processor on test vault...")
    print(f"{'─'*40}")
    async for processed, progress in process_from_vault(vault):
        if processed:
            print(
                f"  [{progress.completed}/{progress.total}] "
                f"{processed.campaign_id}: {processed.tagline}"
            )
        else:
            print(f"  [FAILED] {progress.errors[-1]}")
    print(f"{'─'*40}")
    print(f"Done: {progress.completed} processed, {progress.failed} failed")


def main():
    import logging
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    # Keep our processor logs visible
    logging.getLogger("src.llm.processor").setLevel(logging.INFO)

    parser = argparse.ArgumentParser(description="Prompt testing workflow")
    parser.add_argument("--count", "-n", type=int, default=5, help="Number of random notes to test")
    parser.add_argument("--slugs", "-s", nargs="+", help="Specific slugs to test")
    parser.add_argument("--clean", action="store_true", help="Clean up test vault")
    parser.add_argument("--vault", type=str, default=None, help="Source vault path")
    parser.add_argument("--setup-only", action="store_true", help="Set up test vault without running LLM")
    parser.add_argument("--compare-only", action="store_true", help="Show comparison without re-running LLM")
    args = parser.parse_args()

    if args.clean:
        clean()
        return

    source_vault = Path(args.vault) if args.vault else settings.vault_path

    if args.compare_only:
        # Just show comparison for existing test results
        slugs = []
        for f in (TEST_VAULT / "campaigns").glob("*.md"):
            try:
                post = frontmatter.load(str(f))
                slugs.append(post.metadata.get("slug", f.stem))
            except Exception:
                pass
        if slugs:
            show_comparison(slugs, source_vault)
        else:
            print("No test results found. Run without --compare-only first.")
        return

    slugs = setup_test_vault(source_vault, slugs=args.slugs, count=args.count)

    if args.setup_only:
        print("\nSetup complete. Run processor manually:")
        print(f"  python -m src.llm.processor --vault {TEST_VAULT}")
        return

    asyncio.run(run_test(TEST_VAULT))
    show_comparison(slugs, source_vault)

    print(f"\n\nTest vault preserved at: {TEST_VAULT}")
    print(f"Full notes: {TEST_VAULT / 'campaigns'}")
    print(f"Clean up: python scripts/test_prompt.py --clean")


if __name__ == "__main__":
    main()
