"""Speech-to-text for Telegram voice notes (Gemini audio, OpenAI optional)."""

from __future__ import annotations

import logging
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)


def transcribe(file_path: str, mime_type: str = "audio/ogg") -> str:
    settings = get_settings()
    provider = (settings.stt_provider or "gemini").lower()
    logger.info("stt_transcribe provider=%s file=%s mime=%s", provider, file_path, mime_type)

    if provider == "openai":
        return _transcribe_openai(file_path)
    if provider == "none":
        return ""
    return _transcribe_gemini(file_path, mime_type)


def _transcribe_gemini(file_path: str, mime_type: str) -> str:
    from google import genai
    from google.genai import types

    settings = get_settings()
    if not settings.gemini_api_key:
        logger.error("stt gemini missing GEMINI_API_KEY")
        return ""

    client = genai.Client(api_key=settings.gemini_api_key)
    audio_bytes = Path(file_path).read_bytes()

    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=[
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(
                        text=(
                            "Transcribe this shopkeeper voice note to text. "
                            "It may be Hindi/Hinglish/English about groceries, stock, or billing. "
                            "Return only the transcript, no commentary."
                        )
                    ),
                    types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
                ],
            )
        ],
    )
    text = (response.text or "").strip()
    logger.info("stt_gemini_done chars=%s", len(text))
    return text


def _transcribe_openai(file_path: str) -> str:
    try:
        from openai import OpenAI
    except Exception:
        logger.error("openai package not installed; set STT_PROVIDER=gemini")
        return ""
    settings = get_settings()
    if not settings.openai_api_key:
        logger.error("stt openai missing OPENAI_API_KEY")
        return ""
    client = OpenAI(api_key=settings.openai_api_key)
    with open(file_path, "rb") as f:
        result = client.audio.transcriptions.create(model="whisper-1", file=f)
    return (result.text or "").strip()
