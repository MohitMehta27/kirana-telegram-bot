"""Analytics: today's sales, day close, ranged sales for the deck."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Bill, BillItem, DailyClose, Product

logger = logging.getLogger(__name__)


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min)
    end = datetime.combine(day, time.max)
    return start, end


def _summarize(db: Session, chat_id: int, start: datetime, end: datetime) -> dict[str, Any]:
    bills = db.scalars(
        select(Bill).where(
            Bill.chat_id == chat_id,
            Bill.status == "finalized",
            Bill.finalized_at >= start,
            Bill.finalized_at <= end,
        )
    ).all()

    total_sales = Decimal("0")
    tax = Decimal("0")
    by_mode: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    bill_ids = [b.id for b in bills]

    for b in bills:
        total_sales += Decimal(b.grand_total)
        tax += Decimal(b.cgst_total) + Decimal(b.sgst_total)
        by_mode[b.payment_mode or "unknown"] += Decimal(b.grand_total)

    top_items: list[dict[str, Any]] = []
    if bill_ids:
        rows = db.execute(
            select(
                Product.sku_name,
                func.sum(BillItem.qty).label("qty"),
                func.sum(BillItem.line_total).label("amount"),
            )
            .join(Product, Product.id == BillItem.product_id)
            .where(BillItem.bill_id.in_(bill_ids))
            .group_by(Product.sku_name)
            .order_by(func.sum(BillItem.line_total).desc())
            .limit(5)
        ).all()
        top_items = [
            {"sku_name": r[0], "qty": float(r[1] or 0), "amount": float(r[2] or 0)}
            for r in rows
        ]

    return {
        "bills_count": len(bills),
        "total_sales": float(total_sales),
        "tax_collected": float(tax),
        "cash_total": float(by_mode.get("cash", Decimal("0"))),
        "upi_total": float(by_mode.get("upi", Decimal("0"))),
        "card_total": float(by_mode.get("card", Decimal("0"))),
        "khata_total": float(by_mode.get("khata", Decimal("0"))),
        "top_items": top_items,
    }


def today_sales_summary(db: Session, chat_id: int) -> dict[str, Any]:
    start, end = _day_bounds(date.today())
    summary = _summarize(db, chat_id, start, end)
    logger.info("today_sales chat_id=%s total=%s bills=%s", chat_id, summary["total_sales"], summary["bills_count"])
    return {"ok": True, "date": date.today().isoformat(), **summary}


def close_day(db: Session, chat_id: int, day: date | None = None) -> dict[str, Any]:
    day = day or date.today()
    existing = db.scalar(
        select(DailyClose).where(DailyClose.close_date == day, DailyClose.chat_id == chat_id)
    )
    if existing:
        logger.info("close_day already_closed date=%s chat_id=%s", day, chat_id)
        return {
            "ok": True,
            "already_closed": True,
            "date": day.isoformat(),
            "total_sales": float(existing.total_sales),
            "tax_collected": float(existing.tax_collected),
            "cash_total": float(existing.cash_total),
            "upi_total": float(existing.upi_total),
            "card_total": float(existing.card_total),
            "khata_total": float(existing.khata_total),
            "top_items": existing.top_items or [],
        }

    start, end = _day_bounds(day)
    summary = _summarize(db, chat_id, start, end)
    row = DailyClose(
        close_date=day,
        chat_id=chat_id,
        total_sales=Decimal(str(summary["total_sales"])),
        tax_collected=Decimal(str(summary["tax_collected"])),
        cash_total=Decimal(str(summary["cash_total"])),
        upi_total=Decimal(str(summary["upi_total"])),
        card_total=Decimal(str(summary["card_total"])),
        khata_total=Decimal(str(summary["khata_total"])),
        top_items=summary["top_items"],
    )
    db.add(row)
    db.commit()
    logger.info("close_day done date=%s chat_id=%s total=%s", day, chat_id, summary["total_sales"])
    return {"ok": True, "already_closed": False, "date": day.isoformat(), **summary}


def sales_for_range(db: Session, chat_id: int, start_date: date, end_date: date) -> dict[str, Any]:
    start = datetime.combine(start_date, time.min)
    end = datetime.combine(end_date, time.max)
    summary = _summarize(db, chat_id, start, end)

    # Per-day trend
    daily: list[dict[str, Any]] = []
    cur = start_date
    while cur <= end_date:
        d_start, d_end = _day_bounds(cur)
        d = _summarize(db, chat_id, d_start, d_end)
        daily.append({"date": cur.isoformat(), "total_sales": d["total_sales"], "tax": d["tax_collected"]})
        cur += timedelta(days=1)

    return {
        "ok": True,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "daily": daily,
        **summary,
    }
