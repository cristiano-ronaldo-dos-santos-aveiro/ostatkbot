"""Продажи: создание, отмена, описание."""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.db.models import (
    CancelRequest,
    Move,
    Movement,
    Pay,
    Product,
    QuickButton,
    Sale,
    SaleType,
    Shift,
    Status,
    User,
    utcnow,
)
from bot.services.formatting import PAY_SHORT, fmt_money, fmt_time
from bot.services.stock import move


class SaleError(Exception):
    """Продажу невозможно провести (текст — для пользователя)."""


@dataclass
class SaleSpec:
    kind: str  # SaleType.DRINK / SaleType.PRODUCT
    item_id: int  # drink_size_id или product_id
    addon_ids: list[int] = field(default_factory=list)
    qty: int = 1
    payment: str = Pay.CASH


@dataclass
class SalePreview:
    title: str
    unit_price: int
    addons: list[dict]

    def amount(self, qty: int) -> int:
        return self.unit_price * qty


def addons_title(names: list[str]) -> str:
    return "без добавок" if not names else "+ " + ", ".join(names)


async def preview(session: AsyncSession, spec: SaleSpec) -> SalePreview:
    """Название и цена позиции по текущему меню (с проверкой доступности)."""
    if spec.kind == SaleType.DRINK:
        pair = await crud.get_size_with_drink(session, spec.item_id)
        if pair is None:
            raise SaleError("Размер напитка не найден.")
        size, drink = pair
        if not (size.is_active and drink.is_active):
            raise SaleError(f"{drink.name} {size.size} сейчас недоступен.")
        addons = await crud.get_addons(session, spec.addon_ids)
        inactive = [a.name for a in addons if not a.is_active]
        if inactive or len(addons) != len(spec.addon_ids):
            raise SaleError("Некоторые добавки недоступны: " + (", ".join(inactive) or "?"))
        title = f"{drink.name} {size.size}, {addons_title([a.name for a in addons])}"
        return SalePreview(
            title=title[:255],
            unit_price=size.price + sum(a.price for a in addons),
            addons=[{"id": a.id, "name": a.name, "price": a.price} for a in addons],
        )
    product = await crud.get_product(session, spec.item_id)
    if product is None or not product.is_active or not product.is_sellable or product.price is None:
        raise SaleError("Товар недоступен для продажи.")
    return SalePreview(title=product.name, unit_price=product.price, addons=[])


async def create_sale(
    session: AsyncSession, barista: User, shift: Shift, spec: SaleSpec
) -> tuple[Sale, list[Product]]:
    """Проводит продажу и списывает товары. Возвращает (продажа, товары ниже минимума)."""
    if shift.status != Status.OPEN:
        raise SaleError("Смена закрыта.")
    if spec.qty <= 0:
        raise SaleError("Количество должно быть больше нуля.")
    if spec.payment not in Pay.ALL:
        raise SaleError("Неизвестный тип оплаты.")
    pv = await preview(session, spec)
    sale = Sale(
        shift_id=shift.id,
        barista_id=barista.id,
        type=spec.kind,
        drink_size_id=spec.item_id if spec.kind == SaleType.DRINK else None,
        product_id=spec.item_id if spec.kind == SaleType.PRODUCT else None,
        qty=spec.qty,
        addons=pv.addons,
        title=pv.title,
        amount=pv.amount(spec.qty),
        payment_type=spec.payment,
    )
    session.add(sale)
    await session.flush()

    # что списать со склада: {product_id: qty}
    to_write_off: dict[int, int] = {}
    if spec.kind == SaleType.DRINK:
        size = await crud.get_size(session, spec.item_id)
        if size and size.cup_product_id:
            to_write_off[size.cup_product_id] = to_write_off.get(size.cup_product_id, 0) + spec.qty
        for addon in await crud.get_addons(session, spec.addon_ids):
            if addon.product_id and addon.product_qty > 0:
                to_write_off[addon.product_id] = (
                    to_write_off.get(addon.product_id, 0) + addon.product_qty * spec.qty
                )
    else:
        to_write_off[spec.item_id] = spec.qty

    crossed: list[int] = []
    for product_id, qty in to_write_off.items():
        if await move(
            session,
            product_id,
            -qty,
            Move.SALE,
            user_id=barista.id,
            shift_id=shift.id,
            sale_id=sale.id,
            comment=f"Продажа #{sale.id}",
        ):
            crossed.append(product_id)
    await session.commit()
    low = list((await crud.get_products(session, set(crossed))).values())
    return sale, low


async def cancel_sale(
    session: AsyncSession, sale: Sale, by_user: User, reason: str
) -> tuple[bool, list[CancelRequest]]:
    """Отменяет продажу.

    Товары возвращаются на склад, только если смена ещё открыта: после закрытия
    инвентаризация уже привела остатки к факту. Висящие запросы на отмену
    этой продажи помечаются одобренными. Возвращает (вернули_на_склад, запросы).
    """
    if sale.status != Status.ACTIVE:
        raise SaleError("Продажа уже отменена.")
    now = utcnow()
    sale.status = Status.CANCELLED
    sale.cancelled_by = by_user.id
    sale.cancel_reason = reason
    sale.cancelled_at = now

    shift = await crud.get_shift(session, sale.shift_id)
    returned = False
    if shift and shift.status == Status.OPEN:
        movements = (
            await session.scalars(
                select(Movement).where(Movement.sale_id == sale.id, Movement.type == Move.SALE)
            )
        ).all()
        for m in movements:
            await move(
                session,
                m.product_id,
                -m.qty,
                Move.CANCEL_RETURN,
                user_id=by_user.id,
                shift_id=sale.shift_id,
                sale_id=sale.id,
                comment=f"Отмена продажи #{sale.id}",
            )
        returned = True

    pending = (
        await session.scalars(
            select(CancelRequest).where(
                CancelRequest.sale_id == sale.id, CancelRequest.status == Status.PENDING
            )
        )
    ).all()
    for req in pending:
        req.status = Status.APPROVED
        req.resolved_by = by_user.id
        req.resolved_at = now
    await session.commit()
    return returned, list(pending)


def sale_text(sale: Sale) -> str:
    """«Капучино M, без добавок ×2, нал — 40 000 сум»."""
    qty = f" ×{sale.qty}" if sale.qty > 1 else ""
    return f"{sale.title}{qty}, {PAY_SHORT.get(sale.payment_type, sale.payment_type)} — {fmt_money(sale.amount)}"


def sale_line(sale: Sale) -> str:
    """Короткая строка для списков/кнопок."""
    mark = "❌ " if sale.status == Status.CANCELLED else ""
    return f"{mark}{fmt_time(sale.created_at)} {sale_text(sale)}"


def quick_spec(qb: QuickButton) -> SaleSpec:
    if qb.drink_size_id:
        return SaleSpec(SaleType.DRINK, qb.drink_size_id, list(qb.addon_ids or []), qb.qty, qb.payment_type)
    return SaleSpec(SaleType.PRODUCT, qb.product_id or 0, [], qb.qty, qb.payment_type)


def auto_quick_title(pv: SalePreview, spec: SaleSpec) -> str:
    """«Капучино M + Ваниль ×2 · нал» — короткое название быстрой кнопки."""
    base = pv.title.replace(", без добавок", "").replace(", + ", " + ")
    qty = f" ×{spec.qty}" if spec.qty > 1 else ""
    return f"{base}{qty} · {PAY_SHORT.get(spec.payment, spec.payment)}"[:64]
