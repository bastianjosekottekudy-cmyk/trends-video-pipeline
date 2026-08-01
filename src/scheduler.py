"""APScheduler: fire pipeline at 21:00 per country timezone."""

from __future__ import annotations

import logging
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config import load_countries

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def start_scheduler(run_callback: Callable[[str], None]) -> BackgroundScheduler:
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler

    scheduler = BackgroundScheduler()
    for country in load_countries():
        job_id = f"daily-{country.code}"
        scheduler.add_job(
            run_callback,
            trigger=CronTrigger(hour=21, minute=0, timezone=country.timezone),
            args=[country.code],
            id=job_id,
            replace_existing=True,
            misfire_grace_time=3600,
        )
        logger.info(
            "Scheduled daily job for %s at 21:00 %s",
            country.code,
            country.timezone,
        )

    scheduler.start()
    _scheduler = scheduler
    return scheduler


def get_next_run_times() -> list[dict[str, str]]:
    if not _scheduler:
        return []
    result = []
    for job in _scheduler.get_jobs():
        next_run = job.next_run_time
        result.append(
            {
                "job_id": job.id,
                "country_code": job.id.replace("daily-", ""),
                "next_run": next_run.isoformat() if next_run else "",
            }
        )
    return result


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None
