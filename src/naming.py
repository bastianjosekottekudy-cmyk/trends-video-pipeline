"""YouTube-ready video title and filename helpers."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path


def format_display_date(run_date: str) -> str:
    """Convert YYYY-MM-DD to 'August 2, 2026'."""
    try:
        dt = datetime.strptime(run_date, "%Y-%m-%d")
        return f"{dt.strftime('%B')} {dt.day}, {dt.year}"
    except ValueError:
        return run_date


def build_video_title(country_name: str, run_date: str) -> str:
    """Build a YouTube-style title: 'United States Trends August 2, 2026'."""
    return f"{country_name} Trends {format_display_date(run_date)}"


def safe_filename(title: str) -> str:
    """Strip Windows-invalid filename characters; keep spaces and commas."""
    cleaned = re.sub(r'[<>:"/\\|?*]', "", title)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(".")
    return cleaned or "video"


def video_filename(country_name: str, run_date: str) -> str:
    """Safe MP4 filename matching the YouTube title."""
    return f"{safe_filename(build_video_title(country_name, run_date))}.mp4"


def title_from_video_path(video_path: str | None, country_name: str = "", run_date: str = "") -> str:
    """Derive display title from stored path, or rebuild from country/date."""
    if video_path:
        stem = Path(video_path).stem
        if stem and stem != "final":
            return stem
    if country_name and run_date:
        return build_video_title(country_name, run_date)
    return country_name or "Video"
