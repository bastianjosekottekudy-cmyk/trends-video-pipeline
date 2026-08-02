"""YouTube resumable video upload."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from src.config import Country, get_env, load_pipeline_config
from src.naming import build_video_title
from src.youtube.auth import get_credentials

logger = logging.getLogger(__name__)


class YouTubeUploadError(RuntimeError):
    """Raised when an upload cannot proceed or fails."""


def _build_description(
    country: Country,
    trends: list[str],
    news: dict[str, list[dict[str, Any]]],
    run_date: str,
) -> str:
    lines = [
        build_video_title(country.name, run_date),
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


def youtube_enabled() -> bool:
    config = load_pipeline_config()
    return bool(config.get("youtube", {}).get("enabled", False))


def upload_video(
    video_path: str,
    country: Country,
    trends: list[str],
    news: dict[str, list[dict[str, Any]]],
    run_date: str,
) -> str:
    if get_env("SKIP_YOUTUBE_UPLOAD", "false").lower() in ("true", "1", "yes"):
        raise YouTubeUploadError(
            "YouTube upload skipped (SKIP_YOUTUBE_UPLOAD=true in .env)"
        )

    if not youtube_enabled():
        raise YouTubeUploadError(
            "YouTube upload is disabled (set youtube.enabled: true in config/pipeline.yaml)"
        )

    path = Path(video_path)
    if not path.is_file():
        raise YouTubeUploadError(f"Video file not found: {video_path}")

    config = load_pipeline_config()
    yt_cfg = config.get("youtube", {})

    try:
        creds = get_credentials()
    except Exception as exc:
        raise YouTubeUploadError(
            f"YouTube auth failed. Run: python -m src.youtube.auth ({exc})"
        ) from exc

    youtube = build("youtube", "v3", credentials=creds)

    title = build_video_title(country.name, run_date)
    description = _build_description(country, trends, news, run_date)
    tags = list(country.youtube_tags) + ["google trends", "daily news"]

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags,
            "categoryId": str(yt_cfg.get("category_id", "25")),
        },
        "status": {
            "privacyStatus": yt_cfg.get("privacy", "public"),
            "selfDeclaredMadeForKids": bool(yt_cfg.get("made_for_kids", False)),
        },
    }

    media = MediaFileUpload(str(path), chunksize=256 * 1024, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    try:
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info("Upload progress: %.1f%%", status.progress() * 100)
    except Exception as exc:
        raise YouTubeUploadError(f"YouTube API upload failed: {exc}") from exc

    if not response or not response.get("id"):
        raise YouTubeUploadError("YouTube API returned no video id")

    video_id = response["id"]
    logger.info("Uploaded video: https://youtube.com/watch?v=%s", video_id)
    return video_id


def main() -> None:
    """Test upload with latest video: python -m src.youtube.uploader --country US"""
    import argparse
    import json

    from src.config import OUTPUT_DIR, get_country, local_run_date
    from src.naming import video_filename

    parser = argparse.ArgumentParser()
    parser.add_argument("--country", required=True)
    parser.add_argument("--date", default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    country = get_country(args.country)
    run_date = args.date or local_run_date(country)
    out_dir = OUTPUT_DIR / run_date / country.code.upper()
    video_path = out_dir / video_filename(country.name, run_date)
    if not video_path.exists():
        # Prefer newest per-run folder
        run_dirs = sorted(out_dir.glob("run_*"), reverse=True)
        mp4s: list[Path] = []
        for rd in run_dirs:
            mp4s.extend(rd.glob("*.mp4"))
        mp4s.extend(out_dir.glob("*.mp4"))
        if not mp4s:
            raise FileNotFoundError(f"No video under {out_dir}")
        video_path = mp4s[0]
        # Load trends/news from same folder when possible
        out_dir = video_path.parent

    trends = json.loads((out_dir / "trends.json").read_text(encoding="utf-8"))["trends"]
    news = json.loads((out_dir / "news.json").read_text(encoding="utf-8"))
    video_id = upload_video(str(video_path), country, trends, news, run_date)
    print(f"https://www.youtube.com/watch?v={video_id}")


if __name__ == "__main__":
    main()
