from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.config import settings
from src.jobs.manager import JobManager
from src.storage.database import Database
from src.web.routes import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


async def _seed_prompts(db: Database) -> None:
    """Load default prompt templates from YAML files into DB if not present."""
    for yaml_file in sorted(_PROMPTS_DIR.glob("*.yaml")):
        with open(yaml_file) as f:
            data = yaml.safe_load(f)
        name = data.get("name", yaml_file.stem)
        existing = await db.get_prompt(name)
        if not existing:
            template_text = yaml.dump(data, allow_unicode=True, default_flow_style=False)
            await db.upsert_prompt(
                name=name,
                template=template_text,
                description=data.get("description", ""),
            )
            logger.info(f"Seeded prompt template: {name}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    db = Database(settings.db_path)
    await db.connect()
    await _seed_prompts(db)

    app.state.db = db
    app.state.job_manager = JobManager(db)

    logger.info(f"Idea Distillery running at http://{settings.web_host}:{settings.web_port}")
    yield

    # Shutdown
    await db.close()


app = FastAPI(title="Idea Distillery", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
app.include_router(router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.main:app",
        host=settings.web_host,
        port=settings.web_port,
        reload=True,
    )
