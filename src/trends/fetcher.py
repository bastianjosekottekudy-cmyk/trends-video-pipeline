"""Google Trends fetcher with pluggable provider interface."""

from __future__ import annotations

import abc
import json
import logging
import time
from pathlib import Path
from typing import Any

import feedparser
import httpx

from src.config import Country, load_pipeline_config

logger = logging.getLogger(__name__)

TRENDING_RSS_URL = "https://trends.google.com/trending/rss"


class TrendsProvider(abc.ABC):
    @abc.abstractmethod
    def fetch(self, country: Country, limit: int) -> list[str]:
        raise NotImplementedError


class GoogleTrendsRssProvider(TrendsProvider):
    """Fetch daily trends via Google Trends RSS (current public endpoint)."""

    def fetch(self, country: Country, limit: int) -> list[str]:
        geo = country.trends_geo or country.code
        url = f"{TRENDING_RSS_URL}?geo={geo}"
        try:
            with httpx.Client(timeout=20.0, follow_redirects=True) as client:
                resp = client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
                feed = feedparser.parse(resp.text)
        except Exception as exc:
            raise RuntimeError(f"Trends RSS failed for {country.code}: {exc}") from exc

        titles: list[str] = []
        for entry in feed.entries:
            title = str(getattr(entry, "title", "")).strip()
            if title:
                titles.append(title)

        if not titles:
            raise RuntimeError(f"No trends returned for {country.code}")

        return titles[:limit]


class MockTrendsProvider(TrendsProvider):
    """Deterministic trends for offline testing."""

    def fetch(self, country: Country, limit: int) -> list[str]:
        base = [
            f"{country.name} election updates",
            "Breaking sports headline",
            "Tech product launch",
            "Weather alert",
            "Celebrity news",
            "Stock market today",
            "New movie release",
            "Health advisory",
            "Travel destination trend",
            "Viral social media topic",
        ]
        return base[:limit]


def get_trends_provider(name: str = "http") -> TrendsProvider:
    # "http" / "pytrends" both use RSS now — old pytrends endpoints return 404
    providers: dict[str, TrendsProvider] = {
        "http": GoogleTrendsRssProvider(),
        "pytrends": GoogleTrendsRssProvider(),
        "rss": GoogleTrendsRssProvider(),
        "mock": MockTrendsProvider(),
    }
    if name not in providers:
        raise ValueError(f"Unknown trends provider: {name}")
    return providers[name]


def fetch_trends_with_retry(
    country: Country,
    output_dir: Path,
    provider_name: str = "http",
    max_retries: int = 3,
) -> list[str]:
    config = load_pipeline_config()
    limit = int(config.get("top_trends", 10))
    provider = get_trends_provider(provider_name)

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            trends = provider.fetch(country, limit)
            payload: dict[str, Any] = {
                "country": country.code,
                "provider": provider_name if provider_name != "pytrends" else "rss",
                "trends": trends,
            }
            out_path = output_dir / "trends.json"
            out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return trends
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Trends fetch attempt %s/%s failed for %s: %s",
                attempt,
                max_retries,
                country.code,
                exc,
            )
            if attempt < max_retries:
                time.sleep(2**attempt)

    raise RuntimeError(f"Trends fetch failed for {country.code}") from last_error
