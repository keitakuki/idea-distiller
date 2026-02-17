from __future__ import annotations

import asyncio
import logging

from src.config import settings
from src.obsidian.index import generate_all_indices
from src.llm.processor import process_campaigns, process_from_vault
from src.scraper.cannes import scrape_campaigns
from src.storage.database import Database

logger = logging.getLogger(__name__)


class JobManager:
    """Manages the lifecycle of scrape → process → export jobs.

    New flow: scrape → inbox/ → LLM → campaigns/ → index
    Legacy flow: scrape → JSON → LLM → JSON → export → campaigns/
    """

    def __init__(self, db: Database) -> None:
        self.db = db
        self._running_tasks: dict[str, asyncio.Task] = {}

    def is_running(self, job_id: str) -> bool:
        task = self._running_tasks.get(job_id)
        return task is not None and not task.done()

    async def start_scrape(self, job_id: str) -> None:
        if self.is_running(job_id):
            return
        self._running_tasks[job_id] = asyncio.create_task(self._run_scrape(job_id))

    async def start_process(self, job_id: str) -> None:
        if self.is_running(job_id):
            return
        self._running_tasks[job_id] = asyncio.create_task(self._run_process(job_id))

    async def start_export(self, job_id: str) -> None:
        if self.is_running(job_id):
            return
        self._running_tasks[job_id] = asyncio.create_task(self._run_export(job_id))

    async def start_full_pipeline(self, job_id: str) -> None:
        if self.is_running(job_id):
            return
        self._running_tasks[job_id] = asyncio.create_task(self._run_full_pipeline(job_id))

    async def _run_scrape(self, job_id: str) -> None:
        job = await self.db.get_job(job_id)
        if not job:
            return
        await self.db.update_job(job_id, status="scraping")
        try:
            vault_path = settings.vault_path if settings.obsidian_vault_path else None

            # Find already scraped slugs for resume
            existing = await self.db.list_campaigns(job_id=job_id, scrape_status="scraped")
            skip_slugs = {c["slug"] for c in existing}

            async for campaign, progress in scrape_campaigns(
                source_url=job["source_url"],
                job_id=job_id,
                festival=job.get("festival") or "Cannes Lions",
                year=job.get("year"),
                skip_slugs=skip_slugs,
                vault_path=vault_path,
            ):
                if campaign:
                    await self.db.create_campaign(
                        job_id=job_id,
                        source_url=campaign.url,
                        slug=campaign.slug,
                        title=campaign.title,
                        brand=campaign.brand,
                        agency=campaign.agency,
                        country=campaign.country,
                        category=campaign.categories_str,
                        award_level=campaign.primary_award,
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
            vault_path = settings.vault_path if settings.obsidian_vault_path else None

            if vault_path:
                # New flow: process from Obsidian vault
                async for processed, progress in process_from_vault(vault_path, db=self.db):
                    if processed:
                        campaigns = await self.db.list_campaigns(job_id=job_id)
                        for c in campaigns:
                            if c["slug"] == processed.campaign_id:
                                await self.db.update_campaign(
                                    c["id"],
                                    llm_status="processed",
                                )
                                break
            else:
                # Legacy flow: process from raw JSON
                raw_dir = settings.raw_dir / job_id
                async for processed, progress in process_campaigns(raw_dir, db=self.db):
                    if processed:
                        campaigns = await self.db.list_campaigns(job_id=job_id)
                        for c in campaigns:
                            if c["slug"] == processed.campaign_id:
                                await self.db.update_campaign(
                                    c["id"],
                                    llm_status="processed",
                                    processed_path=str(
                                        settings.processed_dir / job_id / f"{processed.campaign_id}.json"
                                    ),
                                )
                                break

            await self.db.update_job(job_id, status="processed")
            logger.info(f"Job {job_id} LLM processing completed")
        except Exception as e:
            await self.db.update_job(job_id, status="failed", error=str(e))
            logger.error(f"Job {job_id} processing failed: {e}")

    async def _run_export(self, job_id: str) -> None:
        """Generate index notes from campaigns/ frontmatter."""
        await self.db.update_job(job_id, status="exporting")
        try:
            vault_path = settings.vault_path

            if not vault_path or str(vault_path) == ".":
                raise ValueError("OBSIDIAN_VAULT_PATH not configured")

            # Generate indices from campaigns/ Markdown
            generate_all_indices(vault_path)

            # Update DB campaign statuses
            campaigns = await self.db.list_campaigns(job_id=job_id)
            for c in campaigns:
                slug = c["slug"]
                campaign_path = vault_path / "campaigns" / f"{slug}.md"
                if campaign_path.exists():
                    await self.db.update_campaign(
                        c["id"],
                        export_status="exported",
                        markdown_path=str(campaign_path),
                    )

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
