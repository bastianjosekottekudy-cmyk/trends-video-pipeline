"""Narration script generator — Groq witty host or template fallback."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx

from src.config import Country, get_env, load_pipeline_config

logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
WORDS_PER_SEC = 2.3  # ~138 wpm


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def _max_words() -> int:
    config = load_pipeline_config()
    max_sec = int(config.get("max_video_duration_sec", 570))
    return max(200, int(max_sec * WORDS_PER_SEC))


def _template_script(
    country: Country,
    trends: list[str],
    news: dict[str, list[dict[str, Any]]],
) -> str:
    config = load_pipeline_config()
    script_cfg = config.get("script", {})
    intro = script_cfg.get(
        "intro_template",
        "Here are today's top trending searches in {country}.",
    ).format(country=country.name)
    outro = script_cfg.get(
        "outro_template",
        "Thanks for watching. Subscribe for daily trends updates.",
    )

    parts = [
        intro,
        f"We've got {len(trends)} stories heating up — let's move.",
    ]
    for idx, keyword in enumerate(trends, start=1):
        parts.append(f"Number {idx}: {keyword}.")
        headlines = news.get(keyword, [])
        if headlines:
            headline = _strip_html(headlines[0].get("title", ""))
            summary = _strip_html(headlines[0].get("summary", ""))[:180]
            if headline:
                parts.append(f"The buzz: {headline}.")
            if summary and summary != headline:
                parts.append(summary)
        else:
            parts.append(f"Search interest is spiking across {country.name}.")

    parts.append(outro)
    return " ".join(parts)


def _build_prompt(
    country: Country,
    trends: list[str],
    news: dict[str, list[dict[str, Any]]],
    max_words: int,
) -> str:
    style = load_pipeline_config().get("script", {}).get(
        "style",
        "witty, concise, news-host energy; no invented facts",
    )
    lines = [
        f"Write a spoken narration script for a daily Google Trends video about {country.name}.",
        f"Style: {style}",
        f"Hard limit: under {max_words} words. Aim for punchy lines, not an essay.",
        "Only use the facts below. Do not invent news, scores, or quotes.",
        "Structure: short intro, then each trend in order with a witty beat + the given news, short outro.",
        "Output plain narration text only — no titles, markdown, or stage directions.",
        "",
        "Trends and news:",
    ]
    for idx, keyword in enumerate(trends, start=1):
        lines.append(f"{idx}. {keyword}")
        for item in (news.get(keyword) or [])[:2]:
            title = _strip_html(item.get("title", ""))
            summary = _strip_html(item.get("summary", ""))[:220]
            if title:
                lines.append(f"   - Headline: {title}")
            if summary:
                lines.append(f"   - Snippet: {summary}")
    return "\n".join(lines)


def _call_groq(prompt: str, model: str, api_key: str) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "temperature": 0.7,
        "max_tokens": 2500,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a witty daily trends video host. "
                    "Stay factual. Never invent events."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    }
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(GROQ_URL, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    text = data["choices"][0]["message"]["content"].strip()
    # Strip accidental markdown fences
    text = re.sub(r"^```(?:\w+)?\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return text.strip()


def generate_script(
    country: Country,
    trends: list[str],
    news: dict[str, list[dict[str, Any]]],
    output_dir: Path,
) -> str:
    config = load_pipeline_config()
    script_cfg = config.get("script", {})
    provider = str(script_cfg.get("provider", "groq")).lower()
    model = str(script_cfg.get("model", "llama-3.1-8b-instant"))
    api_key = get_env("GROQ_API_KEY")
    max_words = _max_words()

    used_provider = "template"
    script = ""
    active_trends = list(trends)

    if provider == "groq" and api_key:
        try:
            prompt = _build_prompt(country, active_trends, news, max_words)
            script = _call_groq(prompt, model, api_key)
            used_provider = "groq"
            if _word_count(script) > max_words:
                logger.warning(
                    "Script over budget (%s > %s); regenerating shorter",
                    _word_count(script),
                    max_words,
                )
                # Drop lowest-ranked trends and regenerate once
                keep = max(8, len(active_trends) - 5)
                active_trends = active_trends[:keep]
                prompt = _build_prompt(
                    country,
                    active_trends,
                    news,
                    int(max_words * 0.85),
                )
                script = _call_groq(prompt, model, api_key)
            if _word_count(script) > max_words:
                # Hard trim by sentences
                words = script.split()
                script = " ".join(words[:max_words])
        except Exception as exc:
            logger.warning("Groq narration failed, using template: %s", exc)
            script = ""

    if not script:
        used_provider = "template"
        script = _template_script(country, active_trends, news)
        if _word_count(script) > max_words:
            # Shorten template by keeping fewer trends
            while _word_count(script) > max_words and len(active_trends) > 8:
                active_trends = active_trends[:-1]
                script = _template_script(country, active_trends, news)

    script_path = output_dir / "script.txt"
    script_path.write_text(script, encoding="utf-8")

    meta = {
        "provider": used_provider,
        "model": model if used_provider == "groq" else None,
        "word_count": _word_count(script),
        "max_words": max_words,
        "trends_in_script": len(active_trends),
    }
    (output_dir / "script_meta.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )
    logger.info(
        "Script ready via %s (%s words, budget %s)",
        used_provider,
        meta["word_count"],
        max_words,
    )
    return str(script_path)
