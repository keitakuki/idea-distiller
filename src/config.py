from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _ROOT / "config.yaml"


def _load_yaml_config() -> dict:
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


_yaml = _load_yaml_config()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM
    llm_provider: Literal["anthropic", "openai"] = _yaml.get("llm", {}).get(
        "provider", "openai"
    )
    anthropic_api_key: str = ""
    anthropic_model: str = (
        _yaml.get("llm", {}).get("anthropic", {}).get("model", "claude-sonnet-4-5-20250514")
    )
    openai_api_key: str = ""
    openai_model: str = _yaml.get("llm", {}).get("openai", {}).get("model", "gpt-4o")

    # Love the Work credentials
    ltw_email: str = ""
    ltw_password: str = ""

    # Scraper
    scraper_delay: float = _yaml.get("scraper", {}).get("delay_between_pages", 2.5)
    scraper_max_retries: int = _yaml.get("scraper", {}).get("max_retries", 3)
    scraper_headless: bool = _yaml.get("scraper", {}).get("headless", True)
    scraper_timeout: int = _yaml.get("scraper", {}).get("timeout", 30000)

    # Export
    obsidian_vault_path: str = ""
    export_download_images: bool = _yaml.get("export", {}).get("download_images", True)
    export_include_raw_html: bool = _yaml.get("export", {}).get("include_raw_html", False)

    # Web
    web_host: str = _yaml.get("web", {}).get("host", "127.0.0.1")
    web_port: int = _yaml.get("web", {}).get("port", 8000)

    # Paths
    data_dir: Path = Field(default=_ROOT / "data")
    prompts_dir: Path = Field(default=_ROOT / "prompts")
    playwright_state_dir: Path = Field(default=_ROOT / "playwright-state")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db.sqlite3"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def vault_path(self) -> Path:
        return Path(self.obsidian_vault_path).expanduser()


settings = Settings()
