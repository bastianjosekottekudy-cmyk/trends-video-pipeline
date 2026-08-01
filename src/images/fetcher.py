"""Fetch related still images for trend slides (free sources)."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx

from src.config import Country, load_pipeline_config

logger = logging.getLogger(__name__)

USER_AGENT = "TrendsVideoPipeline/1.0 (local; educational)"
WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"
OPENVERSE_API = "https://api.openverse.org/v1/images/"


def _safe_stem(text: str) -> str:
    return re.sub(r"[^\w\-]+", "_", text)[:40].strip("_") or "trend"


def _download_image(client: httpx.Client, url: str, dest: Path) -> bool:
    try:
        resp = client.get(url, follow_redirects=True, timeout=20.0)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "image" not in content_type and not url.lower().endswith(
            (".jpg", ".jpeg", ".png", ".webp")
        ):
            return False
        data = resp.content
        if len(data) < 2000:
            return False
        dest.write_bytes(data)
        return True
    except Exception as exc:
        logger.debug("Image download failed %s: %s", url, exc)
        return False


def _wikimedia_urls(client: httpx.Client, query: str, limit: int) -> list[str]:
    try:
        resp = client.get(
            WIKIMEDIA_API,
            params={
                "action": "query",
                "generator": "search",
                "gsrsearch": query,
                "gsrlimit": max(limit * 2, 4),
                "gsrnamespace": 6,
                "prop": "imageinfo",
                "iiprop": "url|mime|size",
                "iiurlwidth": 1920,
                "format": "json",
            },
            timeout=20.0,
        )
        resp.raise_for_status()
        pages = resp.json().get("query", {}).get("pages", {})
        urls: list[str] = []
        for page in pages.values():
            infos = page.get("imageinfo") or []
            if not infos:
                continue
            info = infos[0]
            mime = str(info.get("mime", ""))
            if not mime.startswith("image/") or "svg" in mime:
                continue
            url = info.get("thumburl") or info.get("url")
            if url:
                urls.append(url)
            if len(urls) >= limit:
                break
        return urls
    except Exception as exc:
        logger.warning("Wikimedia search failed for '%s': %s", query, exc)
        return []


def _openverse_urls(client: httpx.Client, query: str, limit: int) -> list[str]:
    try:
        resp = client.get(
            OPENVERSE_API,
            params={
                "q": query,
                "page_size": limit,
                "license_type": "commercial,modification",
            },
            timeout=20.0,
            headers={"Accept": "application/json"},
        )
        if resp.status_code == 401:
            # Openverse sometimes requires auth for higher limits; skip quietly
            return []
        resp.raise_for_status()
        results = resp.json().get("results") or []
        urls: list[str] = []
        for item in results:
            url = item.get("url") or item.get("thumbnail")
            if url:
                urls.append(url)
            if len(urls) >= limit:
                break
        return urls
    except Exception as exc:
        logger.warning("Openverse search failed for '%s': %s", query, exc)
        return []


def _og_image_url(client: httpx.Client, page_url: str) -> str | None:
    if not page_url or not page_url.startswith("http"):
        return None
    try:
        resp = client.get(page_url, follow_redirects=True, timeout=15.0)
        resp.raise_for_status()
        html = resp.text[:200_000]
        patterns = [
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, flags=re.IGNORECASE)
            if match:
                url = match.group(1).strip()
                if url.startswith("//"):
                    url = "https:" + url
                if url.startswith("http"):
                    return url
    except Exception as exc:
        logger.debug("og:image fetch failed for %s: %s", page_url, exc)
    return None


def fetch_images_for_trends(
    trends: list[str],
    news: dict[str, list[dict[str, Any]]],
    country: Country,
    output_dir: Path,
) -> dict[str, list[str]]:
    """
    Download related images per trend.
    Returns {trend: [local_path, ...]}
    """
    config = load_pipeline_config()
    max_images = int(config.get("max_images_per_trend", 2))
    providers = config.get("images", {}).get(
        "providers", ["wikimedia", "openverse", "news_og"]
    )

    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, list[str]] = {}

    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(headers=headers, follow_redirects=True) as client:
        for rank, keyword in enumerate(trends, start=1):
            urls: list[str] = []
            stem = _safe_stem(keyword)

            if "news_og" in providers:
                headlines = news.get(keyword) or []
                for item in headlines[:2]:
                    og = _og_image_url(client, item.get("link", ""))
                    if og and og not in urls:
                        urls.append(og)
                    if len(urls) >= max_images:
                        break

            if len(urls) < max_images and "wikimedia" in providers:
                for url in _wikimedia_urls(client, keyword, max_images):
                    if url not in urls:
                        urls.append(url)
                    if len(urls) >= max_images:
                        break

            if len(urls) < max_images and "openverse" in providers:
                for url in _openverse_urls(client, keyword, max_images):
                    if url not in urls:
                        urls.append(url)
                    if len(urls) >= max_images:
                        break

            saved: list[str] = []
            for i, url in enumerate(urls[:max_images], start=1):
                ext = ".jpg"
                lower = url.lower()
                if ".png" in lower:
                    ext = ".png"
                elif ".webp" in lower:
                    ext = ".webp"
                dest = images_dir / f"{rank:02d}_{stem}_{i}{ext}"
                if _download_image(client, url, dest):
                    saved.append(str(dest))

            result[keyword] = saved
            logger.info(
                "Images for '%s' (%s): %s",
                keyword,
                country.code,
                len(saved),
            )

    out_path = output_dir / "images.json"
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
