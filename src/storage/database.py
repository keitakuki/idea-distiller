from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    source_url  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    festival    TEXT,
    year        INTEGER,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    error       TEXT,
    config      TEXT
);

CREATE TABLE IF NOT EXISTS campaigns (
    id              TEXT PRIMARY KEY,
    job_id          TEXT NOT NULL REFERENCES jobs(id),
    source_url      TEXT NOT NULL,
    slug            TEXT NOT NULL,
    title           TEXT,
    brand           TEXT,
    agency          TEXT,
    country         TEXT,
    category        TEXT,
    award_level     TEXT,
    festival        TEXT,
    year            INTEGER,
    scrape_status   TEXT NOT NULL DEFAULT 'pending',
    llm_status      TEXT NOT NULL DEFAULT 'pending',
    export_status   TEXT NOT NULL DEFAULT 'pending',
    raw_data_path   TEXT,
    processed_path  TEXT,
    markdown_path   TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prompt_templates (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT,
    template    TEXT NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1,
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id              TEXT PRIMARY KEY,
    campaign_id     TEXT REFERENCES campaigns(id),
    template_name   TEXT,
    provider        TEXT,
    model           TEXT,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    cost_usd        REAL,
    duration_ms     INTEGER,
    created_at      TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return uuid.uuid4().hex[:12]


class Database:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "Database not connected"
        return self._conn

    # --- Jobs ---

    async def create_job(self, source_url: str, festival: str | None = None, year: int | None = None) -> dict:
        job_id = _uuid()
        now = _now()
        await self.conn.execute(
            "INSERT INTO jobs (id, source_url, status, festival, year, created_at, updated_at) VALUES (?, ?, 'pending', ?, ?, ?, ?)",
            (job_id, source_url, festival, year, now, now),
        )
        await self.conn.commit()
        return {"id": job_id, "source_url": source_url, "status": "pending", "festival": festival, "year": year}

    async def get_job(self, job_id: str) -> dict | None:
        async with self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def list_jobs(self) -> list[dict]:
        async with self.conn.execute("SELECT * FROM jobs ORDER BY created_at DESC") as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def update_job(self, job_id: str, **fields) -> None:
        fields["updated_at"] = _now()
        sets = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [job_id]
        await self.conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", vals)
        await self.conn.commit()

    # --- Campaigns ---

    async def create_campaign(self, job_id: str, source_url: str, slug: str, **extra) -> dict:
        cid = _uuid()
        now = _now()
        await self.conn.execute(
            "INSERT INTO campaigns (id, job_id, source_url, slug, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (cid, job_id, source_url, slug, now, now),
        )
        if extra:
            await self.update_campaign(cid, **extra)
        else:
            await self.conn.commit()
        return {"id": cid, "job_id": job_id, "source_url": source_url, "slug": slug}

    async def get_campaign(self, campaign_id: str) -> dict | None:
        async with self.conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def list_campaigns(self, job_id: str | None = None, **filters) -> list[dict]:
        where_parts = []
        params: list = []
        if job_id:
            where_parts.append("job_id = ?")
            params.append(job_id)
        for k, v in filters.items():
            where_parts.append(f"{k} = ?")
            params.append(v)
        where = " AND ".join(where_parts) if where_parts else "1=1"
        async with self.conn.execute(
            f"SELECT * FROM campaigns WHERE {where} ORDER BY created_at", params
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def update_campaign(self, campaign_id: str, **fields) -> None:
        fields["updated_at"] = _now()
        sets = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [campaign_id]
        await self.conn.execute(f"UPDATE campaigns SET {sets} WHERE id = ?", vals)
        await self.conn.commit()

    # --- Prompt Templates ---

    async def upsert_prompt(self, name: str, template: str, description: str = "") -> None:
        now = _now()
        pid = _uuid()
        await self.conn.execute(
            """INSERT INTO prompt_templates (id, name, description, template, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET template=excluded.template, description=excluded.description,
               version=version+1, updated_at=excluded.updated_at""",
            (pid, name, description, template, now, now),
        )
        await self.conn.commit()

    async def get_prompt(self, name: str) -> dict | None:
        async with self.conn.execute("SELECT * FROM prompt_templates WHERE name = ? AND is_active = 1", (name,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def list_prompts(self) -> list[dict]:
        async with self.conn.execute("SELECT * FROM prompt_templates ORDER BY name") as cur:
            return [dict(r) for r in await cur.fetchall()]

    # --- LLM Calls ---

    async def log_llm_call(self, campaign_id: str, template_name: str, provider: str, model: str,
                           input_tokens: int, output_tokens: int, cost_usd: float, duration_ms: int) -> None:
        await self.conn.execute(
            """INSERT INTO llm_calls (id, campaign_id, template_name, provider, model, input_tokens, output_tokens, cost_usd, duration_ms, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (_uuid(), campaign_id, template_name, provider, model, input_tokens, output_tokens, cost_usd, duration_ms, _now()),
        )
        await self.conn.commit()

    async def get_llm_stats(self) -> dict:
        async with self.conn.execute(
            "SELECT COUNT(*) as calls, COALESCE(SUM(input_tokens),0) as input_tokens, COALESCE(SUM(output_tokens),0) as output_tokens, COALESCE(SUM(cost_usd),0) as total_cost FROM llm_calls"
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else {}
