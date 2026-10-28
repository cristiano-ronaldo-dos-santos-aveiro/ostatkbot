"""Модели базы данных.

Правило: ничего не удаляем — только помечаем status=cancelled / is_active=False.
Все изменения остатка товара проходят через таблицу movements.
Время хранится в UTC (naive), показывается в часовом поясе из настроек.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


# --- справочники значений -------------------------------------------------

class Role:
    MANAGER = "manager"
    BARISTA = "barista"


class Unit:
    PCS = "pcs"
    G = "g"
    ML = "ml"
    ALL = (PCS, G, ML)


class Pay:
    CASH = "cash"
    TERMINAL = "terminal"
    QR = "qr"
    ALL = (CASH, TERMINAL, QR)


class SaleType:
    DRINK = "drink"
    PRODUCT = "product"


class Status:
    ACTIVE = "active"
    CANCELLED = "cancelled"
    OPEN = "open"
    CLOSED = "closed"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Move:
    INCOME = "income"
    SALE = "sale"
    WRITEOFF = "writeoff"
    INVENTORY = "inventory"
    CANCEL_RETURN = "cancel_return"


SIZES = ("S", "M", "L")


# --- таблицы ----------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Invite(Base):
    """Пригласительная ссылка для бариста (t.me/<bot>?start=inv_<token>)."""

    __tablename__ = "invites"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    used_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Product(Base):
    """Товар на складе. stock хранится в шт / г / мл (целые числа)."""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    unit_type: Mapped[str] = mapped_column(String(8))
    is_sellable: Mapped[bool] = mapped_column(Boolean, default=False)
    price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_stock: Mapped[int] = mapped_column(Integer, default=0)
    stock: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Drink(Base):
    __tablename__ = "drinks"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DrinkSize(Base):
    __tablename__ = "drink_sizes"

    id: Mapped[int] = mapped_column(primary_key=True)
    drink_id: Mapped[int] = mapped_column(ForeignKey("drinks.id"), index=True)
    size: Mapped[str] = mapped_column(String(2))
    price: Mapped[int] = mapped_column(Integer)
    cup_product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Addon(Base):
    __tablename__ = "addons"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    price: Mapped[int] = mapped_column(Integer, default=0)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    product_qty: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class QuickButton(Base):
    __tablename__ = "quick_buttons"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(64))
    drink_size_id: Mapped[int | None] = mapped_column(ForeignKey("drink_sizes.id"), nullable=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    addon_ids: Mapped[list] = mapped_column(JSON, default=list)
    payment_type: Mapped[str] = mapped_column(String(16))
    qty: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Shift(Base):
    __tablename__ = "shifts"

    id: Mapped[int] = mapped_column(primary_key=True)
    barista_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    opening_cash: Mapped[int] = mapped_column(Integer, default=0)
    fact_cash: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fact_terminal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fact_qr: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=Status.OPEN, index=True)


class Sale(Base):
    __tablename__ = "sales"

    id: Mapped[int] = mapped_column(primary_key=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("shifts.id"), index=True)
    barista_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    type: Mapped[str] = mapped_column(String(16))
    drink_size_id: Mapped[int | None] = mapped_column(ForeignKey("drink_sizes.id"), nullable=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    qty: Mapped[int] = mapped_column(Integer, default=1)
    addons: Mapped[list] = mapped_column(JSON, default=list)  # [{"id", "name", "price"}]
    title: Mapped[str] = mapped_column(String(255), default="")  # снимок названия на момент продажи
    amount: Mapped[int] = mapped_column(Integer)
    payment_type: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default=Status.ACTIVE, index=True)
    cancelled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class CancelRequest(Base):
    __tablename__ = "cancel_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id"), index=True)
    barista_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=Status.PENDING, index=True)
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Expense(Base):
    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("shifts.id"), index=True)
    barista_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    amount: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text)
    photo_file_id: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default=Status.ACTIVE, index=True)
    cancelled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Movement(Base):
    """Журнал склада: qty со знаком (+ приход/возврат, − продажа/списание)."""

    __tablename__ = "movements"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    type: Mapped[str] = mapped_column(String(16), index=True)
    qty: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    shift_id: Mapped[int | None] = mapped_column(ForeignKey("shifts.id"), nullable=True, index=True)
    sale_id: Mapped[int | None] = mapped_column(ForeignKey("sales.id"), nullable=True, index=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class ShiftCount(Base):
    __tablename__ = "shift_counts"

    id: Mapped[int] = mapped_column(primary_key=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("shifts.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    expected_qty: Mapped[int] = mapped_column(Integer)
    fact_qty: Mapped[int] = mapped_column(Integer)
    diff: Mapped[int] = mapped_column(Integer)
