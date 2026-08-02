"""YouTube-ready video title and filename helpers."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

PERIOD_MORNING = "Morning"
PERIOD_EVENING = "Evening"
VALID_PERIODS = {PERIOD_MORNING, PERIOD_EVENING}


def normalize_period(period: str | None) -> str | None:
    """Return Morning/Evening only; other strings return None."""
    if not period:
        return None
    cleaned = str(period).strip().title()
    if cleaned in VALID_PERIODS:
        return cleaned
    lower = cleaned.lower()
    if lower in ("am", "morning", "9am"):
        return PERIOD_MORNING
    if lower in ("pm", "evening", "9pm"):
        return PERIOD_EVENING
    return None


def resolve_title_slot(slot: str | None) -> str | None:
    """
    Slot for titles: Morning/Evening (scheduled) or a clock label (manual), e.g. '9:47 PM'.
    """
    if not slot:
        return None
    named = normalize_period(slot)
    if named:
        return named
    cleaned = str(slot).strip()
    return cleaned or None


def format_display_date(run_date: str) -> str:
    """Convert YYYY-MM-DD to 'August 2, 2026'."""
    try:
        dt = datetime.strptime(run_date, "%Y-%m-%d")
        return f"{dt.strftime('%B')} {dt.day}, {dt.year}"
    except ValueError:
        return run_date


def build_video_title(
    country_name: str,
    run_date: str,
    period: str | None = None,
) -> str:
    """e.g. 'Cyprus Morning Trends …' or 'Cyprus 9:47 PM Trends …'."""
    slot = resolve_title_slot(period)
    date_part = format_display_date(run_date)
    if slot:
        return f"{country_name} {slot} Trends {date_part}"
    return f"{country_name} Trends {date_part}"


def safe_filename(title: str) -> str:
    """Strip Windows-invalid filename characters; keep spaces and commas."""
    cleaned = re.sub(r'[<>:"/\\|?*]', "", title)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(".")
    return cleaned or "video"


def video_filename(
    country_name: str,
    run_date: str,
    period: str | None = None,
) -> str:
    """Safe MP4 filename matching the YouTube title."""
    return f"{safe_filename(build_video_title(country_name, run_date, period))}.mp4"


def title_from_video_path(
    video_path: str | None,
    country_name: str = "",
    run_date: str = "",
    period: str | None = None,
) -> str:
    """Derive display title from stored path, or rebuild from country/date/period."""
    if video_path:
        stem = Path(video_path).stem
        if stem and stem != "final":
            return stem
    if country_name and run_date:
        return build_video_title(country_name, run_date, period)
    return country_name or "Video"
