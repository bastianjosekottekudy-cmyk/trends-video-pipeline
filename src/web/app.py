"""FastAPI local video library dashboard."""

from __future__ import annotations

import json
import logging
import shutil
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.config import (
    OUTPUT_DIR,
    get_country,
    load_countries,
    local_run_date,
    local_time_label,
    load_pipeline_config,
)
from src.db import store
from src.naming import PERIOD_EVENING, normalize_period, resolve_title_slot, title_from_video_path
from src.pipeline import run_country_pipeline
from src.scheduler import get_next_run_times

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

app = FastAPI(title="Trends Video Library")
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

_running_lock = threading.Lock()
_running_countries: set[str] = set()
_upload_lock = threading.Lock()
_uploading_runs: set[int] = set()


def _youtube_enabled() -> bool:
    return bool(load_pipeline_config().get("youtube", {}).get("enabled", False))


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


def _trend_count(run: dict[str, Any]) -> int:
    trends = _parse_json_field(run.get("trends_json"))
    if isinstance(trends, list):
        return len(trends)
    return 0


def _video_exists(run: dict[str, Any]) -> bool:
    path = run.get("video_path")
    return bool(path and Path(path).is_file())


def _safe_video_path(run: dict[str, Any]) -> Path:
    raw = run.get("video_path")
    if not raw:
        raise HTTPException(status_code=404, detail="No video for this run")
    path = Path(raw).resolve()
    output_root = OUTPUT_DIR.resolve()
    try:
        path.relative_to(output_root)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Invalid video path") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Video file not found on disk")
    return path


def _run_output_dir(run: dict[str, Any]) -> Path | None:
    """
    Resolve this run's isolated folder under OUTPUT_DIR.
    Prefer output/.../run_{id}/; never return a shared country folder.
    """
    output_root = OUTPUT_DIR.resolve()
    run_id = run.get("id")
    run_date = run.get("run_date")
    country = run.get("country_code")

    if run_id and run_date and country:
        candidate = (
            OUTPUT_DIR / str(run_date) / str(country).upper() / f"run_{run_id}"
        ).resolve()
        try:
            candidate.relative_to(output_root)
        except ValueError:
            return None
        if candidate.is_dir():
            return candidate

    video_path = run.get("video_path")
    if video_path:
        path = Path(video_path).resolve()
        try:
            path.relative_to(output_root)
        except ValueError:
            return None
        parent = path.parent
        # Only treat as deletable dir if it's a per-run folder
        if parent.name.startswith("run_"):
            return parent
    return None


def _other_runs_use_path(run_id: int, directory: Path) -> bool:
    """True if another DB run's video/script lives inside directory."""
    directory = directory.resolve()
    for other in store.list_runs(limit=500):
        if other.get("id") == run_id:
            continue
        for key in ("video_path", "script_path"):
            raw = other.get(key)
            if not raw:
                continue
            try:
                Path(raw).resolve().relative_to(directory)
                return True
            except ValueError:
                continue
    return False


def _delete_run_artifacts(run: dict[str, Any]) -> list[str]:
    """Permanently delete only this run's files. Returns deleted paths."""
    deleted: list[str] = []
    run_id = int(run["id"])
    out_dir = _run_output_dir(run)

    if out_dir and out_dir.is_dir():
        if _other_runs_use_path(run_id, out_dir):
            logger.warning(
                "Skip folder delete for run %s — other runs share %s",
                run_id,
                out_dir,
            )
        else:
            shutil.rmtree(out_dir)
            deleted.append(str(out_dir))
            # Clean empty country / date parents
            for parent in (out_dir.parent, out_dir.parent.parent):
                try:
                    if parent.is_dir() and parent.resolve() != OUTPUT_DIR.resolve():
                        if not any(parent.iterdir()):
                            parent.rmdir()
                            deleted.append(str(parent))
                except OSError:
                    pass
            return deleted

    # Safe fallback: delete only files owned by this run record
    for key in ("video_path", "script_path"):
        raw = run.get(key)
        if not raw:
            continue
        path = Path(raw).resolve()
        try:
            path.relative_to(OUTPUT_DIR.resolve())
        except ValueError:
            continue
        if path.is_file():
            path.unlink()
            deleted.append(str(path))
        # Remove empty slides dir next to video if present
        slides = path.parent / "slides"
        if slides.is_dir() and not _other_runs_use_path(run_id, path.parent):
            try:
                shutil.rmtree(slides)
                deleted.append(str(slides))
            except OSError:
                pass
    return deleted


def _normalize_upload_status(run: dict[str, Any]) -> str:
    status = (run.get("upload_status") or "none").strip().lower()
    if status in ("uploading", "failed", "uploaded"):
        return status
    yt_id = (run.get("youtube_video_id") or "").strip()
    if yt_id and yt_id != "skipped":
        return "uploaded"
    return "none"


def _enrich_run(run: dict[str, Any]) -> dict[str, Any]:
    run["duration"] = _duration(run.get("started_at"), run.get("finished_at"))
    run["trend_count"] = _trend_count(run)
    run["has_video"] = _video_exists(run)
    run["video_title"] = title_from_video_path(
        run.get("video_path"),
        country_name=run.get("country_name") or "",
        run_date=run.get("run_date") or "",
        period=run.get("period"),
    )
    upload_status = _normalize_upload_status(run)
    run["upload_status"] = upload_status
    yt_id = (run.get("youtube_video_id") or "").strip()
    run["is_uploaded"] = upload_status == "uploaded" and bool(yt_id) and yt_id != "skipped"
    run["youtube_url"] = (
        f"https://www.youtube.com/watch?v={yt_id}" if run["is_uploaded"] else ""
    )
    run["can_upload"] = bool(run["has_video"] and run.get("status") != "running")
    run["upload_label"] = (
        "Re-upload"
        if upload_status in ("uploaded", "failed")
        else "Upload"
    )

    if upload_status == "uploading":
        run["display_status"] = "uploading"
    elif run["is_uploaded"]:
        run["display_status"] = "uploaded"
    elif upload_status == "failed" and run["has_video"]:
        run["display_status"] = "upload-failed"
    elif run.get("status") == "success" and not run["has_video"]:
        run["display_status"] = "missing"
    elif run.get("status") == "success" and run["has_video"]:
        run["display_status"] = "ready"
    else:
        run["display_status"] = run.get("status")
    return run


def _upload_run_video(run_id: int) -> None:
    run = store.get_run(run_id)
    if not run:
        return
    try:
        path = _safe_video_path(run)
    except HTTPException as exc:
        store.set_upload_status(run_id, "failed", upload_error=str(exc.detail))
        store.append_step_log(run_id, "upload", f"Upload failed: {exc.detail}")
        return

    try:
        country = get_country(str(run["country_code"]))
    except ValueError as exc:
        store.set_upload_status(run_id, "failed", upload_error=str(exc))
        return

    trends = _parse_json_field(run.get("trends_json")) or []
    news = _parse_json_field(run.get("news_json")) or {}
    if not isinstance(trends, list):
        trends = []
    if not isinstance(news, dict):
        news = {}

    from src.pipeline import _attempt_youtube_upload

    _attempt_youtube_upload(
        run_id,
        str(path),
        country,
        trends,
        news,
        str(run.get("run_date") or local_run_date(country)),
        period=resolve_title_slot(run.get("period")),
    )


def _group_by_date(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for run in runs:
        date = run.get("run_date") or "unknown"
        grouped.setdefault(date, []).append(run)
    return [{"date": date, "runs": items} for date, items in grouped.items()]


def _scheduled_run(country_code: str, period: str = PERIOD_EVENING) -> None:
    slot = normalize_period(period) or PERIOD_EVENING
    with _running_lock:
        if country_code in _running_countries:
            logger.warning(
                "Skipping scheduled %s run for %s — already running",
                slot,
                country_code,
            )
            return
        _running_countries.add(country_code)
    try:
        run_country_pipeline(
            country_code,
            skip_upload=not _youtube_enabled(),
            period=slot,
        )
    except Exception:
        logger.exception("Scheduled %s run failed for %s", slot, country_code)
    finally:
        with _running_lock:
            _running_countries.discard(country_code)


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    country: str | None = None,
    date: str | None = None,
) -> HTMLResponse:
    runs = [_enrich_run(r) for r in store.list_runs(country_code=country, run_date=date)]
    groups = _group_by_date(runs)
    stats = store.count_runs_today()
    countries = load_countries()
    available_dates = store.list_run_dates()
    next_runs = get_next_run_times()
    has_running = any(r["status"] == "running" for r in runs) or stats.get("running", 0) > 0
    has_uploading = (
        any(r.get("upload_status") == "uploading" for r in runs)
        or stats.get("uploading", 0) > 0
    )
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "groups": groups,
            "stats": stats,
            "countries": countries,
            "available_dates": available_dates,
            "next_runs": next_runs,
            "filter_country": (country or "").upper(),
            "filter_date": date or "",
            "has_running": has_running,
            "has_uploading": has_uploading,
            "youtube_enabled": _youtube_enabled(),
        },
    )


@app.get("/runs/{run_id}", response_class=HTMLResponse)
async def run_detail(request: Request, run_id: int) -> HTMLResponse:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    run = _enrich_run(run)
    run["trends"] = _parse_json_field(run.get("trends_json"))
    run["news"] = _parse_json_field(run.get("news_json"))
    run["steps"] = _parse_json_field(run.get("steps_log")) or []
    script_content = ""
    script_path = run.get("script_path")
    if script_path and Path(script_path).exists():
        script_content = Path(script_path).read_text(encoding="utf-8")
    run["script_content"] = script_content
    return templates.TemplateResponse(
        request,
        "run_detail.html",
        {"run": run},
    )


@app.get("/videos/{run_id}/file")
async def video_file(run_id: int) -> FileResponse:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    path = _safe_video_path(run)
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@app.get("/videos/{run_id}/download")
async def video_download(run_id: int) -> FileResponse:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    path = _safe_video_path(run)
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=path.name,
        content_disposition_type="attachment",
    )


@app.delete("/api/runs/{run_id}")
async def api_delete_run(run_id: int) -> JSONResponse:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.get("status") == "running":
        raise HTTPException(status_code=409, detail="Cannot delete a running job")
    if (run.get("upload_status") or "") == "uploading":
        raise HTTPException(status_code=409, detail="Cannot delete while uploading")

    deleted_paths = _delete_run_artifacts(run)
    store.delete_run(run_id)
    logger.info("Deleted run %s and artifacts: %s", run_id, deleted_paths)
    return JSONResponse(
        {"ok": True, "run_id": run_id, "deleted_paths": deleted_paths}
    )


@app.post("/api/runs/{run_id}/upload")
async def api_upload_run(run_id: int, background_tasks: BackgroundTasks) -> JSONResponse:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.get("status") == "running":
        raise HTTPException(status_code=409, detail="Wait for generation to finish")
    if not _video_exists(run):
        raise HTTPException(status_code=400, detail="No local video to upload")
    if (run.get("upload_status") or "") == "uploading":
        raise HTTPException(status_code=409, detail="Upload already in progress")

    with _upload_lock:
        if run_id in _uploading_runs:
            raise HTTPException(status_code=409, detail="Upload already in progress")
        _uploading_runs.add(run_id)

    store.set_upload_status(run_id, "uploading", upload_error=None)

    def _bg() -> None:
        try:
            _upload_run_video(run_id)
        finally:
            with _upload_lock:
                _uploading_runs.discard(run_id)

    background_tasks.add_task(_bg)
    return JSONResponse({"run_id": run_id, "status": "uploading"})


@app.get("/api/countries")
async def api_countries() -> JSONResponse:
    countries = [
        {"code": c.code, "name": c.name, "timezone": c.timezone}
        for c in load_countries()
    ]
    return JSONResponse({"countries": countries, "next_runs": get_next_run_times()})


@app.get("/api/runs")
async def api_runs(
    country: str | None = None,
    date: str | None = None,
) -> JSONResponse:
    runs = [_enrich_run(r) for r in store.list_runs(country_code=country, run_date=date)]
    return JSONResponse({"runs": runs, "groups": _group_by_date(runs)})


@app.post("/api/trigger/{country_code}")
async def api_trigger(
    country_code: str,
    background_tasks: BackgroundTasks,
    mock: bool = False,
    async_run: bool = True,
) -> JSONResponse:
    code = country_code.upper()
    try:
        country = next(c for c in load_countries() if c.code == code)
    except StopIteration:
        raise HTTPException(status_code=404, detail=f"Unknown country: {code}") from None

    # Manual generate: put the local clock time in the title (not Morning/Evening)
    slot = local_time_label(country)

    with _running_lock:
        if code in _running_countries:
            raise HTTPException(status_code=409, detail=f"{code} is already running")

    if async_run:
        run_id = store.create_run(
            code,
            country.name,
            local_run_date(country),
            period=slot,
        )

        def _bg(rid: int) -> None:
            with _running_lock:
                _running_countries.add(code)
            try:
                run_country_pipeline(
                    code,
                    trends_provider="mock" if mock else "http",
                    news_provider="mock" if mock else "google_news_rss",
                    skip_upload=not _youtube_enabled(),
                    existing_run_id=rid,
                    period=slot,
                )
            except Exception:
                logger.exception("Background run failed for %s", code)
            finally:
                with _running_lock:
                    _running_countries.discard(code)

        background_tasks.add_task(_bg, run_id)
        return JSONResponse(
            {"run_id": run_id, "status": "started", "period": slot}
        )

    with _running_lock:
        _running_countries.add(code)
    try:
        run_id = run_country_pipeline(
            code,
            trends_provider="mock" if mock else "http",
            news_provider="mock" if mock else "google_news_rss",
            skip_upload=not _youtube_enabled(),
            period=slot,
        )
    finally:
        with _running_lock:
            _running_countries.discard(code)
    return JSONResponse(
        {"run_id": run_id, "status": "completed", "period": slot}
    )


def create_app() -> FastAPI:
    store.init_db()
    store.fail_orphaned_runs()
    return app
