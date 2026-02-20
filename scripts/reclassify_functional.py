"""Re-classify Functional Repurposing campaigns that may be Value Redirection or Spatial Repurposing.

The initial migration was too conservative, putting 121/147 into Functional Repurposing.
This script re-classifies with a stronger prompt emphasizing the distinctions.

Usage:
    python -m scripts.reclassify_functional
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections import Counter
from pathlib import Path

import anthropic
import frontmatter
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

VAULT = Path(os.getenv("OBSIDIAN_VAULT_PATH", ""))

CLASSIFY_PROMPT = """あなたはカンヌライオンズ受賞広告キャンペーンのクリエイティブ手法を分類するアナリストです。

キャンペーンのタイトルとタグラインを見て、以下の3つのメソッドのうち**最も本質的なもの**を1つ選んでください。

## 3つのメソッド

### 1. Functional Repurposing
**モノ・文書・フォーマットの「機能」を変える。**
形はそのままだが、果たす役割が変わる。

典型例:
- 処方箋の刻印コード → 電話番号（薬を識別するコードが、助けを求める番号に）
- ビール缶 → 電波受信機（飲料容器が通信アンテナに）
- 保険約款に3語追加 → DV保護（契約書が盾に）
- 医学教科書 → 黒人の身体の証言（教科書が告発に）
- 視力検査をメニューに埋め込む（メニューが検査ツールに）
- 処方箋ラベルに発光素材（ラベルが懐中電灯に）

### 2. Spatial Repurposing
**物理的な「場所・空間・インフラ」の使い方を変える。**
空間の意味や用途が書き換わる。

典型例:
- セーヌ川6キロ → オリンピック開会式の舞台（川が劇場に）
- 地雷原 → 蜂蜜畑（危険地帯が農地に）
- 建設現場 → 学校（作業場が教室に）
- 国会議事堂 → 子どもの教室（権力の場が学びの場に）
- 通信塔 → 火災検知（通信インフラが防災インフラに）
- バス停 → 防犯拠点（待機場所が安全拠点に）
- 廃墟のネオンサイン → DV統計表示（看板が告発に）
- 閉店した店舗 → 地下鉄の募金パネル（店が寄付窓口に）
- 広告インフラ → 障害者包摂の設計（排除の空間が包摂の空間に）

### 3. Value Redirection
**経済的・社会的「価値の流れ」を新しい回路に繋ぎ変える。**
お金、ポイント、データ、注目などの「流れ」が別の方向に向かう。

典型例:
- ゲーム内資産 → 実店舗の決済（仮想通貨が現実の通貨に）
- 音楽ストリーム → 保全資金（再生回数が自然保護に）
- マーケティング予算 → バーの家賃（広告費が直接支援に）
- 母乳育児 → 14.3%利息の銀行口座（育児行為が貯蓄に）
- 選手のゼッケン番号 → 商品価格（背番号が値札に）
- チームバスの移動軌跡 → 購買権（距離がポイントに）
- 難民女性のレシピ → IP化して世界販売（知恵が収入源に）
- 飛行機の機体番号 → 割引コード（識別番号がクーポンに）
- 選手の発言 → クーポンコード（言葉がお金に）
- 紙吹雪 → クーポン（紙切れが価値に）
- 広告枠を買って何も流さない（広告費が「不在」の証明に）
- テレビの握手 → QRコード取引（映像がコマースに）

## 判断のコツ
- 「形あるモノの役割が変わる」→ Functional Repurposing
- 「場所・空間の意味が変わる」→ Spatial Repurposing
- 「お金・価値・注目が別の方向に流れる」→ Value Redirection
- **Value Redirectionの見分け方**: 「X → Y」の変換で、Yが経済的価値（金、クーポン、ポイント、売上、資金、収益）や社会的価値（注目、支援、採用）に関わるなら Value Redirection

## 回答形式
メソッド名のみを1行で回答（Functional Repurposing / Spatial Repurposing / Value Redirection）。"""


async def classify_batch(
    client: anthropic.AsyncAnthropic,
    campaigns: list[dict],
) -> dict[str, str]:
    """Classify campaigns into 3 methods using Haiku with rate limiting."""
    results: dict[str, str] = {}
    total = len(campaigns)
    valid = {"Functional Repurposing", "Spatial Repurposing", "Value Redirection"}

    batch_size = 5
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
                results[c["slug"]] = None  # will keep current
            else:
                answer = resp.content[0].text.strip()
                if answer not in valid:
                    logger.warning(f"Unexpected: {c['slug']} → {answer}")
                    results[c["slug"]] = None
                else:
                    results[c["slug"]] = answer

        done = min(i + batch_size, total)
        logger.info(f"Classified {done}/{total}")
        # Rate limit: wait between batches
        if done < total:
            await asyncio.sleep(1.5)

    return results


async def main() -> None:
    if not VAULT.exists():
        logger.error(f"Vault not found: {VAULT}")
        sys.exit(1)

    campaigns_dir = VAULT / "campaigns"

    # Collect all Functional Repurposing campaigns for re-classification
    fr_campaigns = []
    fr_files: dict[str, tuple[Path, frontmatter.Post]] = {}

    for md_file in sorted(campaigns_dir.glob("*.md")):
        try:
            post = frontmatter.load(str(md_file))
            methods = post.metadata.get("methods", [])
            if "Functional Repurposing" in methods:
                slug = post.metadata.get("slug", md_file.stem)
                fr_campaigns.append({
                    "slug": slug,
                    "title": post.metadata.get("title", md_file.stem),
                    "tagline": post.metadata.get("tagline", ""),
                })
                fr_files[slug] = (md_file, post)
        except Exception as e:
            logger.warning(f"Failed to read {md_file.name}: {e}")

    logger.info(f"Re-classifying {len(fr_campaigns)} Functional Repurposing campaigns")

    client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    classification = await classify_batch(client, fr_campaigns)

    # Apply changes
    changed = 0
    for slug, new_method in classification.items():
        if new_method is None or new_method == "Functional Repurposing":
            continue
        md_file, post = fr_files[slug]
        methods = post.metadata.get("methods", [])
        methods = [new_method if m == "Functional Repurposing" else m for m in methods]
        post.metadata["methods"] = methods
        md_file.write_text(frontmatter.dumps(post), encoding="utf-8")
        changed += 1
        logger.info(f"  {slug} → {new_method}")

    # Final distribution
    logger.info(f"\nChanged {changed} campaigns. New distribution:")
    counter = Counter()
    for md_file in campaigns_dir.glob("*.md"):
        post = frontmatter.load(str(md_file))
        for m in post.metadata.get("methods", []):
            counter[m] += 1
    for method, count in counter.most_common():
        logger.info(f"  {count:3d}  {method}")


if __name__ == "__main__":
    asyncio.run(main())
