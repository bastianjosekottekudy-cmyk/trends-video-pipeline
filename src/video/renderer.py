"""Video renderer: related image slides + narration (GPU-accelerated when available)."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from src.config import Country, load_pipeline_config
from src.naming import build_video_title, video_filename
from src.text_format import format_news_headline, format_trend_title

logger = logging.getLogger(__name__)


def _card_for(
    keyword: str,
    news: dict[str, list[dict[str, Any]]],
    display_titles: dict[str, dict[str, str]] | None,
) -> tuple[str, str]:
    """Resolve clear title/subtitle for a trend slide."""
    if display_titles and keyword in display_titles:
        card = display_titles[keyword]
        title = (card.get("title") or "").strip()
        subtitle = (card.get("subtitle") or "").strip()
        if title:
            return title, subtitle or "People are searching this right now"
    # Fallback: news-first for meaning
    topic = format_trend_title(keyword)
    headlines = news.get(keyword) or []
    if headlines:
        return (
            format_news_headline(headlines[0].get("title", ""), max_len=90),
            f"Why it's trending: {topic}",
        )
    return topic, "People are searching this right now"


def _get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        [
            "C:/Windows/Fonts/segoeuib.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/calibrib.ttf",
        ]
        if bold
        else [
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/calibri.ttf",
        ]
    )
    candidates += ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _cover_resize(img: Image.Image, width: int, height: int) -> Image.Image:
    src_w, src_h = img.size
    scale = max(width / src_w, height / src_h)
    new_w, new_h = int(src_w * scale), int(src_h * scale)
    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = (new_w - width) // 2
    top = (new_h - height) // 2
    return img.crop((left, top, left + width, top + height))


def _draw_gradient(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    for y in range(height // 3):
        alpha = int(170 * (1 - y / (height / 3)))
        draw.rectangle([(0, y), (width, y + 1)], fill=(0, 0, 0, alpha))
    for i, y in enumerate(range(height - int(height * 0.55), height)):
        alpha = min(220, int(230 * (i / (height * 0.55))))
        draw.rectangle([(0, y), (width, y + 1)], fill=(0, 0, 0, alpha))


def _wrap_text(
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    draw: ImageDraw.ImageDraw,
) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _draw_text_with_shadow(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
    shadow: tuple[int, int, int] = (0, 0, 0),
) -> None:
    x, y = xy
    for dx, dy in ((2, 2), (1, 1), (-1, 1)):
        draw.text((x + dx, y + dy), text, font=font, fill=shadow)
    draw.text((x, y), text, font=font, fill=fill)


def _draw_title_block(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    title: str,
    subtitle: str,
    rank: int | None = None,
    *,
    margin: int = 80,
) -> None:
    title_font = _get_font(62, bold=True)
    sub_font = _get_font(32, bold=False)
    label_font = _get_font(28, bold=True)
    max_text_width = width - margin * 2

    # Rank pill
    label_y = int(height * 0.52)
    if rank is not None:
        label = f"TRENDING  #{rank}"
        _draw_text_with_shadow(
            draw, (margin, label_y), label, label_font, fill=(120, 190, 255)
        )
        title_y = label_y + 48
    else:
        title_y = label_y

    title_lines = _wrap_text(title, title_font, max_text_width, draw)[:3]
    line_h = 72
    for i, line in enumerate(title_lines):
        _draw_text_with_shadow(
            draw,
            (margin, title_y + i * line_h),
            line,
            title_font,
            fill=(255, 255, 255),
        )

    sub_y = title_y + len(title_lines) * line_h + 18
    if subtitle:
        news_label = "WHAT IT MEANS"
        _draw_text_with_shadow(
            draw, (margin, sub_y), news_label, label_font, fill=(255, 196, 120)
        )
        sub_lines = _wrap_text(subtitle, sub_font, max_text_width, draw)[:3]
        for i, line in enumerate(sub_lines):
            _draw_text_with_shadow(
                draw,
                (margin, sub_y + 40 + i * 40),
                line,
                sub_font,
                fill=(230, 235, 240),
            )

    draw.rectangle([(0, height - 10), (width, height)], fill=(100, 180, 255))


def _make_solid_slide(
    width: int,
    height: int,
    title: str,
    subtitle: str,
    rank: int | None = None,
) -> Image.Image:
    img = Image.new("RGB", (width, height), color=(14, 20, 34))
    # subtle top accent
    draw = ImageDraw.Draw(img, "RGBA")
    for y in range(180):
        alpha = int(40 * (1 - y / 180))
        draw.rectangle([(0, y), (width, y + 1)], fill=(40, 80, 140, alpha))
    draw_rgb = ImageDraw.Draw(img)
    _draw_title_block(draw_rgb, width, height, title, subtitle, rank)
    return img.convert("RGB")


def _make_image_slide(
    width: int,
    height: int,
    image_path: str,
    title: str,
    subtitle: str,
    rank: int | None = None,
) -> Image.Image:
    try:
        base = Image.open(image_path).convert("RGB")
        base = _cover_resize(base, width, height)
        base = ImageEnhance.Brightness(base).enhance(0.68)
        base = ImageEnhance.Contrast(base).enhance(1.05)
    except Exception as exc:
        logger.warning("Could not open image %s: %s", image_path, exc)
        return _make_solid_slide(width, height, title, subtitle, rank=rank)

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    _draw_gradient(draw, width, height)
    composed = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
    draw2 = ImageDraw.Draw(composed)
    _draw_title_block(draw2, width, height, title, subtitle, rank)
    return composed


def _nvenc_available() -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    try:
        enc = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if "h264_nvenc" not in (enc.stdout or ""):
            return False
        gpu = subprocess.run(
            ["nvidia-smi", "-L"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return gpu.returncode == 0 and bool(gpu.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def _resolve_encoder(video_cfg: dict[str, Any]) -> tuple[str, list[str]]:
    preferred = str(video_cfg.get("codec", "auto")).lower()
    if preferred in ("h264_nvenc", "nvenc", "auto") and _nvenc_available():
        logger.info("Using NVIDIA NVENC (GPU) for video encode")
        return "h264_nvenc", ["-preset", "p4", "-rc", "vbr", "-cq", "23", "-b:v", "0"]
    if preferred == "h264_nvenc":
        logger.warning("h264_nvenc requested but unavailable; falling back to libx264")
    logger.info("Using CPU libx264 for video encode")
    return "libx264", ["-preset", "veryfast", "-crf", "23"]


def render_video(
    country: Country,
    trends: list[str],
    news: dict[str, list[dict[str, Any]]],
    audio_path: str,
    output_dir: Path,
    run_date: str,
    images: dict[str, list[str]] | None = None,
    display_titles: dict[str, dict[str, str]] | None = None,
) -> str:
    config = load_pipeline_config()
    video_cfg = config.get("video", {})
    width = int(video_cfg.get("width", 1920))
    height = int(video_cfg.get("height", 1080))
    fps = int(video_cfg.get("fps", 24))
    max_duration = float(config.get("max_video_duration_sec", 570))
    title = build_video_title(country.name, run_date)
    codec, ffmpeg_params = _resolve_encoder(video_cfg)
    images = images or {}

    from moviepy import AudioFileClip, ImageClip, concatenate_videoclips

    slides_dir = output_dir / "slides"
    slides_dir.mkdir(exist_ok=True)

    intro = _make_solid_slide(
        width,
        height,
        title,
        f"Top {len(trends)} searches · {country.name}",
    )
    intro_path = slides_dir / "00_intro.png"
    intro.save(intro_path, optimize=True)

    slide_paths: list[Path] = [intro_path]
    for idx, keyword in enumerate(trends, start=1):
        display_title, subtitle = _card_for(keyword, news, display_titles)
        trend_images = images.get(keyword) or []
        safe_kw = keyword[:30].replace("/", "-").replace("\\", "-")

        if trend_images:
            for img_i, img_path in enumerate(trend_images, start=1):
                slide = _make_image_slide(
                    width, height, img_path, display_title, subtitle, rank=idx
                )
                slide_path = slides_dir / f"{idx:02d}_{safe_kw}_{img_i}.png"
                slide.save(slide_path, optimize=True)
                slide_paths.append(slide_path)
        else:
            slide = _make_solid_slide(
                width, height, display_title, subtitle, rank=idx
            )
            slide_path = slides_dir / f"{idx:02d}_{safe_kw}.png"
            slide.save(slide_path, optimize=True)
            slide_paths.append(slide_path)

    outro = _make_solid_slide(
        width,
        height,
        "Thanks for watching",
        "Subscribe for daily trends",
    )
    outro_path = slides_dir / "99_outro.png"
    outro.save(outro_path, optimize=True)
    slide_paths.append(outro_path)

    audio = AudioFileClip(audio_path)
    audio_duration = float(audio.duration)
    if audio_duration > max_duration:
        logger.warning(
            "Audio %.1fs exceeds cap %.1fs — trimming video to max duration",
            audio_duration,
            max_duration,
        )
        audio = audio.subclipped(0, max_duration)
        audio_duration = max_duration

    per_slide = max(audio_duration / len(slide_paths), 1.5)
    clips = [
        ImageClip(str(path)).with_duration(per_slide).with_fps(fps)
        for path in slide_paths
    ]
    video = concatenate_videoclips(clips, method="compose")
    video = video.with_audio(audio)

    if video.duration > audio_duration:
        video = video.subclipped(0, audio_duration)

    output_path = output_dir / video_filename(country.name, run_date)
    write_kwargs: dict[str, Any] = {
        "fps": fps,
        "codec": codec,
        "audio_codec": "aac",
        "logger": None,
        "ffmpeg_params": ffmpeg_params,
    }
    if codec == "libx264":
        write_kwargs["threads"] = 4

    video.write_videofile(str(output_path), **write_kwargs)
    logger.info("Wrote video (%s, %.1fs): %s", codec, audio_duration, output_path)

    video.close()
    audio.close()

    return str(output_path)
