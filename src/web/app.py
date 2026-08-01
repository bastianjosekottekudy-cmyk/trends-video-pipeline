"""FastAPI tracking dashboard."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.config import PROJECT_ROOT, load_countries, load_pipeline_config
from src.db import store
from src.pipeline import run_country_pipeline
from src.scheduler import get_next_run_times

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

app = FastAPI(title="Trends Video Pipeline Dashboard")
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

_running_lock = threading.Lock()
_running_countries: set[str] = set()


def _parse_json_field(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _duration(started: str | None, finished: str | None) -> str:
    if not started:
        return "—"
    try:
        start = datetime.fromisoformat(started)
        end = datetime.fromisoformat(finished) if finished else datetime.now(timezone.utc)
        secs = int((end - start).total_seconds())
        mins, s = divmod(secs, 60)
        return f"{mins}m {s}s"
    except ValueError:
        return "—"


def _scheduled_run(country_code: str) -> None:
    with _running_lock:
        if country_code in _running_countries:
            logger.warning("Skipping scheduled run for %s — already running", country_code)
            return
        _running_countries.add(country_code)
    try:
        run_country_pipeline(country_code)
    except Exception:
        logger.exception("Scheduled run failed for %s", country_code)
    finally:
        with _running_lock:
            _running_countries.discard(country_code)


def _trigger_run(country_code: str, mock: bool = False, skip_upload: bool = False) -> int:
    with _running_lock:
        if country_code.upper() in _running_countries:
            raise HTTPException(status_code=409, detail=f"{country_code} is already running")
        _running_countries.add(country_code.upper())
    try:
        return run_country_pipeline(
            country_code,
            trends_provider="mock" if mock else "pytrends",
            news_provider="mock" if mock else "google_news_rss",
            skip_upload=skip_upload,
        )
    finally:
        with _running_lock:
            _running_countries.discard(country_code.upper())


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, country: str | None = None) -> HTMLResponse:
    runs = store.list_runs(country_code=country)
    for run in runs:
        run["duration"] = _duration(run.get("started_at"), run.get("finished_at"))
    stats = store.count_runs_today()
    countries = load_countries()
    next_runs = get_next_run_times()
    has_running = any(r["status"] == "running" for r in runs)
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "runs": runs,
            "stats": stats,
            "countries": countries,
            "next_runs": next_runs,
            "filter_country": country or "",
            "has_running": has_running,
        },
    )


@app.get("/runs/{run_id}", response_class=HTMLResponse)
async def run_detail(request: Request, run_id: int) -> HTMLResponse:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    run["trends"] = _parse_json_field(run.get("trends_json"))
    run["news"] = _parse_json_field(run.get("news_json"))
    run["steps"] = _parse_json_field(run.get("steps_log")) or []
    run["duration"] = _duration(run.get("started_at"), run.get("finished_at"))
    script_content = ""
    script_path = run.get("script_path")
    if script_path and Path(script_path).exists():
        script_content = Path(script_path).read_text(encoding="utf-8")
    run["script_content"] = script_content
    return templates.TemplateResponse(
        "run_detail.html",
        {"request": request, "run": run},
    )


@app.get("/api/countries")
async def api_countries() -> JSONResponse:
    countries = [
        {"code": c.code, "name": c.name, "timezone": c.timezone}
        for c in load_countries()
    ]
    return JSONResponse({"countries": countries, "next_runs": get_next_run_times()})


@app.get("/api/runs")
async def api_runs(country: str | None = None) -> JSONResponse:
    return JSONResponse({"runs": store.list_runs(country_code=country)})


@app.post("/api/trigger/{country_code}")
async def api_trigger(
    country_code: str,
    background_tasks: BackgroundTasks,
    mock: bool = False,
    skip_upload: bool = False,
    async_run: bool = True,
) -> JSONResponse:
    code = country_code.upper()
    try:
        next(c for c in load_countries() if c.code == code)
    except StopIteration:
        raise HTTPException(status_code=404, detail=f"Unknown country: {code}")

    if async_run:
        country = next(c for c in load_countries() if c.code == code)
        run_id = store.create_run(
            code,
            country.name,
            datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        )

        def _bg(rid: int) -> None:
            with _running_lock:
                _running_countries.add(code)
            try:
                run_country_pipeline(
                    code,
                    trends_provider="mock" if mock else "pytrends",
                    news_provider="mock" if mock else "google_news_rss",
                    skip_upload=skip_upload,
                    existing_run_id=rid,
                )
            except Exception:
                logger.exception("Background run failed for %s", code)
            finally:
                with _running_lock:
                    _running_countries.discard(code)

        background_tasks.add_task(_bg, run_id)
        return JSONResponse({"run_id": run_id, "status": "started"})
    else:
        run_id = _trigger_run(code, mock=mock, skip_upload=skip_upload)
        return JSONResponse({"run_id": run_id, "status": "completed"})


def create_app() -> FastAPI:
    store.init_db()
    return app
