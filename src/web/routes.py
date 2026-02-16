from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.config import settings
from src.storage.files import list_json_files, load_json

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _get_db(request: Request):
    return request.app.state.db


def _get_jobs(request: Request):
    return request.app.state.job_manager


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db=Depends(_get_db)):
    jobs = await db.list_jobs()
    stats = await db.get_llm_stats()
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "jobs": jobs,
        "stats": stats,
    })


@router.post("/jobs")
async def create_job(
    request: Request,
    source_url: str = Form(...),
    festival: str = Form(""),
    year: int = Form(None),
    mode: str = Form("full"),
    db=Depends(_get_db),
    jm=Depends(_get_jobs),
):
    job = await db.create_job(source_url=source_url, festival=festival or None, year=year)
    if mode == "full":
        await jm.start_full_pipeline(job["id"])
    elif mode == "scrape":
        await jm.start_scrape(job["id"])
    return RedirectResponse(url=f"/jobs/{job['id']}", status_code=303)


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(request: Request, job_id: str, db=Depends(_get_db)):
    job = await db.get_job(job_id)
    if not job:
        return HTMLResponse("Job not found", status_code=404)
    campaigns = await db.list_campaigns(job_id=job_id)
    return templates.TemplateResponse("job_detail.html", {
        "request": request,
        "job": job,
        "campaigns": campaigns,
    })


@router.post("/jobs/{job_id}/process")
async def trigger_process(request: Request, job_id: str, jm=Depends(_get_jobs)):
    await jm.start_process(job_id)
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


@router.post("/jobs/{job_id}/export")
async def trigger_export(request: Request, job_id: str, jm=Depends(_get_jobs)):
    await jm.start_export(job_id)
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


@router.get("/campaigns", response_class=HTMLResponse)
async def campaigns_list(request: Request, db=Depends(_get_db)):
    campaigns = await db.list_campaigns()
    return templates.TemplateResponse("campaigns.html", {
        "request": request,
        "campaigns": campaigns,
    })


@router.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
async def campaign_detail(request: Request, campaign_id: str, db=Depends(_get_db)):
    campaign = await db.get_campaign(campaign_id)
    if not campaign:
        return HTMLResponse("Campaign not found", status_code=404)

    # Load processed data if available
    extra_data = {}
    if campaign.get("processed_path"):
        p = Path(campaign["processed_path"])
        if p.exists():
            extra_data = load_json(p)

    return templates.TemplateResponse("campaign_detail.html", {
        "request": request,
        "campaign": campaign,
        "data": extra_data,
    })


@router.get("/prompts", response_class=HTMLResponse)
async def prompts_list(request: Request, db=Depends(_get_db)):
    prompts = await db.list_prompts()
    return templates.TemplateResponse("prompt_editor.html", {
        "request": request,
        "prompts": prompts,
    })


@router.post("/prompts/{name}")
async def update_prompt(
    request: Request,
    name: str,
    template: str = Form(...),
    description: str = Form(""),
    db=Depends(_get_db),
):
    await db.upsert_prompt(name=name, template=template, description=description)
    return RedirectResponse(url="/prompts", status_code=303)


@router.get("/api/jobs/{job_id}/status")
async def job_status_api(job_id: str, db=Depends(_get_db), jm=Depends(_get_jobs)):
    """JSON API endpoint for polling job status."""
    job = await db.get_job(job_id)
    if not job:
        return {"error": "not found"}
    campaigns = await db.list_campaigns(job_id=job_id)
    scraped = sum(1 for c in campaigns if c["scrape_status"] == "scraped")
    processed = sum(1 for c in campaigns if c["llm_status"] == "processed")
    exported = sum(1 for c in campaigns if c["export_status"] == "exported")
    return {
        "status": job["status"],
        "total": len(campaigns),
        "scraped": scraped,
        "processed": processed,
        "exported": exported,
        "is_running": jm.is_running(job_id),
        "error": job.get("error"),
    }
