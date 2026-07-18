"""Preference-driven scheduled reports (daily summary + weekly deck).

Schedules are stored per-chat in the `preferences` table (no extra tables):
  - daily_report_time   e.g. "21:00"          -> daily text summary at that IST time
  - weekly_report_day   e.g. "monday"          -> weekly deck day
  - weekly_report_time  e.g. "13:00"           -> weekly deck time
  - last_daily_sent     "YYYY-MM-DD" (internal, dedupe)
  - last_weekly_sent    "YYYY-MM-DD" (internal, dedupe)

If a chat has none of these keys, nothing fires — the owner just asks on demand.
The whole feature can be turned off with SCHEDULER_ENABLED=false.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

from app.config import get_settings
from app.db.base import SessionLocal
from app.services import analytics_service, preference_service
from app.agent.tools.registry import _generate_analysis_deck

logger = logging.getLogger(__name__)

_WEEKDAYS = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}


def normalize_time(raw: str | None) -> str | None:
    """Accept '21:00', '9pm', '9 pm', '1pm', '13:00' -> 'HH:MM' (24h). None if unparseable."""
    if not raw:
        return None
    s = raw.strip().lower().replace(".", "")
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", s)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    ampm = m.group(3)
    if ampm == "pm" and hour != 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def normalize_weekday(raw: str | None) -> str | None:
    if not raw:
        return None
    key = raw.strip().lower()
    if key in _WEEKDAYS:
        # canonical full name
        for name, num in _WEEKDAYS.items():
            if num == _WEEKDAYS[key] and len(name) > 3:
                return name
    return None


def _now_ist() -> datetime:
    settings = get_settings()
    if ZoneInfo is not None:
        return datetime.now(ZoneInfo(settings.report_timezone))
    return datetime.now()  # fallback: server local time


def _build_daily_text(chat_id: int) -> str:
    db = SessionLocal()
    try:
        s = analytics_service.today_sales_summary(db, chat_id)
    finally:
        db.close()
    lines = [
        f"Daily report — {s['date']}",
        f"Bills: {s['bills_count']}",
        f"Total sales: Rs {s['total_sales']:.2f}",
        f"Tax collected: Rs {s['tax_collected']:.2f}",
        f"Cash: Rs {s['cash_total']:.2f} | UPI: Rs {s['upi_total']:.2f} | "
        f"Card: Rs {s['card_total']:.2f} | Khata: Rs {s['khata_total']:.2f}",
    ]
    if s.get("top_items"):
        lines.append("Top items:")
        for it in s["top_items"]:
            lines.append(f"  - {it['sku_name']}: {it['qty']:g} (Rs {it['amount']:.2f})")
    else:
        lines.append("No sales recorded today.")
    return "\n".join(lines)


def _build_weekly_deck(chat_id: int) -> dict[str, Any]:
    db = SessionLocal()
    try:
        return _generate_analysis_deck(db, chat_id, None, None)
    finally:
        db.close()


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _load_schedules() -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        return preference_service.get_report_schedules(db)
    finally:
        db.close()


def _mark_sent(chat_id: int, key: str, value: str) -> None:
    db = SessionLocal()
    try:
        preference_service.set_preference(db, chat_id, key, value)
    finally:
        db.close()


async def _maybe_send_daily(context, sched: dict[str, Any], hhmm: str, today: str) -> None:
    dtime = normalize_time(sched.get("daily_report_time"))
    if not dtime or dtime != hhmm or sched.get("last_daily_sent") == today:
        return
    chat_id = sched["chat_id"]
    try:
        text = await asyncio.to_thread(_build_daily_text, chat_id)
        await context.bot.send_message(chat_id=chat_id, text=text)
        await asyncio.to_thread(_mark_sent, chat_id, "last_daily_sent", today)
        logger.info("scheduled_daily_sent chat_id=%s time=%s", chat_id, hhmm)
    except Exception:
        logger.exception("scheduled_daily_failed chat_id=%s", chat_id)


async def _maybe_send_weekly(
    context, sched: dict[str, Any], hhmm: str, weekday: str, today: str
) -> None:
    wday = normalize_weekday(sched.get("weekly_report_day"))
    wtime = normalize_time(sched.get("weekly_report_time"))
    if not (wday and wtime) or wday != weekday or wtime != hhmm:
        return
    if sched.get("last_weekly_sent") == today:
        return
    chat_id = sched["chat_id"]
    try:
        result = await asyncio.to_thread(_build_weekly_deck, chat_id)
        path = result.get("file_path")
        if path:
            data = await asyncio.to_thread(_read_bytes, path)
            await context.bot.send_document(
                chat_id=chat_id,
                document=data,
                filename=os.path.basename(path),
                caption="Weekly sales analysis deck",
            )
        await asyncio.to_thread(_mark_sent, chat_id, "last_weekly_sent", today)
        logger.info("scheduled_weekly_sent chat_id=%s time=%s", chat_id, hhmm)
    except Exception:
        logger.exception("scheduled_weekly_failed chat_id=%s", chat_id)


async def _tick(context) -> None:  # context: telegram.ext.CallbackContext
    settings = get_settings()
    if not settings.scheduler_enabled:
        return
    try:
        now = _now_ist()
        hhmm = now.strftime("%H:%M")
        today = now.date().isoformat()
        weekday = now.strftime("%A").lower()

        schedules = await asyncio.to_thread(_load_schedules)
        for sched in schedules:
            await _maybe_send_daily(context, sched, hhmm, today)
            await _maybe_send_weekly(context, sched, hhmm, weekday, today)
    except Exception:
        logger.exception("scheduler_tick_error")


def register_scheduler(application) -> bool:
    """Attach the repeating report checker to PTB's job queue. Returns True if scheduled."""
    settings = get_settings()
    if not settings.scheduler_enabled:
        logger.info("scheduler_disabled")
        return False
    jq = getattr(application, "job_queue", None)
    if jq is None:
        logger.warning("scheduler_no_job_queue — install python-telegram-bot[job-queue]")
        return False
    # every 30s so we reliably catch the minute a schedule fires; dedupe by date
    jq.run_repeating(_tick, interval=30, first=15, name="report_scheduler")
    logger.info("scheduler_registered tz=%s", settings.report_timezone)
    return True
