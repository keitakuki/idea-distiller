from __future__ import annotations

from pydantic import BaseModel


class LLMResponse(BaseModel):
    content: str
    input_tokens: int
    output_tokens: int
    model: str
    provider: str


class ProcessedCampaign(BaseModel):
    campaign_id: str
    # 3-level summary structure (Japanese only)
    tagline: str = ""                  # 一行キャッチ (15字以内)
    summary: str = ""                  # 概要 (1-2 sentences)
    overview_background: str = ""      # 全体像-背景 (1-2 sentences)
    overview_strategy: str = ""        # 全体像-戦略 (1-2 sentences)
    overview_idea: str = ""            # 全体像-アイデア (1-2 sentences)
    overview_outcome: str = ""         # 全体像-結果 (1-2 sentences)
    background: str = ""              # 背景・課題 詳細 (200-400 chars)
    strategy: str = ""                # 戦略 詳細 (200-400 chars)
    idea: str = ""                    # アイデア 詳細 (200-400 chars)
    outcome: str = ""                 # 結果・成果 詳細 (200-400 chars)
    # Classification
    methods: list[str] = []
    method_definitions: dict[str, str] = {}  # メソッド名→定義（新規メソッドの定義保存用）
    tags: list[str] = []
    # 戦略構造 (Strategic DNA) — 別パスで抽出
    strategic_essence: str = ""   # 課題の本質
    insight: str = ""             # インサイト
    strategic_shift: str = ""     # 戦略転換
    mechanism: str = ""           # メカニズム
    scale_factor: str = ""        # なぜスケールしたか
