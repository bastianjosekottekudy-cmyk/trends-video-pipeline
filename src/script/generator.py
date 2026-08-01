"""Template-based narration script generator."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.config import Country, load_pipeline_config


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def generate_script(
    country: Country,
    trends: list[str],
    news: dict[str, list[dict[str, Any]]],
    output_dir: Path,
) -> str:
    config = load_pipeline_config()
    script_cfg = config.get("script", {})
    intro = script_cfg.get(
        "intro_template", "Here are today's top trending searches in {country}."
    ).format(country=country.name)
    outro = script_cfg.get(
        "outro_template", "Thanks for watching. Subscribe for daily trends updates."
    )

    parts = [intro]
    for idx, keyword in enumerate(trends, start=1):
        parts.append(f"Number {idx}: {keyword}.")
        headlines = news.get(keyword, [])
        if headlines:
            headline = _strip_html(headlines[0].get("title", ""))
            if headline:
                parts.append(f"Related news: {headline}.")
        else:
            parts.append(f"This topic is gaining search interest across {country.name}.")

    parts.append(outro)
    script = " ".join(parts)
    script_path = output_dir / "script.txt"
    script_path.write_text(script, encoding="utf-8")
    return str(script_path)
