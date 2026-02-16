from __future__ import annotations

from pydantic import BaseModel


class ScrapedCampaign(BaseModel):
    url: str
    slug: str = ""
    title: str = ""
    brand: str = ""
    agency: str = ""
    country: str = ""
    category: str = ""
    subcategory: str = ""
    award_level: str = ""
    festival: str = ""
    year: int | None = None
    description: str = ""
    credits: list[dict[str, str]] = []
    video_urls: list[str] = []
    image_urls: list[str] = []
    case_study_text: str = ""
    raw_html: str = ""
