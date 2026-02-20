"""One-time migration: update method taxonomy on all campaign notes.

Changes:
1. Rule-based renames/merges:
   - Documentary as Brand Statement → Branded Narrative
   - Entertainment as Brand Statement → Branded Narrative
   - Long-Form Storytelling as Brand Statement → Branded Narrative
   - Cumulative Storytelling Architecture → Branded Narrative
   - Narrative Architecture → Branded Narrative
   - Community Hijacking → Cultural Hijacking
   - Remove: Real-Time Response, Real-Time Opportunism, Real-time Responsiveness
   - Remove: Media Arbitrage, Scarcity Play

2. LLM-based reclassification:
   - System Hijacking → Functional Repurposing / Spatial Repurposing / Value Redirection
   (Uses Anthropic Haiku to classify based on title + tagline)

Usage:
    python -m scripts.migrate_methods
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import anthropic
import frontmatter
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

VAULT = Path(os.getenv("OBSIDIAN_VAULT_PATH", ""))

# --- Rule-based mapping ---

RENAME_MAP = {
    "Documentary as Brand Statement": "Branded Narrative",
    "Entertainment as Brand Statement": "Branded Narrative",
    "Long-Form Storytelling as Brand Statement": "Branded Narrative",
    "Cumulative Storytelling Architecture": "Branded Narrative",
    "Narrative Architecture": "Branded Narrative",
    "Community Hijacking": "Cultural Hijacking",
}

REMOVE_SET = {
    "Real-Time Response",
    "Real-Time Opportunism",
    "Real-time Responsiveness",
    "Media Arbitrage",
    "Scarcity Play",
}

NEW_METHODS = {"Functional Repurposing", "Spatial Repurposing", "Value Redirection"}


# --- LLM classification for System Hijacking ---

CLASSIFY_PROMPT = """あなたはカンヌライオンズ受賞広告キャンペーンのクリエイティブ手法を分類するアナリストです。

以下のキャンペーンの「System Hijacking」メソッドを、より具体的な3つのメソッドのいずれかに再分類してください。

## 3つの新メソッド

1. **Functional Repurposing** — 既存のモノ・文書の形式に、本来と異なる機能を持たせる
   例: 処方箋→電話番号、ビール缶→電波受信機、保険約款→DV保護、段ボール→ベッド

2. **Spatial Repurposing** — 物理的空間・インフラの用途を書き換えて、別の体験を生む
   例: セーヌ川→オリンピック舞台、地雷原→蜂蜜畑、建設現場→学校、通信塔→火災検知

3. **Value Redirection** — 既存の価値の流れを読み替え、新しい経済的・社会的回路を作る
   例: ゲーム内資産→決済、音楽ストリーム→保全資金、マーケティング予算→家賃支援

## 判断基準
- 「モノ・フォーマットの機能が変わる」→ Functional Repurposing
- 「場所・空間の使い方が変わる」→ Spatial Repurposing
- 「お金・価値の流れ先が変わる」→ Value Redirection
- 迷ったら、最も本質的な変化に注目してください

## 回答形式
メソッド名のみを1行で回答してください（Functional Repurposing / Spatial Repurposing / Value Redirection）。"""


async def classify_system_hijacking(
    client: anthropic.AsyncAnthropic,
    campaigns: list[dict],
) -> dict[str, str]:
    """Classify System Hijacking campaigns into 3 new methods using Haiku."""
    results: dict[str, str] = {}
    total = len(campaigns)

    # Process in batches of 10 for efficiency
    batch_size = 10
    for i in range(0, total, batch_size):
        batch = campaigns[i : i + batch_size]
        tasks = []
        for c in batch:
            user_msg = f"タイトル: {c['title']}\nタグライン: {c['tagline']}"
            tasks.append(
                client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=20,
                    temperature=0,
                    system=CLASSIFY_PROMPT,
                    messages=[{"role": "user", "content": user_msg}],
                )
            )
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        for c, resp in zip(batch, responses):
            if isinstance(resp, Exception):
                logger.warning(f"LLM error for {c['slug']}: {resp}")
                results[c["slug"]] = "Functional Repurposing"  # fallback
            else:
                answer = resp.content[0].text.strip()
                if answer not in NEW_METHODS:
                    logger.warning(f"Unexpected answer for {c['slug']}: {answer}, defaulting to Functional Repurposing")
                    answer = "Functional Repurposing"
                results[c["slug"]] = answer

        done = min(i + batch_size, total)
        logger.info(f"Classified {done}/{total} System Hijacking campaigns")

    return results


def apply_rule_based(methods: list[str]) -> list[str]:
    """Apply renames, merges, and removals to a method list."""
    new_methods = []
    seen = set()
    for m in methods:
        if m in REMOVE_SET:
            continue
        if m in RENAME_MAP:
            m = RENAME_MAP[m]
        if m not in seen:
            new_methods.append(m)
            seen.add(m)
    return new_methods


async def main() -> None:
    if not VAULT.exists():
        logger.error(f"Vault not found: {VAULT}")
        sys.exit(1)

    campaigns_dir = VAULT / "campaigns"

    # Phase 1: Collect System Hijacking campaigns for LLM classification
    sh_campaigns = []
    all_notes: list[tuple[Path, frontmatter.Post]] = []

    for md_file in sorted(campaigns_dir.glob("*.md")):
        try:
            post = frontmatter.load(str(md_file))
            all_notes.append((md_file, post))
            methods = post.metadata.get("methods", [])
            if "System Hijacking" in methods:
                sh_campaigns.append({
                    "slug": post.metadata.get("slug", md_file.stem),
                    "title": post.metadata.get("title", md_file.stem),
                    "tagline": post.metadata.get("tagline", ""),
                })
        except Exception as e:
            logger.warning(f"Failed to read {md_file.name}: {e}")

    logger.info(f"Total campaign notes: {len(all_notes)}")
    logger.info(f"System Hijacking campaigns to reclassify: {len(sh_campaigns)}")

    # Phase 2: LLM classification
    sh_map: dict[str, str] = {}
    if sh_campaigns:
        client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        sh_map = await classify_system_hijacking(client, sh_campaigns)
        logger.info(f"LLM classification complete. Distribution:")
        from collections import Counter
        dist = Counter(sh_map.values())
        for method, count in sorted(dist.items()):
            logger.info(f"  {method}: {count}")

    # Phase 3: Apply all changes
    updated = 0
    for md_file, post in all_notes:
        methods = post.metadata.get("methods", [])
        if not methods:
            continue

        slug = post.metadata.get("slug", md_file.stem)
        original = list(methods)

        # Replace System Hijacking with new method
        if "System Hijacking" in methods:
            new_method = sh_map.get(slug, "Functional Repurposing")
            methods = [new_method if m == "System Hijacking" else m for m in methods]

        # Apply rule-based changes
        methods = apply_rule_based(methods)

        if methods != original:
            post.metadata["methods"] = methods
            md_file.write_text(frontmatter.dumps(post), encoding="utf-8")
            updated += 1

    logger.info(f"Migration complete: {updated} notes updated out of {len(all_notes)}")


if __name__ == "__main__":
    asyncio.run(main())
