from __future__ import annotations

from pydantic import BaseModel


class Award(BaseModel):
    """A single award entry (a campaign can win multiple awards)."""
    level: str = ""          # Grand Prix, Gold, Silver, Bronze
    category: str = ""       # e.g., "Audio & Radio"
    subcategory: str = ""    # e.g., "Use of Music"
    festival: str = ""       # e.g., "Cannes Lions"
    year: int | None = None


class CampaignEntry(BaseModel):
    """Minimal campaign info extracted from the Campaign Library listing page."""
    url: str                 # /work/campaigns/{slug}-{id}
    slug: str = ""           # e.g., "a-tale-as-old-as-websites-1828157"
    title: str = ""
    brand: str = ""
    agency: str = ""
    agency_location: str = ""
    image_url: str = ""
    award_count_text: str = ""  # e.g., "4 Cannes Lions Awards"
    year: int | None = None
    awards: list[Award] = []


class ScrapedCampaign(BaseModel):
    """Full campaign data after visiting the detail page."""
    url: str
    slug: str = ""
    title: str = ""
    brand: str = ""
    agency: str = ""
    country: str = ""
    awards: list[Award] = []
    award_count_text: str = ""  # e.g., "4 Cannes Lions Awards" from listing
    campaign_year: int | None = None  # Year from listing card
    campaign_festival: str = ""  # Festival name
    description: str = ""
    credits: list[dict[str, str]] = []
    video_urls: list[str] = []
    image_urls: list[str] = []
    image_paths: list[str] = []  # Local file paths for downloaded images
    case_study_text: str = ""
    raw_html: str = ""

    @property
    def primary_award(self) -> str:
        """Highest award level for display."""
        order = {"Grand Prix": 0, "Gold": 1, "Silver": 2, "Bronze": 3}
        if not self.awards:
            return self.award_count_text or ""
        return min(self.awards, key=lambda a: order.get(a.level, 99)).level

    @property
    def categories_str(self) -> str:
        return ", ".join(dict.fromkeys(a.category for a in self.awards if a.category))

    @property
    def festival(self) -> str:
        if self.awards:
            return self.awards[0].festival
        return self.campaign_festival

    @property
    def year(self) -> int | None:
        if self.awards:
            return self.awards[0].year
        return self.campaign_year
