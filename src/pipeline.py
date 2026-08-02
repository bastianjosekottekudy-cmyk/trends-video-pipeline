"""Orchestrates a full country pipeline run."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from src.audio.tts import generate_narration
from src.config import (
    Country,
    country_output_dir,
    get_country,
    load_pipeline_config,
    local_run_date,
)
from src.db import store
from src.images.fetcher import fetch_images_for_trends
from src.naming import build_video_title, resolve_title_slot
from src.news.fetcher import fetch_news_for_trends
from src.script.generator import generate_script
from src.titles.clarity import generate_clear_titles
from src.trends.fetcher import fetch_trends_with_retry
from src.video.renderer import render_video

logger = logging.getLogger(__name__)


def _youtube_enabled() -> bool:
    from src.youtube.uploader import youtube_enabled

    return youtube_enabled()


def _attempt_youtube_upload(
    run_id: int,
    video_path: str,
    country: Country,
    trends: list[str],
    news: dict[str, list[dict[str, Any]]],
    run_date: str,
    period: str | None = None,
) -> str | None:
    """Upload without failing the local pipeline. Returns video id or None."""
    from src.youtube.uploader import YouTubeUploadError, upload_video

    store.set_upload_status(run_id, "uploading", upload_error=None)
    store.append_step_log(run_id, "upload", "Uploading to YouTube")
    try:
        youtube_id = upload_video(
            video_path, country, trends, news, run_date, period=period
        )
        store.set_upload_status(
            run_id,
            "uploaded",
            youtube_video_id=youtube_id,
            upload_error=None,
        )
        store.append_step_log(
            run_id, "upload", f"Uploaded https://www.youtube.com/watch?v={youtube_id}"
        )
        return youtube_id
    except YouTubeUploadError as exc:
        logger.warning("YouTube upload failed for run %s: %s", run_id, exc)
        store.set_upload_status(run_id, "failed", upload_error=str(exc))
        store.append_step_log(run_id, "upload", f"Upload failed: {exc}")
        return None
    except Exception as exc:
        logger.exception("Unexpected YouTube upload error for run %s", run_id)
        store.set_upload_status(run_id, "failed", upload_error=str(exc))
        store.append_step_log(run_id, "upload", f"Upload failed: {exc}")
        return None


def run_country_pipeline(
    country_code: str,
    *,
    run_date: str | None = None,
    trends_provider: str = "http",
    news_provider: str = "google_news_rss",
    skip_upload: bool = True,
    force_upload: bool = False,
    existing_run_id: int | None = None,
    period: str | None = None,
) -> int:
    country = get_country(country_code)
    if not run_date and existing_run_id:
        existing = store.get_run(existing_run_id)
        if existing and existing.get("run_date"):
            run_date = str(existing["run_date"])
        if existing and not period and existing.get("period"):
            period = str(existing["period"])
    run_date = run_date or local_run_date(country)
    period = resolve_title_slot(period)

    run_id = existing_run_id or store.create_run(
        country.code, country.name, run_date, period=period
    )
    if existing_run_id and period:
        store.update_run(run_id, period=period)
    output_dir = country_output_dir(country.code, run_date, run_id=run_id)
    logger.info(
        "Starting pipeline run %s for %s (%s) → %s",
        run_id,
        country.code,
        period or "unspecified",
        output_dir,
    )

    try:
        store.append_step_log(
            run_id,
            "start",
            f"{period or 'Manual'} run for {country.name}",
        )
        store.append_step_log(run_id, "trends", "Fetching Google Trends (top 20)")
        trends = fetch_trends_with_retry(country, output_dir, provider_name=trends_provider)
        store.update_run(run_id, trends_json=json.dumps(trends))

        store.append_step_log(run_id, "news", f"Fetching news for {len(trends)} trends")
        news = fetch_news_for_trends(trends, country, output_dir, provider_name=news_provider)
        store.update_run(run_id, news_json=json.dumps(news))

        store.append_step_log(run_id, "images", "Fetching related images")
        images = fetch_images_for_trends(trends, news, country, output_dir)

        store.append_step_log(run_id, "titles", "Writing clear on-screen titles")
        display_titles = generate_clear_titles(country, trends, news, output_dir)

        store.append_step_log(run_id, "script", "Generating witty narration")
        script_path = generate_script(country, trends, news, output_dir)
        store.update_run(run_id, script_path=script_path)

        # Prefer keywords that actually have spoken beats (may be trimmed)
        spoken_trends = list(trends)
        segments_path = output_dir / "script_segments.json"
        if segments_path.exists():
            try:
                seg_data = json.loads(segments_path.read_text(encoding="utf-8"))
                keywords = seg_data.get("trend_keywords") or []
                if keywords:
                    spoken_trends = [str(k) for k in keywords]
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Could not read script segments: %s", exc)

        store.append_step_log(run_id, "tts", "Generating voiceover")
        audio_path = generate_narration(Path(script_path), country, output_dir)

        store.append_step_log(run_id, "render", "Rendering video with image slides")
        video_title = build_video_title(country.name, run_date, period)
        video_path = render_video(
            country,
            spoken_trends,
            news,
            audio_path,
            output_dir,
            run_date=run_date,
            images=images,
            display_titles=display_titles,
            period=period,
        )
        store.update_run(run_id, video_path=video_path)

        youtube_id = None
        should_upload = force_upload or (not skip_upload and _youtube_enabled())
        if should_upload:
            youtube_id = _attempt_youtube_upload(
                run_id,
                video_path,
                country,
                trends,
                news,
                run_date,
                period=period,
            )
        else:
            store.append_step_log(
                run_id,
                "local",
                f"Saved as '{video_title}.mp4' — upload from dashboard or enable auto-upload",
            )

        manifest: dict[str, Any] = {
            "run_id": run_id,
            "country": country.code,
            "run_date": run_date,
            "period": period,
            "trends": trends,
            "trends_count": len(trends),
            "news": news,
            "images": images,
            "display_titles": display_titles,
            "script_path": script_path,
            "video_title": video_title,
            "video_path": video_path,
            "youtube_video_id": youtube_id,
        }
        manifest_path = output_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        store.append_step_log(run_id, "done", "Pipeline completed successfully")
        store.finish_run(run_id, "success")
        logger.info("Pipeline run %s completed for %s", run_id, country.code)
        return run_id

    except Exception as exc:
        logger.exception("Pipeline run %s failed: %s", run_id, exc)
        store.append_step_log(run_id, "error", str(exc))
        store.finish_run(run_id, "failed", error_message=str(exc))
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Run trends video pipeline for one country")
    parser.add_argument("--country", required=True, help="Country code (e.g. US)")
    parser.add_argument("--date", default=None, help="Run date YYYY-MM-DD")
    parser.add_argument(
        "--period",
        default=None,
        help="Title slot: Morning, Evening, or a clock label like '9:47 PM' "
        "(default: local clock time)",
    )
    parser.add_argument("--mock", action="store_true", help="Use mock trends/news providers")
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Force YouTube upload (requires youtube.enabled or this flag)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    store.init_db()
    trends_provider = "mock" if args.mock else "http"
    news_provider = "mock" if args.mock else "google_news_rss"

    country = get_country(args.country)
    from src.config import local_time_label

    period = resolve_title_slot(args.period) or local_time_label(country)

    run_id = run_country_pipeline(
        args.country,
        run_date=args.date,
        trends_provider=trends_provider,
        news_provider=news_provider,
        skip_upload=not _youtube_enabled(),
        force_upload=args.upload,
        period=period,
    )
    print(f"Run {run_id} completed. Check output/ and dashboard at http://127.0.0.1:8080")


if __name__ == "__main__":
    main()
