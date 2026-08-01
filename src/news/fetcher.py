"""News enrichment for trending keywords."""

from __future__ import annotations

import json
import logging
import urllib.parse
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import feedparser

from src.config import Country, load_pipeline_config

logger = logging.getLogger(__name__)


@dataclass
class NewsItem:
    title: str
    link: str
    summary: str
    source: str = "google_news_rss"


class NewsProvider:
    def fetch_for_keyword(
        self, keyword: str, country: Country, max_items: int
    ) -> list[NewsItem]:
        raise NotImplementedError


class GoogleNewsRssProvider(NewsProvider):
    def fetch_for_keyword(
        self, keyword: str, country: Country, max_items: int
    ) -> list[NewsItem]:
        lang = country.language or "en"
        query = urllib.parse.quote(keyword)
        url = (
            f"https://news.google.com/rss/search?q={query}"
            f"&hl={lang}&gl={country.code}&ceid={country.code}:{lang}"
        )
        feed = feedparser.parse(url)
        items: list[NewsItem] = []
        for entry in feed.entries[:max_items]:
            items.append(
                NewsItem(
                    title=getattr(entry, "title", keyword),
                    link=getattr(entry, "link", ""),
                    summary=getattr(entry, "summary", "")[:500],
                    source="google_news_rss",
                )
            )
        return items


class MockNewsProvider(NewsProvider):
    def fetch_for_keyword(
        self, keyword: str, country: Country, max_items: int
    ) -> list[NewsItem]:
        return [
            NewsItem(
                title=f"Latest on {keyword} in {country.name}",
                link="https://example.com/news",
                summary=f"Reports indicate growing interest in {keyword}.",
            )
        ][:max_items]


def get_news_provider(name: str = "google_news_rss") -> NewsProvider:
    providers: dict[str, NewsProvider] = {
        "google_news_rss": GoogleNewsRssProvider(),
        "mock": MockNewsProvider(),
    }
    if name not in providers:
        raise ValueError(f"Unknown news provider: {name}")
    return providers[name]


def fetch_news_for_trends(
    trends: list[str],
    country: Country,
    output_dir: Path,
    provider_name: str = "google_news_rss",
) -> dict[str, list[dict[str, Any]]]:
    config = load_pipeline_config()
    max_items = int(config.get("max_news_per_trend", 2))
    provider = get_news_provider(provider_name)

    result: dict[str, list[dict[str, Any]]] = {}
    for keyword in trends:
        try:
            items = provider.fetch_for_keyword(keyword, country, max_items)
            result[keyword] = [asdict(item) for item in items]
        except Exception as exc:
            logger.warning("News fetch failed for '%s': %s", keyword, exc)
            result[keyword] = []

    out_path = output_dir / "news.json"
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
