"""Khata (credit ledger) service."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Customer, KhataLedger

logger = logging.getLogger(__name__)

_NOT_FOUND = "Customer not found"


def _mask_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    p = phone.strip()
    if len(p) <= 4:
        return p
    return "*" * (len(p) - 4) + p[-4:]


def _customer_dict(c: Customer, mask: bool = False) -> dict[str, Any]:
    return {
        "id": c.id,
        "name": c.name,
        "phone": _mask_phone(c.phone) if mask else c.phone,
        "khata_balance": float(c.khata_balance),
    }


def _find_by_name(db: Session, name: str) -> list[Customer]:
    return list(
        db.scalars(
            select(Customer).where(func.lower(Customer.name) == name.strip().lower())
        ).all()
    )


def find_or_create_customer(
    db: Session,
    name: str | None = None,
    phone: str | None = None,
    customer_id: int | None = None,
) -> dict[str, Any]:
    """Resolve a khata customer. Phone is the unique identity (names may repeat).

    - customer_id given            -> return that exact customer.
    - phone given                  -> match by phone (authoritative); create if new (name required).
    - only name, 1 match           -> return it.
    - only name, many matches       -> return ambiguous candidates so the agent asks which one.
    - only name, 0 matches          -> needs_phone: cannot create a credit customer without a mobile.
    """
    if customer_id:
        c = db.get(Customer, int(customer_id))
        if not c:
            return {"ok": False, "error": _NOT_FOUND}
        return {"ok": True, "created": False, "customer": _customer_dict(c)}

    phone = phone.strip() if phone else None
    name = name.strip() if name else None

    if phone:
        c = db.scalar(select(Customer).where(Customer.phone == phone))
        if c:
            return {"ok": True, "created": False, "customer": _customer_dict(c)}
        if not name:
            return {"ok": False, "error": "name required to create a new customer"}
        c = Customer(name=name, phone=phone, khata_balance=Decimal("0"))
        db.add(c)
        db.commit()
        db.refresh(c)
        logger.info("create_customer id=%s name=%s", c.id, name)
        return {"ok": True, "created": True, "customer": _customer_dict(c)}

    if not name:
        return {"ok": False, "error": "name or phone required"}

    matches = _find_by_name(db, name)
    if len(matches) == 1:
        return {"ok": True, "created": False, "customer": _customer_dict(matches[0])}
    if len(matches) > 1:
        return {
            "ok": True,
            "ambiguous": True,
            "candidates": [_customer_dict(c, mask=True) for c in matches],
            "message": (
                f"{len(matches)} customers named '{name}'. Ask the owner which one "
                "(by phone / last 4 digits), then pass customer_id or phone."
            ),
        }
    return {
        "ok": False,
        "needs_phone": True,
        "error": (
            f"No existing customer named '{name}'. For a new credit customer, "
            "ask for their mobile number, then call again with name + phone."
        ),
    }


def get_khata_balance(db: Session, name: str | None = None, customer_id: int | None = None) -> dict[str, Any]:
    if customer_id:
        customer = db.get(Customer, int(customer_id))
        if not customer:
            return {"ok": False, "error": _NOT_FOUND}
        return {"ok": True, "customer": _customer_dict(customer)}

    if not name:
        return {"ok": False, "error": "name or customer_id required"}

    matches = _find_by_name(db, name)
    if not matches:
        return {"ok": False, "error": _NOT_FOUND}
    if len(matches) > 1:
        return {
            "ok": True,
            "ambiguous": True,
            "candidates": [_customer_dict(c, mask=True) for c in matches],
            "message": f"{len(matches)} customers named '{name}'. Ask which one (by phone).",
        }
    return {"ok": True, "customer": _customer_dict(matches[0])}


def charge_khata(db: Session, customer_id: int, amount: Decimal, note: str | None = None) -> dict[str, Any]:
    if amount <= 0:
        return {"ok": False, "error": "amount must be positive"}
    customer = db.execute(
        select(Customer).where(Customer.id == customer_id).with_for_update()
    ).scalar_one_or_none()
    if not customer:
        return {"ok": False, "error": _NOT_FOUND}
    customer.khata_balance = Decimal(customer.khata_balance) + amount
    db.add(
        KhataLedger(
            customer_id=customer.id,
            amount=amount,
            note=note or "Manual charge",
            ref_type="manual",
        )
    )
    db.commit()
    logger.info("charge_khata customer_id=%s amount=%s balance=%s", customer_id, amount, customer.khata_balance)
    return get_khata_balance(db, customer_id=customer_id)


def settle_khata(db: Session, customer_id: int, amount: Decimal, note: str | None = None) -> dict[str, Any]:
    if amount <= 0:
        return {"ok": False, "error": "amount must be positive"}
    customer = db.execute(
        select(Customer).where(Customer.id == customer_id).with_for_update()
    ).scalar_one_or_none()
    if not customer:
        return {"ok": False, "error": _NOT_FOUND}

    history = db.scalar(select(KhataLedger.id).where(KhataLedger.customer_id == customer_id).limit(1))
    if history is None and Decimal(customer.khata_balance) == 0:
        return {"ok": False, "error": "No khata exists for this customer — nothing to settle"}

    if amount > Decimal(customer.khata_balance) + Decimal("0.01"):
        return {
            "ok": False,
            "error": f"Settlement ₹{amount} exceeds balance ₹{customer.khata_balance}",
            "khata_balance": float(customer.khata_balance),
        }

    customer.khata_balance = Decimal(customer.khata_balance) - amount
    db.add(
        KhataLedger(
            customer_id=customer.id,
            amount=-amount,
            note=note or "Payment received",
            ref_type="manual",
        )
    )
    db.commit()
    logger.info("settle_khata customer_id=%s amount=%s balance=%s", customer_id, amount, customer.khata_balance)
    return get_khata_balance(db, customer_id=customer_id)
