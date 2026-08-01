"""Text-to-speech via edge-tts."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from src.config import Country, load_pipeline_config

logger = logging.getLogger(__name__)


def _resolve_voice(country: Country) -> str:
    config = load_pipeline_config()
    voice_map = config.get("tts", {}).get("voice_map", {})
    return voice_map.get(country.language, "en-US-JennyNeural")


async def _generate_async(text: str, voice: str, output_path: Path) -> None:
    import edge_tts

    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(str(output_path))


def generate_narration(
    script_path: Path,
    country: Country,
    output_dir: Path,
) -> str:
    text = script_path.read_text(encoding="utf-8")
    voice = _resolve_voice(country)
    audio_path = output_dir / "narration.mp3"

    try:
        asyncio.run(_generate_async(text, voice, audio_path))
    except Exception as exc:
        logger.error("TTS failed: %s", exc)
        raise RuntimeError("Text-to-speech generation failed") from exc

    if not audio_path.exists():
        raise RuntimeError("TTS output file was not created")

    return str(audio_path)
