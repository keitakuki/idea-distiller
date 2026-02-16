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
    summary: str = ""
    summary_ja: str = ""
    key_insight: str = ""
    key_insight_ja: str = ""
    techniques: list[str] = []
    themes: list[str] = []
    tags: list[str] = []
    target_audience: str = ""
    media_channels: list[str] = []
    effectiveness_notes: str = ""
