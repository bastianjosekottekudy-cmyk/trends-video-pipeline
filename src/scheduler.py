"""APScheduler: fire pipeline at 09:00 and 21:00 per country timezone."""

from __future__ import annotations

import logging
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config import load_countries
from src.naming import PERIOD_EVENING, PERIOD_MORNING

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None

# (period label, hour local)
_SLOTS: tuple[tuple[str, int], ...] = (
    (PERIOD_MORNING, 9),
    (PERIOD_EVENING, 21),
)


def start_scheduler(run_callback: Callable[[str, str], None]) -> BackgroundScheduler:
    """run_callback(country_code, period) where period is Morning|Evening."""
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler

    scheduler = BackgroundScheduler()
    for country in load_countries():
        for period, hour in _SLOTS:
            job_id = f"daily-{country.code}-{period.lower()}"
            scheduler.add_job(
                run_callback,
                trigger=CronTrigger(hour=hour, minute=0, timezone=country.timezone),
                args=[country.code, period],
                id=job_id,
                replace_existing=True,
                misfire_grace_time=3600,
            )
            logger.info(
                "Scheduled %s job for %s at %02d:00 %s",
                period,
                country.code,
                hour,
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
        # job_id: daily-CY-morning
        parts = job.id.split("-", 2)
        country_code = parts[1] if len(parts) >= 2 else job.id
        period = parts[2].title() if len(parts) >= 3 else ""
        result.append(
            {
                "job_id": job.id,
                "country_code": country_code,
                "period": period,
                "next_run": next_run.isoformat() if next_run else "",
            }
        )
    result.sort(key=lambda item: (item.get("next_run") or "", item.get("job_id") or ""))
    return result


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None
