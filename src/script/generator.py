"""Narration script generator — segmented Groq host or template fallback."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx

from src.config import Country, get_env, load_pipeline_config

logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
WORDS_PER_SEC = 2.0  # ~120 wpm at slower TTS rate

_URL_RE = re.compile(
    r"https?://\S+|www\.\S+",
    re.IGNORECASE,
)
_SOURCE_TAIL_RE = re.compile(
    r"\s*[\|\-–—]\s*[A-Za-z0-9][A-Za-z0-9 .&]{1,40}$",
)
_OPENERS = (
    "Up next,",
    "Also trending,",
    "People are looking this up —",
    "Here's another one.",
    "This one's climbing.",
    "Worth a mention:",
    "Next up,",
    "And this,",
)


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _clean_for_speech(text: str) -> str:
    """Strip HTML, URLs, and source crumbs for spoken narration."""
    cleaned = _strip_html(text)
    cleaned = _URL_RE.sub(" ", cleaned)
    cleaned = _SOURCE_TAIL_RE.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,;:-")
    return cleaned.strip()


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def _max_words() -> int:
    config = load_pipeline_config()
    max_sec = int(config.get("max_video_duration_sec", 570))
    return max(200, int(max_sec * WORDS_PER_SEC))


def _token_set(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"\b\w+\b", text) if len(w) > 2}


def _overlaps(a: str, b: str, threshold: float = 0.55) -> bool:
    ta, tb = _token_set(a), _token_set(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / min(len(ta), len(tb)) >= threshold


def _join_segments(segments: dict[str, Any]) -> str:
    parts = [segments.get("intro", "")]
    parts.extend(segments.get("trends") or [])
    parts.append(segments.get("outro", ""))
    return " ".join(p.strip() for p in parts if p and str(p).strip())


def _dedupe_segments(segments: dict[str, Any]) -> dict[str, Any]:
    intro = _clean_for_speech(str(segments.get("intro") or ""))
    outro = _clean_for_speech(str(segments.get("outro") or ""))
    keywords = list(segments.get("trend_keywords") or [])
    raw_beats = list(segments.get("trends") or [])
    cleaned_trends: list[str] = []
    kept_keywords: list[str] = []
    prev = intro
    for idx, raw in enumerate(raw_beats):
        beat = _clean_for_speech(str(raw))
        if not beat:
            continue
        if prev and _overlaps(beat, prev):
            continue
        if cleaned_trends and _overlaps(beat, cleaned_trends[-1]):
            continue
        cleaned_trends.append(beat)
        if idx < len(keywords):
            kept_keywords.append(str(keywords[idx]))
        prev = beat
    if outro and cleaned_trends and _overlaps(outro, cleaned_trends[-1]):
        outro = "Thanks for watching. Come back tomorrow for the next round of trends."
    # If keywords were missing, keep length aligned with beats for later fill
    if not kept_keywords and cleaned_trends:
        kept_keywords = keywords[: len(cleaned_trends)]
    return {
        "intro": intro,
        "trends": cleaned_trends,
        "outro": outro,
        "trend_keywords": kept_keywords,
    }


def _fact_for_trend(
    keyword: str,
    news: dict[str, list[dict[str, Any]]],
) -> str:
    headlines = news.get(keyword) or []
    if not headlines:
        return ""
    item = headlines[0]
    summary = _clean_for_speech(item.get("summary", ""))[:180]
    headline = _clean_for_speech(item.get("title", ""))
    if summary and not _overlaps(summary, keyword):
        return summary
    if headline and not _overlaps(headline, keyword):
        return headline
    return summary or headline


def _template_segments(
    country: Country,
    trends: list[str],
    news: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    config = load_pipeline_config()
    script_cfg = config.get("script", {})
    intro = _clean_for_speech(
        script_cfg.get(
            "intro_template",
            "Here are today's top trending searches in {country}.",
        ).format(country=country.name)
    )
    if intro and "let's" not in intro.lower():
        intro = f"{intro} We've got {len(trends)} stories — let's dig in."
    outro = _clean_for_speech(
        script_cfg.get(
            "outro_template",
            "Thanks for watching. Subscribe for daily trends updates.",
        )
    )

    beats: list[str] = []
    for idx, keyword in enumerate(trends):
        opener = _OPENERS[idx % len(_OPENERS)]
        fact = _fact_for_trend(keyword, news)
        if fact:
            beat = f"{opener} {fact}"
            if beat[-1] not in ".!?":
                beat += "."
        else:
            beat = (
                f"{opener} Search interest is spiking for this across "
                f"{country.name}."
            )
        beats.append(_clean_for_speech(beat))

    return _dedupe_segments(
        {
            "intro": intro,
            "trends": beats,
            "outro": outro,
            "trend_keywords": list(trends),
        }
    )


def _build_prompt(
    country: Country,
    trends: list[str],
    news: dict[str, list[dict[str, Any]]],
    max_words: int,
) -> str:
    style = load_pipeline_config().get("script", {}).get(
        "style",
        "warm, conversational spoken host; natural pacing; no invented facts",
    )
    lines = [
        f"Write segmented spoken narration for a daily Google Trends video about {country.name}.",
        f"Style: {style}",
        f"Hard limit: under {max_words} words across all segments.",
        "Only use the facts below. Do not invent news, scores, or quotes.",
        "Rules:",
        "- One unique spoken beat per trend — do not restate the search query.",
        "- Do not repeat the same fact across trends.",
        "- Never include URLs, links, website names as links, or markdown.",
        "- Do not say on-screen labels like 'what it means'.",
        "- Use contractions and varied transitions; sound human, not like a headline reader.",
        "- Short intro once, short outro once.",
        "",
        "Return ONLY valid JSON with this shape:",
        '{ "intro": "...", "trends": ["beat for trend 1", "..."], "outro": "..." }',
        f"The trends array MUST have exactly {len(trends)} strings, in the same order.",
        "",
        "Trends and one fact each:",
    ]
    for idx, keyword in enumerate(trends, start=1):
        fact = _fact_for_trend(keyword, news) or "Search interest is rising."
        lines.append(f"{idx}. keyword={keyword!r} fact={fact!r}")
    return "\n".join(lines)


def _call_groq(prompt: str, model: str, api_key: str) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "temperature": 0.65,
        "max_tokens": 2500,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a warm, conversational daily trends video host. "
                    "Stay factual. Never invent events. "
                    "Reply with JSON only — no markdown fences."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    }
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(GROQ_URL, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    text = data["choices"][0]["message"]["content"].strip()
    text = re.sub(r"^```(?:\w+)?\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _parse_groq_segments(
    raw: str,
    trends: list[str],
    country: Country,
) -> dict[str, Any] | None:
    try:
        # Extract JSON object if model added prose
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        payload = json.loads(match.group(0) if match else raw)
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None

    intro = _clean_for_speech(str(payload.get("intro") or ""))
    outro = _clean_for_speech(str(payload.get("outro") or ""))
    raw_trends = payload.get("trends")
    if not isinstance(raw_trends, list):
        return None

    beats = [_clean_for_speech(str(b)) for b in raw_trends if str(b).strip()]
    if len(beats) != len(trends):
        # Pad or trim to match trend count
        if len(beats) < len(trends):
            for keyword in trends[len(beats) :]:
                beats.append(
                    _clean_for_speech(
                        f"Search interest is climbing for this across {country.name}."
                    )
                )
        else:
            beats = beats[: len(trends)]

    if not intro:
        intro = f"Here are today's top trending searches in {country.name}."
    if not outro:
        outro = "Thanks for watching. Subscribe for daily trends updates."

    return _dedupe_segments(
        {
            "intro": intro,
            "trends": beats,
            "outro": outro,
            "trend_keywords": list(trends),
        }
    )


def _trim_segments_to_budget(
    segments: dict[str, Any],
    max_words: int,
) -> dict[str, Any]:
    script = _join_segments(segments)
    keywords = list(segments.get("trend_keywords") or [])
    beats = list(segments.get("trends") or [])
    while _word_count(script) > max_words and len(beats) > 8:
        beats = beats[:-1]
        if keywords:
            keywords = keywords[: len(beats)]
        segments = {
            "intro": segments["intro"],
            "trends": beats,
            "outro": segments["outro"],
            "trend_keywords": keywords,
        }
        script = _join_segments(segments)
    if _word_count(script) > max_words:
        # Hard-trim the last beat
        words_left = max_words - _word_count(
            _join_segments(
                {
                    "intro": segments["intro"],
                    "trends": beats[:-1] if beats else [],
                    "outro": segments["outro"],
                }
            )
        )
        if beats and words_left > 8:
            beats[-1] = " ".join(beats[-1].split()[:words_left])
        segments = {
            "intro": segments["intro"],
            "trends": beats,
            "outro": segments["outro"],
            "trend_keywords": keywords[: len(beats)] if keywords else keywords,
        }
    return segments


def generate_script(
    country: Country,
    trends: list[str],
    news: dict[str, list[dict[str, Any]]],
    output_dir: Path,
) -> str:
    config = load_pipeline_config()
    script_cfg = config.get("script", {})
    provider = str(script_cfg.get("provider", "groq")).lower()
    model = str(script_cfg.get("model", "llama-3.1-8b-instant"))
    api_key = get_env("GROQ_API_KEY")
    max_words = _max_words()

    used_provider = "template"
    active_trends = list(trends)
    segments: dict[str, Any] | None = None

    if provider == "groq" and api_key:
        try:
            prompt = _build_prompt(country, active_trends, news, max_words)
            raw = _call_groq(prompt, model, api_key)
            segments = _parse_groq_segments(raw, active_trends, country)
            if segments is None:
                raise ValueError("Could not parse Groq JSON segments")
            used_provider = "groq"
            if _word_count(_join_segments(segments)) > max_words:
                logger.warning(
                    "Script over budget (%s > %s); regenerating shorter",
                    _word_count(_join_segments(segments)),
                    max_words,
                )
                keep = max(8, len(active_trends) - 5)
                active_trends = active_trends[:keep]
                prompt = _build_prompt(
                    country,
                    active_trends,
                    news,
                    int(max_words * 0.85),
                )
                raw = _call_groq(prompt, model, api_key)
                segments = _parse_groq_segments(raw, active_trends, country)
                if segments is None:
                    raise ValueError("Could not parse shortened Groq segments")
            segments = _trim_segments_to_budget(segments, max_words)
        except Exception as exc:
            logger.warning("Groq narration failed, using template: %s", exc)
            segments = None

    if segments is None:
        used_provider = "template"
        segments = _template_segments(country, active_trends, news)
        segments = _trim_segments_to_budget(segments, max_words)

    # Keep keyword list aligned with spoken beats
    keywords = list(segments.get("trend_keywords") or active_trends)
    beats = list(segments.get("trends") or [])
    if len(keywords) != len(beats):
        keywords = keywords[: len(beats)]
        if len(keywords) < len(beats):
            keywords.extend(active_trends[len(keywords) : len(beats)])
    segments["trend_keywords"] = keywords

    script = _join_segments(segments)
    script_path = output_dir / "script.txt"
    script_path.write_text(script, encoding="utf-8")

    segments_path = output_dir / "script_segments.json"
    segments_path.write_text(
        json.dumps(
            {
                "intro": segments["intro"],
                "trends": segments["trends"],
                "outro": segments["outro"],
                "trend_keywords": segments["trend_keywords"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    meta = {
        "provider": used_provider,
        "model": model if used_provider == "groq" else None,
        "word_count": _word_count(script),
        "max_words": max_words,
        "trends_in_script": len(segments["trends"]),
        "segments_path": str(segments_path),
    }
    (output_dir / "script_meta.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )
    logger.info(
        "Script ready via %s (%s words, %s segments, budget %s)",
        used_provider,
        meta["word_count"],
        meta["trends_in_script"] + 2,
        max_words,
    )
    return str(script_path)
