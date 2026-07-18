"""Telegram bot application (polling for local; webhook later)."""

from __future__ import annotations

import logging

from telegram.ext import Application, CommandHandler, MessageHandler, filters
from telegram.request import HTTPXRequest

from app.config import get_settings
from app.telegram.handlers import (
    handle_document,
    handle_new,
    handle_text,
    handle_voice,
)

logger = logging.getLogger(__name__)


def build_application() -> Application:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing in .env")

    # Longer timeouts — api.telegram.org can be slow on some networks
    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
    )
    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .request(request)
        .build()
    )
    app.add_handler(CommandHandler("start", handle_new))
    app.add_handler(CommandHandler("new", handle_new))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("telegram_application_built mode=%s", settings.telegram_mode)
    return app
