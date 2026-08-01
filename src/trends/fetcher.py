"""Google Trends fetcher with pluggable provider interface."""

from __future__ import annotations

import abc
import json
import logging
import time
from pathlib import Path
from typing import Any

from src.config import Country, load_pipeline_config

logger = logging.getLogger(__name__)


class TrendsProvider(abc.ABC):
    @abc.abstractmethod
    def fetch(self, country: Country, limit: int) -> list[str]:
        raise NotImplementedError


class PytrendsProvider(TrendsProvider):
    """Fetch daily trending searches via pytrends."""

    def fetch(self, country: Country, limit: int) -> list[str]:
        from pytrends.request import TrendReq

        pytrends = TrendReq(hl="en-US", tz=360, retries=3, backoff_factor=0.5)
        trends: list[str] = []

        # Primary: daily trends for geo
        try:
            df = pytrends.today_searches(pn=country.trends_geo)
            if df is not None and not df.empty:
                trends = [str(x).strip() for x in df.iloc[:, 0].tolist() if str(x).strip()]
        except Exception as exc:
            logger.warning("today_searches failed for %s: %s", country.code, exc)

        # Fallback: trending_searches by pn name
        if not trends:
            try:
                df = pytrends.trending_searches(pn=country.trends_pn)
                if df is not None and not df.empty:
                    trends = [str(x).strip() for x in df.iloc[:, 0].tolist() if str(x).strip()]
            except Exception as exc:
                logger.warning("trending_searches failed for %s: %s", country.code, exc)

        if not trends:
            raise RuntimeError(f"Could not fetch trends for {country.code}")

        return trends[:limit]


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


def get_trends_provider(name: str = "pytrends") -> TrendsProvider:
    providers: dict[str, TrendsProvider] = {
        "pytrends": PytrendsProvider(),
        "mock": MockTrendsProvider(),
    }
    if name not in providers:
        raise ValueError(f"Unknown trends provider: {name}")
    return providers[name]


def fetch_trends_with_retry(
    country: Country,
    output_dir: Path,
    provider_name: str = "pytrends",
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
                "provider": provider_name,
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
