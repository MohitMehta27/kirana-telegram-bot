"""Durable preferences (survive /new)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Preference

logger = logging.getLogger(__name__)


def get_preferences(db: Session, chat_id: int) -> dict[str, str]:
    rows = db.scalars(select(Preference).where(Preference.chat_id == chat_id)).all()
    prefs = {r.pref_key: r.pref_value for r in rows}
    if not prefs:
        # Fall back to global template chat_id=0 from seed
        rows = db.scalars(select(Preference).where(Preference.chat_id == 0)).all()
        prefs = {r.pref_key: r.pref_value for r in rows}
    return prefs


def set_preference(db: Session, chat_id: int, key: str, value: str) -> dict[str, Any]:
    key = key.strip()
    value = value.strip()
    row = db.scalar(
        select(Preference).where(Preference.chat_id == chat_id, Preference.pref_key == key)
    )
    if row:
        row.pref_value = value
    else:
        db.add(Preference(chat_id=chat_id, pref_key=key, pref_value=value))
    db.commit()
    logger.info("set_preference chat_id=%s key=%s", chat_id, key)
    return {"ok": True, "key": key, "value": value, "preferences": get_preferences(db, chat_id)}


def clear_preference(db: Session, chat_id: int, key: str) -> dict[str, Any]:
    """Delete a single preference key for this chat (used to stop reports)."""
    key = key.strip()
    row = db.scalar(
        select(Preference).where(Preference.chat_id == chat_id, Preference.pref_key == key)
    )
    if row:
        db.delete(row)
        db.commit()
        logger.info("clear_preference chat_id=%s key=%s", chat_id, key)
        return {"ok": True, "cleared": key}
    return {"ok": True, "cleared": None, "note": "nothing was set"}


def get_report_schedules(db: Session) -> list[dict[str, Any]]:
    """Return per-chat report schedule state for the scheduler.

    Reads real chat rows only (skips the chat_id=0 seed template) so the
    scheduler never fires for the global default.
    """
    keys = (
        "daily_report_time",
        "weekly_report_day",
        "weekly_report_time",
        "last_daily_sent",
        "last_weekly_sent",
    )
    rows = db.scalars(
        select(Preference).where(
            Preference.chat_id != 0, Preference.pref_key.in_(keys)
        )
    ).all()
    by_chat: dict[int, dict[str, str]] = {}
    for r in rows:
        by_chat.setdefault(r.chat_id, {})[r.pref_key] = r.pref_value
    return [{"chat_id": cid, **prefs} for cid, prefs in by_chat.items()]
