from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def list_json_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(directory.glob("*.json"))


async def download_image(url: str, output_dir: Path, filename: str | None = None) -> Path | None:
    """Download an image from URL to output_dir. Returns local path or None on failure."""
    if not url or url.startswith("data:"):
        return None

    if not filename:
        # Derive filename from URL path
        parsed = urlparse(url)
        path_part = parsed.path.rstrip("/").split("/")[-1]
        # Remove query params from filename, add extension if missing
        if "." not in path_part:
            path_part += ".webp"
        filename = path_part

    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / filename

    if dest.exists():
        return dest

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            logger.debug(f"Downloaded image: {dest}")
            return dest
    except Exception as e:
        logger.warning(f"Failed to download image {url}: {e}")
        return None
