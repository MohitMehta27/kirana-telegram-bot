"""FastAPI entrypoint + Telegram polling/webhook lifecycle."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response

from app.config import get_settings
from app.logging_setup import setup_logging

logger = logging.getLogger(__name__)

telegram_app = None  # set in lifespan


def _register_reports(app) -> None:
    """Attach the preference-driven report scheduler if enabled."""
    try:
        from app.telegram.scheduler import register_scheduler

        register_scheduler(app)
    except Exception:
        logger.exception("scheduler_register_failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global telegram_app
    settings = get_settings()
    setup_logging(log_dir=settings.log_dir, retention_days=settings.log_retention_days)
    Path(settings.generated_dir).mkdir(parents=True, exist_ok=True)

    logger.info(
        "startup env=%s db=%s@%s:%s model=%s telegram_mode=%s",
        settings.app_env,
        settings.db_name,
        settings.db_host,
        settings.db_port,
        settings.gemini_model,
        settings.telegram_mode,
    )

    from app.telegram.bot import build_application
    import asyncio

    telegram_app = build_application()

    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            logger.info("telegram_initialize attempt=%s", attempt)
            await telegram_app.initialize()
            me = await telegram_app.bot.get_me()
            logger.info("telegram_bot_ok @%s id=%s", me.username, me.id)
            last_err = None
            break
        except Exception as e:
            last_err = e
            logger.warning("telegram_initialize_failed attempt=%s err=%s", attempt, e)
            await asyncio.sleep(2 * attempt)
    if last_err:
        logger.error(
            "telegram_unreachable — health API will still run; fix network to api.telegram.org: %s",
            last_err,
        )
        telegram_app = None
    elif settings.telegram_mode == "polling":
        await telegram_app.start()
        await telegram_app.updater.start_polling(drop_pending_updates=True)
        logger.info("telegram_polling_started")
        _register_reports(telegram_app)
    else:
        if not settings.public_base_url:
            logger.error("webhook mode needs PUBLIC_BASE_URL")
        else:
            await telegram_app.start()
            webhook_url = settings.public_base_url.rstrip("/") + "/telegram/webhook"
            await telegram_app.bot.set_webhook(url=webhook_url)
            logger.info("telegram_webhook_set url=%s", webhook_url)
            _register_reports(telegram_app)

    yield

    logger.info("shutdown_begin")
    if telegram_app:
        try:
            if settings.telegram_mode == "polling" and telegram_app.updater:
                await telegram_app.updater.stop()
            await telegram_app.stop()
            await telegram_app.shutdown()
        except Exception:
            logger.exception("telegram_shutdown_error")
    logger.info("shutdown_done")


app = FastAPI(title="Supermarket Ops Agent", version="0.2.0", lifespan=lifespan)


@app.get("/health")
def health():
    settings = get_settings()
    return {
        "status": "ok",
        "env": settings.app_env,
        "db": settings.db_name,
        "model": settings.gemini_model,
        "telegram_mode": settings.telegram_mode,
    }


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    """Used when TELEGRAM_MODE=webhook."""
    global telegram_app
    if telegram_app is None:
        return Response(status_code=503)
    data = await request.json()
    from telegram import Update

    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}
