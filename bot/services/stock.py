"""Склад: все изменения остатка проходят через movements."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Movement, Product


async def move(
    session: AsyncSession,
    product_id: int,
    qty: int,
    type_: str,
    *,
    user_id: int | None,
    shift_id: int | None = None,
    sale_id: int | None = None,
    comment: str | None = None,
    photo_file_id: str | None = None,
    created_at: datetime | None = None,
) -> bool:
    """Изменяет остаток атомарно и пишет движение.

    Возвращает True, если товар только что опустился ниже min_stock.
    """
    res = await session.execute(
        update(Product)
        .where(Product.id == product_id)
        .values(stock=Product.stock + qty)
        .returning(Product.stock, Product.min_stock)
        .execution_options(synchronize_session="fetch")
    )
    new_stock, min_stock = res.one()
    movement = Movement(
        product_id=product_id,
        type=type_,
        qty=qty,
        user_id=user_id,
        shift_id=shift_id,
        sale_id=sale_id,
        comment=comment,
        photo_file_id=photo_file_id,
    )
    if created_at is not None:
        movement.created_at = created_at
    session.add(movement)
    old_stock = new_stock - qty
    return qty < 0 and old_stock >= min_stock > new_stock


async def stock_before(
    session: AsyncSession, product_ids: Iterable[int], when: datetime
) -> dict[int, int]:
    """Остаток на момент when (сумма всех движений до него)."""
    ids = list(product_ids)
    if not ids:
        return {}
    rows = await session.execute(
        select(Movement.product_id, func.coalesce(func.sum(Movement.qty), 0))
        .where(Movement.product_id.in_(ids), Movement.created_at < when)
        .group_by(Movement.product_id)
    )
    result = {pid: 0 for pid in ids}
    result.update({pid: int(total) for pid, total in rows})
    return result


async def movement_sums(
    session: AsyncSession, product_ids: Iterable[int], start: datetime, end: datetime
) -> dict[int, dict[str, int]]:
    """{product_id: {type: Σqty}} за период [start, end)."""
    ids = list(product_ids)
    result: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    if not ids:
        return result
    rows = await session.execute(
        select(Movement.product_id, Movement.type, func.sum(Movement.qty))
        .where(
            Movement.product_id.in_(ids),
            Movement.created_at >= start,
            Movement.created_at < end,
        )
        .group_by(Movement.product_id, Movement.type)
    )
    for pid, type_, total in rows:
        result[pid][type_] = int(total or 0)
    return result
