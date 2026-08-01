"""Generate clear, human-understandable on-screen titles for trends + news."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx

from src.config import Country, get_env, load_pipeline_config
from src.text_format import format_news_headline, format_trend_title, strip_html

logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def _heuristic_cards(
    trends: list[str],
    news: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, str]]:
    """
    Fallback without LLM: prefer a cleaned news headline as the main title
    so viewers understand the story; put the search topic underneath.
    """
    cards: dict[str, dict[str, str]] = {}
    for keyword in trends:
        topic = format_trend_title(keyword)
        items = news.get(keyword) or []
        if items:
            headline = format_news_headline(items[0].get("title", ""), max_len=90)
            snippet = strip_html(items[0].get("summary", ""))
            snippet = re.sub(r"\s+", " ", snippet).strip()
            if len(snippet) > 120:
                snippet = snippet[:117].rsplit(" ", 1)[0] + "…"
            # Main line = what happened; secondary = why it's trending
            cards[keyword] = {
                "title": headline if headline else topic,
                "subtitle": f"Why it's trending: {topic}"
                + (f" — {snippet}" if snippet else ""),
            }
        else:
            cards[keyword] = {
                "title": topic,
                "subtitle": "People are searching this right now",
            }
        # Cap subtitle length for slides
        if len(cards[keyword]["subtitle"]) > 140:
            cards[keyword]["subtitle"] = (
                cards[keyword]["subtitle"][:137].rsplit(" ", 1)[0] + "…"
            )
    return cards


def _parse_json_cards(text: str, trends: list[str]) -> dict[str, dict[str, str]] | None:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to find a JSON array in the response
        match = re.search(r"\[.*\]", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

    if not isinstance(data, list):
        return None

    cards: dict[str, dict[str, str]] = {}
    for i, keyword in enumerate(trends):
        entry = data[i] if i < len(data) else None
        if isinstance(entry, dict):
            title = strip_html(str(entry.get("title") or "")).strip()
            subtitle = strip_html(str(entry.get("subtitle") or "")).strip()
        else:
            title, subtitle = "", ""
        if not title:
            title = format_trend_title(keyword)
        if not subtitle:
            subtitle = "People are searching this right now"
        if len(title) > 90:
            title = title[:87].rsplit(" ", 1)[0] + "…"
        if len(subtitle) > 140:
            subtitle = subtitle[:137].rsplit(" ", 1)[0] + "…"
        cards[keyword] = {"title": title, "subtitle": subtitle}
    return cards


def _groq_clear_cards(
    country: Country,
    trends: list[str],
    news: dict[str, list[dict[str, Any]]],
    api_key: str,
    model: str,
) -> dict[str, dict[str, str]] | None:
    items_block: list[str] = []
    for idx, keyword in enumerate(trends, start=1):
        items_block.append(f"{idx}. Search query: {keyword}")
        for n in (news.get(keyword) or [])[:2]:
            t = strip_html(n.get("title", ""))
            s = strip_html(n.get("summary", ""))[:200]
            if t:
                items_block.append(f"   News: {t}")
            if s:
                items_block.append(f"   Context: {s}")

    prompt = f"""You rewrite Google Trends search queries into clear on-screen titles for a {country.name} news video.

For EACH item below, output one object with:
- "title": a clear headline anyone can understand in under 12 words (what is happening / who / what)
- "subtitle": one plain sentence explaining why people are searching this (use only the news/context given; do not invent facts)

Rules:
- No clickbait, no ALL CAPS, no hashtags
- Prefer meaning over repeating the raw search query
- If news is thin, still explain the search topic in plain language
- Return ONLY a JSON array with exactly {len(trends)} objects, in the same order

Items:
""" + "\n".join(items_block)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "temperature": 0.4,
        "max_tokens": 3500,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You write crystal-clear video captions. "
                    "Output valid JSON only. Never invent news."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    }
    with httpx.Client(timeout=90.0) as client:
        resp = client.post(GROQ_URL, headers=headers, json=payload)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    return _parse_json_cards(content, trends)


def generate_clear_titles(
    country: Country,
    trends: list[str],
    news: dict[str, list[dict[str, Any]]],
    output_dir: Path,
) -> dict[str, dict[str, str]]:
    """
    Returns {trend_keyword: {"title": ..., "subtitle": ...}} for slide overlays.
    """
    config = load_pipeline_config()
    model = str(config.get("script", {}).get("model", "llama-3.1-8b-instant"))
    api_key = get_env("GROQ_API_KEY")
    provider = "heuristic"

    cards: dict[str, dict[str, str]] | None = None
    if api_key:
        try:
            cards = _groq_clear_cards(country, trends, news, api_key, model)
            if cards:
                provider = "groq"
        except Exception as exc:
            logger.warning("Clear-title LLM failed, using news/heuristic: %s", exc)

    if not cards:
        cards = _heuristic_cards(trends, news)

    payload = {"provider": provider, "cards": cards}
    (output_dir / "display_titles.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    logger.info("Display titles ready via %s (%s items)", provider, len(cards))
    return cards
