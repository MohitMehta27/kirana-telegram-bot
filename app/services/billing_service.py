"""Billing service — draft bills, GST math, finalize with oversell + idempotency."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Bill, BillItem, Customer, KhataLedger, Product, StockLedger

logger = logging.getLogger(__name__)

TWOPLACES = Decimal("0.01")


def _money(x: Decimal | float | int | str) -> Decimal:
    return Decimal(str(x)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def _line_tax(taxable: Decimal, gst_rate: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """Return (cgst, sgst, line_total) for intra-state sale."""
    rate = Decimal(str(gst_rate))
    half = (rate / Decimal("2")) / Decimal("100")
    cgst = _money(taxable * half)
    sgst = _money(taxable * half)
    line_total = _money(taxable + cgst + sgst)
    return cgst, sgst, line_total


def get_or_create_draft(db: Session, chat_id: int) -> Bill:
    bill = db.scalar(
        select(Bill)
        .options(selectinload(Bill.items))
        .where(Bill.chat_id == chat_id, Bill.status == "draft")
        .order_by(Bill.id.desc())
    )
    if bill:
        return bill
    bill = Bill(chat_id=chat_id, status="draft")
    db.add(bill)
    db.commit()
    db.refresh(bill)
    logger.info("start_bill chat_id=%s bill_id=%s", chat_id, bill.id)
    return bill


def add_bill_item(db: Session, bill_id: int, product_id: int, qty: Decimal) -> dict[str, Any]:
    if qty <= 0:
        return {"ok": False, "error": "qty must be positive"}

    bill = db.get(Bill, bill_id)
    if not bill or bill.status != "draft":
        return {"ok": False, "error": "Draft bill not found"}

    product = db.get(Product, product_id)
    if not product or not product.is_active:
        return {"ok": False, "error": "Product not found"}

    # Soft check only — hard guard is on finalize
    if Decimal(product.quantity_on_hand) < qty:
        return {
            "ok": False,
            "error": f"Only {product.quantity_on_hand} {product.unit} of {product.sku_name} in stock",
            "available": float(product.quantity_on_hand),
        }

    if Decimal(product.sell_price) < Decimal(product.cost_price):
        return {"ok": False, "error": "Sell price is below cost — fix product pricing first"}

    existing = db.scalar(
        select(BillItem).where(BillItem.bill_id == bill_id, BillItem.product_id == product_id)
    )
    unit_price = Decimal(product.sell_price)
    gst_rate = Decimal(product.gst_rate)

    if existing:
        existing.qty = Decimal(existing.qty) + qty
        _recompute_line(existing, unit_price, gst_rate)
    else:
        item = BillItem(
            bill_id=bill_id,
            product_id=product_id,
            qty=qty,
            unit_price=unit_price,
            gst_rate=gst_rate,
        )
        _recompute_line(item, unit_price, gst_rate)
        db.add(item)

    _recompute_bill_totals(db, bill)
    db.commit()
    logger.info("add_bill_item bill_id=%s product_id=%s qty=%s", bill_id, product_id, qty)
    return get_bill_summary(db, bill_id)


def remove_bill_item(db: Session, bill_id: int, product_id: int) -> dict[str, Any]:
    bill = db.get(Bill, bill_id)
    if not bill or bill.status != "draft":
        return {"ok": False, "error": "Draft bill not found"}
    item = db.scalar(
        select(BillItem).where(BillItem.bill_id == bill_id, BillItem.product_id == product_id)
    )
    if not item:
        return {"ok": False, "error": "Item not on this bill"}
    db.delete(item)
    _recompute_bill_totals(db, bill)
    db.commit()
    logger.info("remove_bill_item bill_id=%s product_id=%s", bill_id, product_id)
    return get_bill_summary(db, bill_id)


def update_bill_item_qty(db: Session, bill_id: int, product_id: int, qty: Decimal) -> dict[str, Any]:
    if qty <= 0:
        return remove_bill_item(db, bill_id, product_id)
    bill = db.get(Bill, bill_id)
    if not bill or bill.status != "draft":
        return {"ok": False, "error": "Draft bill not found"}
    item = db.scalar(
        select(BillItem).where(BillItem.bill_id == bill_id, BillItem.product_id == product_id)
    )
    if not item:
        return {"ok": False, "error": "Item not on this bill"}
    product = db.get(Product, product_id)
    if product and Decimal(product.quantity_on_hand) < qty:
        return {
            "ok": False,
            "error": f"Only {product.quantity_on_hand} of {product.sku_name} in stock",
        }
    item.qty = qty
    _recompute_line(item, Decimal(item.unit_price), Decimal(item.gst_rate))
    _recompute_bill_totals(db, bill)
    db.commit()
    return get_bill_summary(db, bill_id)


def set_bill_payment(
    db: Session,
    bill_id: int,
    mode: str,
    ref: str | None = None,
    customer_id: int | None = None,
) -> dict[str, Any]:
    bill = db.get(Bill, bill_id)
    if not bill or bill.status != "draft":
        return {"ok": False, "error": "Draft bill not found"}
    mode = mode.lower()
    if mode not in ("cash", "upi", "card", "khata"):
        return {"ok": False, "error": "payment mode must be cash|upi|card|khata"}
    bill.payment_mode = mode
    bill.payment_ref = ref
    bill.customer_id = customer_id if mode == "khata" else None
    db.commit()
    return get_bill_summary(db, bill_id)


def get_bill_summary(db: Session, bill_id: int) -> dict[str, Any]:
    bill = db.scalar(
        select(Bill).options(selectinload(Bill.items)).where(Bill.id == bill_id)
    )
    if not bill:
        return {"ok": False, "error": "Bill not found"}
    customer_name = None
    customer_phone = None
    if bill.customer_id:
        cust = db.get(Customer, bill.customer_id)
        if cust:
            customer_name = cust.name
            customer_phone = cust.phone
    lines = []
    text_lines = []
    for it in bill.items:
        p = db.get(Product, it.product_id)
        name = p.sku_name if p else str(it.product_id)
        unit = p.unit if p else ""
        lines.append(
            {
                "product_id": it.product_id,
                "sku_name": name,
                "hsn_code": p.hsn_code if p else "",
                "qty": float(it.qty),
                "unit": unit,
                "unit_price": float(it.unit_price),
                "gst_rate": float(it.gst_rate),
                "cgst": float(it.cgst_amount),
                "sgst": float(it.sgst_amount),
                "line_total": float(it.line_total),
            }
        )
        qty_str = f"{Decimal(it.qty).normalize():f}"
        text_lines.append(f"- {name} x{qty_str}{(' ' + unit) if unit else ''} = ₹{_money(it.line_total)}")

    # Deterministic summary the agent can echo VERBATIM (LLMs mis-transcribe numbers).
    header = f"Bill #{bill.id} ({bill.status})"
    body = "\n".join(text_lines) if text_lines else "(no items)"
    summary_text = (
        f"{header}\n{body}\n"
        f"Subtotal ₹{_money(bill.subtotal)} | CGST ₹{_money(bill.cgst_total)} | "
        f"SGST ₹{_money(bill.sgst_total)}\n"
        f"Total ₹{_money(bill.grand_total)}"
    )

    return {
        "ok": True,
        "bill_id": bill.id,
        "status": bill.status,
        "payment_mode": bill.payment_mode,
        "payment_ref": bill.payment_ref,
        "customer_id": bill.customer_id,
        "customer_name": customer_name,
        "customer_phone": customer_phone,
        "invoice_number": bill.invoice_number,
        "items": lines,
        "subtotal": float(bill.subtotal),
        "cgst_total": float(bill.cgst_total),
        "sgst_total": float(bill.sgst_total),
        "grand_total": float(bill.grand_total),
        "summary_text": summary_text,
    }


def make_idempotency_key(bill_id: int, items: list[BillItem]) -> str:
    raw = f"{bill_id}|" + "|".join(
        f"{it.product_id}:{it.qty}:{it.unit_price}" for it in sorted(items, key=lambda x: x.product_id)
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:48]


def finalize_bill(db: Session, bill_id: int, idempotency_key: str | None = None) -> dict[str, Any]:
    bill = db.scalar(
        select(Bill).options(selectinload(Bill.items)).where(Bill.id == bill_id).with_for_update()
    )
    if not bill:
        return {"ok": False, "error": "Bill not found"}

    if bill.status == "finalized":
        logger.info("finalize_bill idempotent hit bill_id=%s", bill_id)
        return {**get_bill_summary(db, bill_id), "idempotent": True}

    if bill.status != "draft":
        return {"ok": False, "error": f"Bill status is {bill.status}"}

    if not bill.items:
        return {"ok": False, "error": "Bill has no items"}

    if not bill.payment_mode:
        return {"ok": False, "error": "Set payment mode before finalize (cash/upi/card/khata)"}

    if bill.payment_mode == "khata":
        if not bill.customer_id:
            return {
                "ok": False,
                "error": "Khata (credit) sale needs a customer. Ask the owner for the customer's name and mobile number first.",
            }
        cust = db.get(Customer, bill.customer_id)
        if not cust or not (cust.phone and cust.phone.strip()):
            return {
                "ok": False,
                "error": (
                    "Credit sale requires the customer's mobile number so the due can be tracked. "
                    "Ask the owner for the customer's phone, save it, then finalize."
                ),
            }

    key = idempotency_key or make_idempotency_key(bill_id, list(bill.items))
    existing = db.scalar(select(Bill).where(Bill.idempotency_key == key))
    if existing and existing.id != bill.id:
        return {**get_bill_summary(db, existing.id), "idempotent": True}

    # Re-check stock under row locks and decrement
    for it in bill.items:
        product = db.execute(
            select(Product).where(Product.id == it.product_id).with_for_update()
        ).scalar_one()
        if Decimal(product.quantity_on_hand) < Decimal(it.qty):
            db.rollback()
            return {
                "ok": False,
                "error": f"Oversell blocked: only {product.quantity_on_hand} of {product.sku_name} left",
            }
        if Decimal(it.unit_price) < Decimal(product.cost_price):
            db.rollback()
            return {"ok": False, "error": f"Refusing sale below cost for {product.sku_name}"}

        product.quantity_on_hand = Decimal(product.quantity_on_hand) - Decimal(it.qty)
        product.version = int(product.version) + 1
        db.add(
            StockLedger(
                product_id=product.id,
                change_qty=-Decimal(it.qty),
                reason="sale",
                ref_type="bill",
                ref_id=bill.id,
                note="Sale finalize",
            )
        )

    _recompute_bill_totals(db, bill)
    bill.status = "finalized"
    bill.idempotency_key = key
    bill.finalized_at = datetime.now(timezone.utc).replace(tzinfo=None)
    bill.invoice_number = f"INV-{bill.id:06d}"

    if bill.payment_mode == "khata" and bill.customer_id:
        customer = db.execute(
            select(Customer).where(Customer.id == bill.customer_id).with_for_update()
        ).scalar_one()
        amt = Decimal(bill.grand_total)
        customer.khata_balance = Decimal(customer.khata_balance) + amt
        db.add(
            KhataLedger(
                customer_id=customer.id,
                amount=amt,
                note=f"Bill {bill.invoice_number}",
                ref_type="bill",
                ref_id=bill.id,
            )
        )

    db.commit()
    logger.info(
        "finalize_bill bill_id=%s invoice=%s total=%s",
        bill.id,
        bill.invoice_number,
        bill.grand_total,
    )
    return get_bill_summary(db, bill_id)


def cancel_draft(db: Session, chat_id: int) -> dict[str, Any]:
    bills = db.scalars(
        select(Bill).where(Bill.chat_id == chat_id, Bill.status == "draft")
    ).all()
    for b in bills:
        b.status = "cancelled"
    db.commit()
    logger.info("cancel_draft chat_id=%s count=%d", chat_id, len(bills))
    return {"ok": True, "cancelled": len(bills)}


def _recompute_line(item: BillItem, unit_price: Decimal, gst_rate: Decimal) -> None:
    taxable = _money(Decimal(item.qty) * unit_price)
    cgst, sgst, line_total = _line_tax(taxable, gst_rate)
    item.unit_price = _money(unit_price)
    item.gst_rate = gst_rate
    item.taxable_amt = taxable
    item.cgst_amount = cgst
    item.sgst_amount = sgst
    item.line_total = line_total


def _recompute_bill_totals(db: Session, bill: Bill) -> None:
    # Session uses autoflush=False, so push pending add/delete to the DB first,
    # otherwise this SELECT sees the pre-operation item set and totals lag by one step.
    db.flush()
    items = db.scalars(select(BillItem).where(BillItem.bill_id == bill.id)).all()
    subtotal = sum((Decimal(i.taxable_amt) for i in items), Decimal("0"))
    cgst = sum((Decimal(i.cgst_amount) for i in items), Decimal("0"))
    sgst = sum((Decimal(i.sgst_amount) for i in items), Decimal("0"))
    bill.subtotal = _money(subtotal)
    bill.cgst_total = _money(cgst)
    bill.sgst_total = _money(sgst)
    bill.grand_total = _money(subtotal + cgst + sgst)
