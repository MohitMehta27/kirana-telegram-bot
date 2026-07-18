"""GST-correct invoice PDF via reportlab."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.config import get_settings

logger = logging.getLogger(__name__)


def generate_invoice_pdf(bill: dict[str, Any], prefs: dict[str, str]) -> str:
    """bill = billing_service.get_bill_summary(...) dict. Returns file path."""
    settings = get_settings()
    out_dir = Path(settings.generated_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    inv_no = bill.get("invoice_number") or f"DRAFT-{bill['bill_id']}"
    file_path = str(out_dir / f"invoice_{inv_no}.pdf")

    doc = SimpleDocTemplate(
        file_path,
        pagesize=A4,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        title=f"Invoice {inv_no}",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "shop", parent=styles["Title"], fontSize=18, spaceAfter=2
    )
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=9, textColor=colors.grey)
    right = ParagraphStyle("right", parent=styles["Normal"], alignment=2)

    elements: list[Any] = []

    shop_name = prefs.get("shop_name", "Kirana Store")
    elements.append(Paragraph(shop_name, title_style))
    meta = []
    if prefs.get("shop_address"):
        meta.append(prefs["shop_address"])
    if prefs.get("shop_gstin"):
        meta.append(f"GSTIN: {prefs['shop_gstin']}")
    if prefs.get("shop_phone"):
        meta.append(f"Ph: {prefs['shop_phone']}")
    if meta:
        elements.append(Paragraph(" &nbsp;|&nbsp; ".join(meta), small))
    elements.append(Spacer(1, 6 * mm))

    # Invoice header row
    header_tbl = Table(
        [
            [
                Paragraph(f"<b>TAX INVOICE</b><br/>No: {inv_no}", styles["Normal"]),
                Paragraph(
                    f"Date: {datetime.now().strftime('%d-%b-%Y %H:%M')}<br/>"
                    f"Payment: {(bill.get('payment_mode') or '-').upper()}"
                    + (f" ({bill.get('payment_ref')})" if bill.get("payment_ref") else ""),
                    right,
                ),
            ]
        ],
        colWidths=[90 * mm, 88 * mm],
    )
    header_tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    elements.append(header_tbl)
    elements.append(Spacer(1, 4 * mm))

    # Bill To (customer) — always shown for khata; shown when available otherwise
    cust_name = bill.get("customer_name")
    cust_phone = bill.get("customer_phone")
    if cust_name:
        bill_to = f"<b>Bill To:</b> {cust_name}"
        if cust_phone:
            bill_to += f" &nbsp;|&nbsp; Ph: {cust_phone}"
        if (bill.get("payment_mode") or "").lower() == "khata":
            bill_to += " &nbsp;|&nbsp; <b>(ON CREDIT / KHATA)</b>"
        elements.append(Paragraph(bill_to, styles["Normal"]))
        elements.append(Spacer(1, 4 * mm))

    # Line items
    data = [
        ["#", "Item", "HSN", "Qty", "Rate", "Taxable", "GST%", "CGST", "SGST", "Total"]
    ]
    for i, it in enumerate(bill.get("items", []), start=1):
        taxable = it["qty"] * it["unit_price"]
        data.append(
            [
                str(i),
                it["sku_name"],
                _hsn(it),
                _num(it["qty"]),
                _num(it["unit_price"]),
                _money(taxable),
                f"{it['gst_rate']:.0f}%",
                _money(it["cgst"]),
                _money(it["sgst"]),
                _money(it["line_total"]),
            ]
        )

    table = Table(data, repeatRows=1, colWidths=[
        8 * mm, 42 * mm, 16 * mm, 14 * mm, 16 * mm, 20 * mm, 12 * mm, 16 * mm, 16 * mm, 18 * mm
    ])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4e79")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f7fb")]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    elements.append(table)
    elements.append(Spacer(1, 4 * mm))

    # Totals block
    totals = [
        ["Subtotal (Taxable)", _money(bill["subtotal"])],
        ["CGST", _money(bill["cgst_total"])],
        ["SGST", _money(bill["sgst_total"])],
        ["Grand Total", _money(bill["grand_total"])],
    ]
    tot_tbl = Table(totals, colWidths=[40 * mm, 30 * mm], hAlign="RIGHT")
    tot_tbl.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("LINEABOVE", (0, -1), (-1, -1), 0.6, colors.black),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    elements.append(tot_tbl)
    elements.append(Spacer(1, 10 * mm))
    elements.append(Paragraph("Thank you! Goods once sold are subject to store policy.", small))

    doc.build(elements)
    logger.info("invoice_pdf_generated bill_id=%s path=%s", bill.get("bill_id"), file_path)
    return file_path


def _hsn(it: dict[str, Any]) -> str:
    return str(it.get("hsn_code", "") or "")


def _num(x: float) -> str:
    return f"{x:g}"


def _money(x: float) -> str:
    return f"{float(x):,.2f}"
