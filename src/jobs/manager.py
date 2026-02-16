from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from src.config import settings
from src.export.index import generate_all_indices
from src.export.markdown import generate_campaign_note
from src.llm.processor import process_campaigns, create_provider
from src.scraper.engine import scrape_campaigns
from src.storage.database import Database
from src.storage.files import load_json, list_json_files

logger = logging.getLogger(__name__)


class JobManager:
    """Manages the lifecycle of scrape → process → export jobs."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._running_tasks: dict[str, asyncio.Task] = {}

    def is_running(self, job_id: str) -> bool:
        task = self._running_tasks.get(job_id)
        return task is not None and not task.done()

    async def start_scrape(self, job_id: str) -> None:
        """Start scraping for a job in the background."""
        if self.is_running(job_id):
            return
        task = asyncio.create_task(self._run_scrape(job_id))
        self._running_tasks[job_id] = task

    async def start_process(self, job_id: str) -> None:
        """Start LLM processing for a job in the background."""
        if self.is_running(job_id):
            return
        task = asyncio.create_task(self._run_process(job_id))
        self._running_tasks[job_id] = task

    async def start_export(self, job_id: str) -> None:
        """Start Markdown export for a job in the background."""
        if self.is_running(job_id):
            return
        task = asyncio.create_task(self._run_export(job_id))
        self._running_tasks[job_id] = task

    async def start_full_pipeline(self, job_id: str) -> None:
        """Run scrape → process → export sequentially."""
        if self.is_running(job_id):
            return
        task = asyncio.create_task(self._run_full_pipeline(job_id))
        self._running_tasks[job_id] = task

    async def _run_scrape(self, job_id: str) -> None:
        job = await self.db.get_job(job_id)
        if not job:
            return
        await self.db.update_job(job_id, status="scraping")
        try:
            # Find already scraped URLs for resume
            existing = await self.db.list_campaigns(job_id=job_id, scrape_status="scraped")
            skip_urls = {c["source_url"] for c in existing}

            async for campaign, progress in scrape_campaigns(job["source_url"], job_id, skip_urls=skip_urls):
                if campaign:
                    await self.db.create_campaign(
                        job_id=job_id,
                        source_url=campaign.url,
                        slug=campaign.slug,
                        title=campaign.title,
                        brand=campaign.brand,
                        agency=campaign.agency,
                        country=campaign.country,
                        category=campaign.category,
                        award_level=campaign.award_level,
                        festival=campaign.festival,
                        year=campaign.year,
                        scrape_status="scraped",
                        raw_data_path=str(settings.raw_dir / job_id / f"{campaign.slug}.json"),
                    )

            await self.db.update_job(job_id, status="scraped")
            logger.info(f"Job {job_id} scraping completed")
        except Exception as e:
            await self.db.update_job(job_id, status="failed", error=str(e))
            logger.error(f"Job {job_id} scraping failed: {e}")

    async def _run_process(self, job_id: str) -> None:
        await self.db.update_job(job_id, status="processing")
        try:
            raw_dir = settings.raw_dir / job_id
            async for processed, progress in process_campaigns(raw_dir, db=self.db):
                if processed:
                    # Update campaign LLM status
                    campaigns = await self.db.list_campaigns(job_id=job_id)
                    for c in campaigns:
                        if c["slug"] == processed.campaign_id:
                            await self.db.update_campaign(
                                c["id"],
                                llm_status="processed",
                                processed_path=str(settings.processed_dir / job_id / f"{processed.campaign_id}.json"),
                            )
                            break

            await self.db.update_job(job_id, status="processed")
            logger.info(f"Job {job_id} LLM processing completed")
        except Exception as e:
            await self.db.update_job(job_id, status="failed", error=str(e))
            logger.error(f"Job {job_id} processing failed: {e}")

    async def _run_export(self, job_id: str) -> None:
        await self.db.update_job(job_id, status="exporting")
        try:
            processed_dir = settings.processed_dir / job_id
            vault_path = settings.vault_path

            if not vault_path or str(vault_path) == ".":
                raise ValueError("OBSIDIAN_VAULT_PATH not configured")

            # Export individual campaign notes
            for json_file in list_json_files(processed_dir):
                data = load_json(json_file)
                md_path = generate_campaign_note(data, vault_path)

                # Update campaign export status
                campaigns = await self.db.list_campaigns(job_id=job_id)
                for c in campaigns:
                    if c["slug"] == json_file.stem:
                        await self.db.update_campaign(c["id"], export_status="exported", markdown_path=str(md_path))
                        break

            # Generate index notes
            generate_all_indices(processed_dir, vault_path)

            await self.db.update_job(job_id, status="completed")
            logger.info(f"Job {job_id} export completed to {vault_path}")
        except Exception as e:
            await self.db.update_job(job_id, status="failed", error=str(e))
            logger.error(f"Job {job_id} export failed: {e}")

    async def _run_full_pipeline(self, job_id: str) -> None:
        await self._run_scrape(job_id)
        job = await self.db.get_job(job_id)
        if job and job["status"] == "scraped":
            await self._run_process(job_id)
        job = await self.db.get_job(job_id)
        if job and job["status"] == "processed":
            await self._run_export(job_id)
