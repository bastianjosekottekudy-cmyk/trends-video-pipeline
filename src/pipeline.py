"""Orchestrates a full country pipeline run."""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.audio.tts import generate_narration
from src.config import country_output_dir, get_country, load_pipeline_config
from src.db import store
from src.news.fetcher import fetch_news_for_trends
from src.script.generator import generate_script
from src.trends.fetcher import fetch_trends_with_retry
from src.video.renderer import render_video
from src.youtube.uploader import upload_video

logger = logging.getLogger(__name__)


def run_country_pipeline(
    country_code: str,
    *,
    run_date: str | None = None,
    trends_provider: str = "pytrends",
    news_provider: str = "google_news_rss",
    skip_upload: bool = False,
    existing_run_id: int | None = None,
) -> int:
    country = get_country(country_code)
    run_date = run_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output_dir = country_output_dir(country.code, run_date)

    run_id = existing_run_id or store.create_run(country.code, country.name, run_date)
    logger.info("Starting pipeline run %s for %s", run_id, country.code)

    try:
        store.append_step_log(run_id, "trends", "Fetching Google Trends")
        trends = fetch_trends_with_retry(country, output_dir, provider_name=trends_provider)
        store.update_run(run_id, trends_json=json.dumps(trends))

        store.append_step_log(run_id, "news", f"Fetching news for {len(trends)} trends")
        news = fetch_news_for_trends(trends, country, output_dir, provider_name=news_provider)
        store.update_run(run_id, news_json=json.dumps(news))

        store.append_step_log(run_id, "script", "Generating narration script")
        script_path = generate_script(country, trends, news, output_dir)
        store.update_run(run_id, script_path=script_path)

        store.append_step_log(run_id, "tts", "Generating voiceover")
        audio_path = generate_narration(
            Path(script_path), country, output_dir
        )

        store.append_step_log(run_id, "render", "Rendering video")
        video_path = render_video(country, trends, news, audio_path, output_dir)
        store.update_run(run_id, video_path=video_path)

        youtube_id = "skipped"
        if not skip_upload:
            store.append_step_log(run_id, "upload", "Uploading to YouTube")
            youtube_id = upload_video(video_path, country, trends, news, run_date)
            store.update_run(run_id, youtube_video_id=youtube_id)
        else:
            store.append_step_log(run_id, "upload", "Skipped YouTube upload")

        manifest: dict[str, Any] = {
            "run_id": run_id,
            "country": country.code,
            "run_date": run_date,
            "trends": trends,
            "news": news,
            "script_path": script_path,
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
    parser.add_argument("--mock", action="store_true", help="Use mock trends/news providers")
    parser.add_argument("--skip-upload", action="store_true", help="Skip YouTube upload")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    store.init_db()
    trends_provider = "mock" if args.mock else "pytrends"
    news_provider = "mock" if args.mock else "google_news_rss"

    run_id = run_country_pipeline(
        args.country,
        run_date=args.date,
        trends_provider=trends_provider,
        news_provider=news_provider,
        skip_upload=args.skip_upload,
    )
    print(f"Run {run_id} completed. Check output/ and dashboard at http://127.0.0.1:8080")


if __name__ == "__main__":
    main()
