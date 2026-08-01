"""YouTube resumable video upload."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from src.config import Country, get_env, load_pipeline_config
from src.youtube.auth import get_credentials

logger = logging.getLogger(__name__)


def _build_description(
    country: Country,
    trends: list[str],
    news: dict[str, list[dict[str, Any]]],
    run_date: str,
) -> str:
    lines = [
        f"Top Google Trends in {country.name} — {run_date}",
        "",
        "Trending searches:",
    ]
    for idx, keyword in enumerate(trends, start=1):
        lines.append(f"{idx}. {keyword}")
        headlines = news.get(keyword, [])
        for item in headlines[:1]:
            link = item.get("link", "")
            if link:
                lines.append(f"   News: {link}")
    lines.extend(["", "#GoogleTrends", "#DailyNews", f"#{country.code}"])
    return "\n".join(lines)


def upload_video(
    video_path: str,
    country: Country,
    trends: list[str],
    news: dict[str, list[dict[str, Any]]],
    run_date: str,
) -> str:
    if get_env("SKIP_YOUTUBE_UPLOAD", "false").lower() in ("true", "1", "yes"):
        logger.info("Skipping YouTube upload (SKIP_YOUTUBE_UPLOAD=true)")
        return "skipped"

    config = load_pipeline_config()
    yt_cfg = config.get("youtube", {})

    creds = get_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    title = f"Top Google Trends in {country.name} — {run_date}"
    description = _build_description(country, trends, news, run_date)
    tags = list(country.youtube_tags) + ["google trends", "daily news"]

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": str(yt_cfg.get("category_id", "25")),
        },
        "status": {
            "privacyStatus": yt_cfg.get("privacy", "public"),
            "selfDeclaredMadeForKids": bool(yt_cfg.get("made_for_kids", False)),
        },
    }

    media = MediaFileUpload(video_path, chunksize=256 * 1024, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logger.info("Upload progress: %.1f%%", status.progress() * 100)

    video_id = response["id"]
    logger.info("Uploaded video: https://youtube.com/watch?v=%s", video_id)
    return video_id


def main() -> None:
    """Test upload with latest video: python -m src.youtube.uploader --country US"""
    import argparse
    from pathlib import Path

    from src.config import OUTPUT_DIR, get_country

    parser = argparse.ArgumentParser()
    parser.add_argument("--country", required=True)
    parser.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    country = get_country(args.country)
    out_dir = OUTPUT_DIR / args.date / country.code.upper()
    video_path = out_dir / "final.mp4"
    if not video_path.exists():
        raise FileNotFoundError(f"No video at {video_path}")

    import json

    trends = json.loads((out_dir / "trends.json").read_text())["trends"]
    news = json.loads((out_dir / "news.json").read_text())
    upload_video(str(video_path), country, trends, news, args.date)


if __name__ == "__main__":
    main()
