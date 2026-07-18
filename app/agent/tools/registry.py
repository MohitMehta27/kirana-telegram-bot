"""Register Gemini function-calling tools that wrap services."""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any, Callable

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.services import (
    analytics_service,
    billing_service,
    deck_pptx,
    inventory_service,
    invoice_pdf,
    khata_service,
    preference_service,
)

logger = logging.getLogger(__name__)


def _dec(v: Any) -> Decimal:
    return Decimal(str(v))


def build_tool_declarations() -> list[dict[str, Any]]:
    """JSON-schema style declarations for Gemini function calling."""
    return [
        {
            "name": "find_product",
            "description": "Search products by name. Always call before selling or receiving stock. Never invent products.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
        {
            "name": "check_stock",
            "description": "Check stock for a product_id or name query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer"},
                    "query": {"type": "string"},
                },
            },
        },
        {
            "name": "list_inventory",
            "description": "List the ENTIRE product catalogue with qty, unit, price and GST. Use for 'all stock', 'full inventory', 'what do I have', 'show everything'.",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "low_stock_report",
            "description": "List products at or below reorder level.",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "receive_stock",
            "description": "Receive inbound stock for an existing product_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer"},
                    "qty": {"type": "number"},
                    "cost_price": {"type": "number"},
                    "mrp": {"type": "number"},
                    "note": {"type": "string"},
                },
                "required": ["product_id", "qty"],
            },
        },
        {
            "name": "create_product",
            "description": "Create a new SKU.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sku_name": {"type": "string"},
                    "unit": {
                        "type": "string",
                        "enum": ["kg", "g", "litre", "ml", "packet", "dozen", "piece"],
                    },
                    "is_loose": {"type": "boolean"},
                    "hsn_code": {"type": "string"},
                    "gst_rate": {"type": "number"},
                    "cost_price": {"type": "number"},
                    "sell_price": {"type": "number"},
                    "reorder_level": {"type": "number"},
                    "opening_qty": {"type": "number"},
                },
                "required": [
                    "sku_name",
                    "unit",
                    "is_loose",
                    "hsn_code",
                    "gst_rate",
                    "cost_price",
                    "sell_price",
                ],
            },
        },
        {
            "name": "start_bill",
            "description": "Start or resume the draft bill for this chat.",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "add_bill_item",
            "description": "Add a line to the draft bill. Does not decrement stock yet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "bill_id": {"type": "integer"},
                    "product_id": {"type": "integer"},
                    "qty": {"type": "number"},
                },
                "required": ["bill_id", "product_id", "qty"],
            },
        },
        {
            "name": "remove_bill_item",
            "description": "Remove a product from the draft bill.",
            "parameters": {
                "type": "object",
                "properties": {
                    "bill_id": {"type": "integer"},
                    "product_id": {"type": "integer"},
                },
                "required": ["bill_id", "product_id"],
            },
        },
        {
            "name": "update_bill_item_qty",
            "description": "Change qty of a draft bill line.",
            "parameters": {
                "type": "object",
                "properties": {
                    "bill_id": {"type": "integer"},
                    "product_id": {"type": "integer"},
                    "qty": {"type": "number"},
                },
                "required": ["bill_id", "product_id", "qty"],
            },
        },
        {
            "name": "set_bill_payment",
            "description": "Set payment mode on draft bill. Use khata + customer_id for credit sales.",
            "parameters": {
                "type": "object",
                "properties": {
                    "bill_id": {"type": "integer"},
                    "mode": {"type": "string", "enum": ["cash", "upi", "card", "khata"]},
                    "ref": {"type": "string"},
                    "customer_id": {"type": "integer"},
                },
                "required": ["bill_id", "mode"],
            },
        },
        {
            "name": "get_bill_summary",
            "description": "Read current bill totals and lines.",
            "parameters": {
                "type": "object",
                "properties": {"bill_id": {"type": "integer"}},
                "required": ["bill_id"],
            },
        },
        {
            "name": "finalize_bill",
            "description": "Finalize draft bill: GST, stock decrement, optional khata charge. Idempotent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "bill_id": {"type": "integer"},
                    "idempotency_key": {"type": "string"},
                },
                "required": ["bill_id"],
            },
        },
        {
            "name": "find_or_create_customer",
            "description": (
                "Resolve a khata (credit) customer. Phone is the unique identity; names may repeat. "
                "Call with just a name to look up an EXISTING customer. If the result has ambiguous=true, "
                "several people share that name — ask the owner which one and call again with customer_id (or phone). "
                "For a NEW credit customer you MUST pass name AND phone. Never use placeholder names like "
                "'Walk-in Customer' — credit must be tied to a real, contactable person."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "phone": {"type": "string", "description": "Customer mobile — required to create a new credit customer"},
                    "customer_id": {"type": "integer", "description": "Pick an exact customer (e.g. after disambiguating)"},
                },
            },
        },
        {
            "name": "get_khata_balance",
            "description": "Get customer khata balance.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "customer_id": {"type": "integer"},
                },
            },
        },
        {
            "name": "charge_khata",
            "description": "Put an amount on customer's credit (increase balance owed).",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "integer"},
                    "amount": {"type": "number"},
                    "note": {"type": "string"},
                },
                "required": ["customer_id", "amount"],
            },
        },
        {
            "name": "settle_khata",
            "description": "Record a payment against khata (reduce balance).",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "integer"},
                    "amount": {"type": "number"},
                    "note": {"type": "string"},
                },
                "required": ["customer_id", "amount"],
            },
        },
        {
            "name": "get_preferences",
            "description": "Read durable owner preferences for this chat.",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "set_preference",
            "description": (
                "Save a durable preference. Common keys: default_payment_mode, default_atta, "
                "shop_name, shop_gstin. SCHEDULING keys (for auto reports): "
                "'daily_report_time' value as 24h HH:MM e.g. '21:00' (owner: 'send daily report at 9pm'); "
                "'weekly_report_day' value lowercase weekday e.g. 'monday' and 'weekly_report_time' as HH:MM "
                "(owner: 'send weekly deck every monday 1pm'). Always convert times to 24h HH:MM before saving."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["key", "value"],
            },
        },
        {
            "name": "clear_preference",
            "description": (
                "Delete a durable preference key. Use to STOP scheduled reports: "
                "key 'daily_report_time' (owner: 'stop daily report'); "
                "keys 'weekly_report_day' and 'weekly_report_time' (owner: 'stop weekly report' — "
                "call this tool twice, once per key)."
            ),
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
            },
        },
        {
            "name": "today_sales_summary",
            "description": "Today's sales: total, GST, cash/upi/card/khata split, top items.",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "close_day",
            "description": "Close the day: snapshot totals, tax, payment split, top items. Idempotent per date.",
            "parameters": {
                "type": "object",
                "properties": {"day": {"type": "string", "description": "YYYY-MM-DD, defaults today"}},
            },
        },
        {
            "name": "generate_invoice_pdf",
            "description": "Generate a GST invoice PDF for a bill_id. Returns a file that is sent to the owner. Prefer finalized bills.",
            "parameters": {
                "type": "object",
                "properties": {"bill_id": {"type": "integer"}},
                "required": ["bill_id"],
            },
        },
        {
            "name": "generate_analysis_deck",
            "description": "Generate a PPTX sales-analysis deck with charts for a date range. Defaults to last 7 days. Returns a file sent to the owner.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                },
            },
        },
    ]


def execute_tool(db: Session, chat_id: int, name: str, args: dict[str, Any]) -> Any:
    logger.info("tool_call name=%s chat_id=%s args=%s", name, chat_id, args)
    handlers: dict[str, Callable[[], Any]] = {
        "find_product": lambda: {
            "ok": True,
            "candidates": inventory_service.find_products(db, args.get("query", "")),
        },
        "check_stock": lambda: inventory_service.check_stock(
            db, product_id=args.get("product_id"), query=args.get("query")
        ),
        "list_inventory": lambda: {"ok": True, "items": inventory_service.list_all(db)},
        "low_stock_report": lambda: {
            "ok": True,
            "items": inventory_service.low_stock_report(db),
        },
        "receive_stock": lambda: inventory_service.receive_stock(
            db,
            product_id=int(args["product_id"]),
            qty=_dec(args["qty"]),
            cost_price=_dec(args["cost_price"]) if args.get("cost_price") is not None else None,
            mrp=_dec(args["mrp"]) if args.get("mrp") is not None else None,
            note=args.get("note"),
        ),
        "create_product": lambda: inventory_service.create_product(
            db,
            sku_name=args["sku_name"],
            unit=args["unit"],
            is_loose=bool(args["is_loose"]),
            hsn_code=args["hsn_code"],
            gst_rate=_dec(args["gst_rate"]),
            cost_price=_dec(args["cost_price"]),
            sell_price=_dec(args["sell_price"]),
            reorder_level=_dec(args.get("reorder_level", 0)),
            opening_qty=_dec(args.get("opening_qty", 0)),
        ),
        "start_bill": lambda: {
            "ok": True,
            **billing_service.get_bill_summary(
                db, billing_service.get_or_create_draft(db, chat_id).id
            ),
        },
        "add_bill_item": lambda: billing_service.add_bill_item(
            db, int(args["bill_id"]), int(args["product_id"]), _dec(args["qty"])
        ),
        "remove_bill_item": lambda: billing_service.remove_bill_item(
            db, int(args["bill_id"]), int(args["product_id"])
        ),
        "update_bill_item_qty": lambda: billing_service.update_bill_item_qty(
            db, int(args["bill_id"]), int(args["product_id"]), _dec(args["qty"])
        ),
        "set_bill_payment": lambda: billing_service.set_bill_payment(
            db,
            int(args["bill_id"]),
            args["mode"],
            ref=args.get("ref"),
            customer_id=args.get("customer_id"),
        ),
        "get_bill_summary": lambda: billing_service.get_bill_summary(db, int(args["bill_id"])),
        "finalize_bill": lambda: billing_service.finalize_bill(
            db, int(args["bill_id"]), idempotency_key=args.get("idempotency_key")
        ),
        "find_or_create_customer": lambda: khata_service.find_or_create_customer(
            db,
            name=args.get("name"),
            phone=args.get("phone"),
            customer_id=args.get("customer_id"),
        ),
        "get_khata_balance": lambda: khata_service.get_khata_balance(
            db, name=args.get("name"), customer_id=args.get("customer_id")
        ),
        "charge_khata": lambda: khata_service.charge_khata(
            db, int(args["customer_id"]), _dec(args["amount"]), note=args.get("note")
        ),
        "settle_khata": lambda: khata_service.settle_khata(
            db, int(args["customer_id"]), _dec(args["amount"]), note=args.get("note")
        ),
        "get_preferences": lambda: {
            "ok": True,
            "preferences": preference_service.get_preferences(db, chat_id),
        },
        "set_preference": lambda: preference_service.set_preference(
            db, chat_id, args["key"], args["value"]
        ),
        "clear_preference": lambda: preference_service.clear_preference(
            db, chat_id, args["key"]
        ),
        "today_sales_summary": lambda: analytics_service.today_sales_summary(db, chat_id),
        "close_day": lambda: analytics_service.close_day(
            db, chat_id, day=date.fromisoformat(args["day"]) if args.get("day") else None
        ),
        "generate_invoice_pdf": lambda: _generate_invoice_pdf(db, chat_id, int(args["bill_id"])),
        "generate_analysis_deck": lambda: _generate_analysis_deck(
            db,
            chat_id,
            args.get("start_date"),
            args.get("end_date"),
        ),
    }
    if name not in handlers:
        return {"ok": False, "error": f"Unknown tool: {name}"}
    try:
        result = handlers[name]()
        logger.info("tool_result name=%s ok=%s", name, result.get("ok", True) if isinstance(result, dict) else True)
        return result
    except Exception as e:
        logger.exception("tool_error name=%s", name)
        return {"ok": False, "error": str(e)}


def _generate_invoice_pdf(db: Session, chat_id: int, bill_id: int) -> dict[str, Any]:
    bill = billing_service.get_bill_summary(db, bill_id)
    if not bill.get("ok"):
        return bill
    prefs = preference_service.get_preferences(db, chat_id)
    path = invoice_pdf.generate_invoice_pdf(bill, prefs)
    return {
        "ok": True,
        "file_path": path,
        "file_kind": "pdf",
        "invoice_number": bill.get("invoice_number"),
        "grand_total": bill.get("grand_total"),
        "message": "Invoice PDF generated.",
    }


def _generate_analysis_deck(
    db: Session,
    chat_id: int,
    start_date: str | None,
    end_date: str | None,
) -> dict[str, Any]:
    end = date.fromisoformat(end_date) if end_date else date.today()
    start = date.fromisoformat(start_date) if start_date else end - timedelta(days=6)
    data = analytics_service.sales_for_range(db, chat_id, start, end)
    low_stock = inventory_service.low_stock_report(db)
    prefs = preference_service.get_preferences(db, chat_id)
    path = deck_pptx.generate_analysis_deck(data, prefs, low_stock=low_stock)
    return {
        "ok": True,
        "file_path": path,
        "file_kind": "pptx",
        "range": f"{start.isoformat()} to {end.isoformat()}",
        "total_sales": data.get("total_sales"),
        "message": "Analysis deck generated.",
    }


def dumps_tool_result(result: Any) -> str:
    return json.dumps(result, ensure_ascii=False, default=str)
