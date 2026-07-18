"""ORM models mirroring sql/001_schema.sql"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    sku_name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    unit: Mapped[str] = mapped_column(
        Enum("kg", "g", "litre", "ml", "packet", "dozen", "piece", name="product_unit"),
        nullable=False,
    )
    is_loose: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    hsn_code: Mapped[str] = mapped_column(String(16), nullable=False)
    gst_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("0.00"))
    cost_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    sell_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    quantity_on_hand: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, default=Decimal("0"))
    reorder_level: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, default=Decimal("0"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(6), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(6), server_default=func.now(), onupdate=func.now()
    )

    stock_movements: Mapped[list[StockLedger]] = relationship(back_populates="product")
    bill_items: Mapped[list[BillItem]] = relationship(back_populates="product")


class StockLedger(Base):
    __tablename__ = "stock_ledger"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    change_qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    reason: Mapped[str] = mapped_column(Enum("receive", "sale", "adjustment", name="stock_reason"), nullable=False)
    ref_type: Mapped[Optional[str]] = mapped_column(String(32))
    ref_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    note: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(6), server_default=func.now())

    product: Mapped[Product] = relationship(back_populates="stock_movements")


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(20), unique=True)
    khata_balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(6), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(6), server_default=func.now(), onupdate=func.now()
    )

    ledger_entries: Mapped[list[KhataLedger]] = relationship(back_populates="customer")
    bills: Mapped[list[Bill]] = relationship(back_populates="customer")


class KhataLedger(Base):
    __tablename__ = "khata_ledger"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(255))
    ref_type: Mapped[Optional[str]] = mapped_column(String(32))
    ref_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(6), server_default=func.now())

    customer: Mapped[Customer] = relationship(back_populates="ledger_entries")


class Bill(Base):
    __tablename__ = "bills"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        Enum("draft", "finalized", "cancelled", name="bill_status"),
        nullable=False,
        default="draft",
    )
    payment_mode: Mapped[Optional[str]] = mapped_column(
        Enum("cash", "upi", "card", "khata", name="payment_mode")
    )
    payment_ref: Mapped[Optional[str]] = mapped_column(String(120))
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    cgst_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    sgst_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    grand_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    customer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"))
    invoice_number: Mapped[Optional[str]] = mapped_column(String(32), unique=True)
    finalized_at: Mapped[Optional[datetime]] = mapped_column(DateTime(6))
    created_at: Mapped[datetime] = mapped_column(DateTime(6), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(6), server_default=func.now(), onupdate=func.now()
    )

    customer: Mapped[Optional[Customer]] = relationship(back_populates="bills")
    items: Mapped[list[BillItem]] = relationship(
        back_populates="bill", cascade="all, delete-orphan"
    )


class BillItem(Base):
    __tablename__ = "bill_items"
    __table_args__ = (UniqueConstraint("bill_id", "product_id", name="uq_bill_product"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    bill_id: Mapped[int] = mapped_column(ForeignKey("bills.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    gst_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    taxable_amt: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    cgst_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    sgst_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(6), server_default=func.now())

    bill: Mapped[Bill] = relationship(back_populates="items")
    product: Mapped[Product] = relationship(back_populates="bill_items")


class Preference(Base):
    __tablename__ = "preferences"
    __table_args__ = (UniqueConstraint("chat_id", "pref_key", name="uq_preferences_chat_key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pref_key: Mapped[str] = mapped_column(String(64), nullable=False)
    pref_value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(6), server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(6), server_default=func.now())


class ProcessedUpdate(Base):
    __tablename__ = "processed_updates"

    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    response_text: Mapped[Optional[str]] = mapped_column(Text)
    response_file_path: Mapped[Optional[str]] = mapped_column(String(512))
    processed_at: Mapped[datetime] = mapped_column(DateTime(6), server_default=func.now())


class DailyClose(Base):
    __tablename__ = "daily_close"
    __table_args__ = (UniqueConstraint("close_date", "chat_id", name="uq_daily_close_date_chat"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    close_date: Mapped[date] = mapped_column(Date, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_sales: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    tax_collected: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    cash_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    upi_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    card_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    khata_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    top_items: Mapped[Optional[Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(6), server_default=func.now())


class InboundDocument(Base):
    __tablename__ = "inbound_documents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    telegram_file_id: Mapped[Optional[str]] = mapped_column(String(255))
    file_name: Mapped[Optional[str]] = mapped_column(String(255))
    mime_type: Mapped[Optional[str]] = mapped_column(String(128))
    source_type: Mapped[str] = mapped_column(
        Enum("pdf", "docx", "image", "voice", "other", name="inbound_source"),
        nullable=False,
        default="other",
    )
    doc_kind: Mapped[Optional[str]] = mapped_column(
        Enum(
            "supplier_invoice",
            "purchase_list",
            "gst_return",
            "bank_statement",
            "unknown",
            name="doc_kind",
        )
    )
    raw_extract: Mapped[Optional[str]] = mapped_column(MEDIUMTEXT)
    structured_json: Mapped[Optional[Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(
        Enum("received", "extracted", "applied", "rejected", name="inbound_status"),
        nullable=False,
        default="received",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(6), server_default=func.now())


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    role: Mapped[str] = mapped_column(Enum("user", "assistant", "tool", name="msg_role"), nullable=False)
    content: Mapped[str] = mapped_column(MEDIUMTEXT, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(6), server_default=func.now())
