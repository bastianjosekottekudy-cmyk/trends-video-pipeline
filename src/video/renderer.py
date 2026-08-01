"""Video renderer: slide cards + narration audio."""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from src.config import Country, load_pipeline_config

logger = logging.getLogger(__name__)


def _get_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _make_slide(
    width: int,
    height: int,
    title: str,
    subtitle: str,
    rank: int | None = None,
) -> Image.Image:
    img = Image.new("RGB", (width, height), color=(18, 24, 38))
    draw = ImageDraw.Draw(img)
    title_font = _get_font(72)
    sub_font = _get_font(36)
    rank_font = _get_font(48)

    if rank is not None:
        draw.text((80, 60), f"#{rank}", fill=(100, 180, 255), font=rank_font)

    wrapped_title = textwrap.fill(title, width=28)
    draw.text((80, 180), wrapped_title, fill=(255, 255, 255), font=title_font)

    wrapped_sub = textwrap.fill(subtitle, width=42)
    draw.text((80, height - 280), wrapped_sub, fill=(200, 210, 220), font=sub_font)

    draw.rectangle([(0, height - 8), (width, height)], fill=(100, 180, 255))
    return img


def render_video(
    country: Country,
    trends: list[str],
    news: dict[str, list[dict[str, Any]]],
    audio_path: str,
    output_dir: Path,
) -> str:
    config = load_pipeline_config()
    video_cfg = config.get("video", {})
    width = int(video_cfg.get("width", 1920))
    height = int(video_cfg.get("height", 1080))
    fps = int(video_cfg.get("fps", 24))

    from moviepy import AudioFileClip, ImageClip, concatenate_videoclips

    slides_dir = output_dir / "slides"
    slides_dir.mkdir(exist_ok=True)

    # Intro slide
    intro = _make_slide(
        width,
        height,
        f"Top Trends — {country.name}",
        "Daily Google Trends & News Recap",
    )
    intro_path = slides_dir / "00_intro.png"
    intro.save(intro_path)

    slide_paths: list[Path] = [intro_path]
    for idx, keyword in enumerate(trends, start=1):
        headlines = news.get(keyword, [])
        subtitle = headlines[0]["title"] if headlines else "Trending now on Google"
        slide = _make_slide(width, height, keyword, subtitle, rank=idx)
        slide_path = slides_dir / f"{idx:02d}_{keyword[:30].replace('/', '-')}.png"
        slide.save(slide_path)
        slide_paths.append(slide_path)

    # Outro slide
    outro = _make_slide(width, height, "Thanks for watching!", "Subscribe for daily trends")
    outro_path = slides_dir / "99_outro.png"
    outro.save(outro_path)
    slide_paths.append(outro_path)

    audio = AudioFileClip(audio_path)
    total_duration = audio.duration
    per_slide = max(total_duration / len(slide_paths), 2.0)

    clips = [
        ImageClip(str(path)).with_duration(per_slide).with_fps(fps)
        for path in slide_paths
    ]
    video = concatenate_videoclips(clips, method="compose")
    video = video.with_audio(audio)

    # Trim to audio length if slides run longer
    if video.duration > audio.duration:
        video = video.subclipped(0, audio.duration)

    output_path = output_dir / "final.mp4"
    video.write_videofile(
        str(output_path),
        fps=fps,
        codec="libx264",
        audio_codec="aac",
        logger=None,
    )

    video.close()
    audio.close()

    return str(output_path)
