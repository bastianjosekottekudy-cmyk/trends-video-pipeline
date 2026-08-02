"""Project paths and configuration loading."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
OUTPUT_DIR = PROJECT_ROOT / "output"
SECRETS_DIR = PROJECT_ROOT / "secrets"

load_dotenv(PROJECT_ROOT / ".env")


@dataclass
class Country:
    code: str
    name: str
    timezone: str
    trends_geo: str
    trends_pn: str
    language: str
    youtube_tags: list[str] = field(default_factory=list)


def load_countries() -> list[Country]:
    path = CONFIG_DIR / "countries.yaml"
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return [Country(**entry) for entry in data.get("countries", [])]


def get_country(code: str) -> Country:
    for country in load_countries():
        if country.code.upper() == code.upper():
            return country
    raise ValueError(f"Unknown country code: {code}")


def local_run_date(country: Country) -> str:
    """Calendar date in the country's timezone (YYYY-MM-DD)."""
    return datetime.now(ZoneInfo(country.timezone)).strftime("%Y-%m-%d")


def local_period(country: Country) -> str:
    """Morning before local noon, Evening from noon onward (scheduled-style)."""
    from src.naming import PERIOD_EVENING, PERIOD_MORNING

    hour = datetime.now(ZoneInfo(country.timezone)).hour
    return PERIOD_MORNING if hour < 12 else PERIOD_EVENING


def local_time_label(country: Country) -> str:
    """Local clock for manual generates, e.g. '9:47 PM'."""
    now = datetime.now(ZoneInfo(country.timezone))
    return now.strftime("%I:%M %p").lstrip("0")


def load_pipeline_config() -> dict[str, Any]:
    path = CONFIG_DIR / "pipeline.yaml"
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def country_output_dir(
    country_code: str,
    run_date: str,
    run_id: int | None = None,
) -> Path:
    """
    Per-run output folder so deletes never wipe another job's files.
    layout: output/{date}/{COUNTRY}/run_{id}/
    """
    path = OUTPUT_DIR / run_date / country_code.upper()
    if run_id is not None:
        path = path / f"run_{run_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path
