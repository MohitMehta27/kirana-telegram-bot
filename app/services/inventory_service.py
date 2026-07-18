"""Inventory service — stock reads/writes with guards."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Product, StockLedger

logger = logging.getLogger(__name__)


def list_all(db: Session) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(Product).where(Product.is_active.is_(True)).order_by(Product.sku_name)
    ).all()
    logger.info("list_all count=%d", len(rows))
    return [_product_dict(p) for p in rows]


def find_products(db: Session, query: str, limit: int = 20) -> list[dict[str, Any]]:
    q = (query or "").strip()
    # Treat empty or wildcard-style queries as "list everything"
    if not q or q in ("*", "%", "all", "all items", "everything"):
        return list_all(db)[:limit]
    # MySQL-friendly case-insensitive LIKE; model disambiguates
    rows = db.scalars(
        select(Product)
        .where(
            Product.is_active.is_(True),
            func.lower(Product.sku_name).like(f"%{q.lower()}%"),
        )
        .limit(limit)
    ).all()
    if not rows:
        token = q.split()[0]
        rows = db.scalars(
            select(Product)
            .where(
                Product.is_active.is_(True),
                func.lower(Product.sku_name).like(f"%{token.lower()}%"),
            )
            .limit(limit)
        ).all()
    result = [_product_dict(p) for p in rows]
    logger.info("find_products query=%r matches=%d", q, len(result))
    return result


def get_product(db: Session, product_id: int) -> dict[str, Any] | None:
    p = db.get(Product, product_id)
    return _product_dict(p) if p else None


def check_stock(db: Session, product_id: int | None = None, query: str | None = None) -> dict[str, Any]:
    if product_id:
        p = db.get(Product, product_id)
        if not p:
            return {"ok": False, "error": f"product_id {product_id} not found"}
        return {"ok": True, "product": _product_dict(p)}
    matches = find_products(db, query or "")
    if not matches:
        return {"ok": False, "error": f"No product matched {query!r}"}
    if len(matches) > 1:
        return {"ok": True, "ambiguous": True, "candidates": matches}
    return {"ok": True, "product": matches[0]}


def low_stock_report(db: Session) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(Product).where(
            Product.is_active.is_(True),
            Product.quantity_on_hand <= Product.reorder_level,
        )
    ).all()
    logger.info("low_stock_report count=%d", len(rows))
    return [_product_dict(p) for p in rows]


def create_product(
    db: Session,
    *,
    sku_name: str,
    unit: str,
    is_loose: bool,
    hsn_code: str,
    gst_rate: Decimal,
    cost_price: Decimal,
    sell_price: Decimal,
    reorder_level: Decimal = Decimal("0"),
    opening_qty: Decimal = Decimal("0"),
) -> dict[str, Any]:
    existing = db.scalar(select(Product).where(Product.sku_name == sku_name))
    if existing:
        return {"ok": False, "error": f"Product already exists: {sku_name}", "product": _product_dict(existing)}

    p = Product(
        sku_name=sku_name,
        unit=unit,
        is_loose=is_loose,
        hsn_code=hsn_code,
        gst_rate=gst_rate,
        cost_price=cost_price,
        sell_price=sell_price,
        quantity_on_hand=opening_qty,
        reorder_level=reorder_level,
    )
    db.add(p)
    db.flush()
    if opening_qty > 0:
        db.add(
            StockLedger(
                product_id=p.id,
                change_qty=opening_qty,
                reason="receive",
                ref_type="manual",
                note="Opening stock on create",
            )
        )
    db.commit()
    db.refresh(p)
    logger.info("create_product id=%s sku=%s", p.id, p.sku_name)
    return {"ok": True, "product": _product_dict(p)}


def receive_stock(
    db: Session,
    *,
    product_id: int,
    qty: Decimal,
    cost_price: Decimal | None = None,
    mrp: Decimal | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    if qty <= 0:
        return {"ok": False, "error": "qty must be positive"}

    # Row lock for concurrency
    p = db.execute(
        select(Product).where(Product.id == product_id).with_for_update()
    ).scalar_one_or_none()
    if not p:
        return {"ok": False, "error": f"product_id {product_id} not found"}

    p.quantity_on_hand = Decimal(p.quantity_on_hand) + Decimal(qty)
    p.version = int(p.version) + 1
    if cost_price is not None:
        p.cost_price = cost_price
    if mrp is not None:
        p.sell_price = mrp

    db.add(
        StockLedger(
            product_id=p.id,
            change_qty=qty,
            reason="receive",
            ref_type="manual",
            note=note or "Stock receive",
        )
    )
    db.commit()
    db.refresh(p)
    logger.info("receive_stock product_id=%s qty=%s new_qty=%s", p.id, qty, p.quantity_on_hand)
    return {"ok": True, "product": _product_dict(p)}


def _product_dict(p: Product) -> dict[str, Any]:
    return {
        "id": p.id,
        "sku_name": p.sku_name,
        "unit": p.unit,
        "is_loose": bool(p.is_loose),
        "hsn_code": p.hsn_code,
        "gst_rate": float(p.gst_rate),
        "cost_price": float(p.cost_price),
        "sell_price": float(p.sell_price),
        "quantity_on_hand": float(p.quantity_on_hand),
        "reorder_level": float(p.reorder_level),
        "low_stock": float(p.quantity_on_hand) <= float(p.reorder_level),
    }
