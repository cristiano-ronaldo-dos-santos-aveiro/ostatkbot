"""Простые запросы к базе. Бизнес-операции — в bot/services."""
from __future__ import annotations

import secrets
from collections.abc import Sequence

from aiogram.types import User as TgUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_config
from bot.db.models import (
    SIZES,
    Addon,
    CancelRequest,
    Drink,
    DrinkSize,
    Expense,
    Invite,
    Product,
    QuickButton,
    Role,
    Sale,
    Shift,
    Status,
    Unit,
    User,
    utcnow,
)

# --- пользователи ---------------------------------------------------------------


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def get_user_by_tg(session: AsyncSession, telegram_id: int) -> User | None:
    return await session.scalar(select(User).where(User.telegram_id == telegram_id))


def _tg_name(tg_user: TgUser) -> str:
    return (tg_user.full_name or tg_user.username or str(tg_user.id))[:64]


async def resolve_user(session: AsyncSession, tg_user: TgUser) -> User | None:
    """Возвращает пользователя с доступом или None.

    Менеджеры берутся из MANAGER_IDS и автоматически заводятся в users.
    """
    user = await get_user_by_tg(session, tg_user.id)
    if tg_user.id in get_config().manager_ids:
        if user is None:
            user = User(telegram_id=tg_user.id, name=_tg_name(tg_user), role=Role.MANAGER)
            session.add(user)
            await session.commit()
        elif user.role != Role.MANAGER or not user.is_active:
            user.role = Role.MANAGER
            user.is_active = True
            await session.commit()
        return user
    if user and user.is_active and user.role == Role.BARISTA:
        return user
    return None


async def list_baristas(session: AsyncSession) -> Sequence[User]:
    res = await session.scalars(
        select(User).where(User.role == Role.BARISTA).order_by(User.is_active.desc(), User.name)
    )
    return res.all()


async def list_users(session: AsyncSession, ids: set[int]) -> dict[int, User]:
    if not ids:
        return {}
    res = await session.scalars(select(User).where(User.id.in_(ids)))
    return {u.id: u for u in res}


async def upsert_barista(session: AsyncSession, telegram_id: int, name: str) -> User:
    """Создаёт бариста или реактивирует существующую запись."""
    user = await get_user_by_tg(session, telegram_id)
    if user is None:
        user = User(telegram_id=telegram_id, name=name, role=Role.BARISTA, is_active=True)
        session.add(user)
    else:
        user.name = name
        user.role = Role.BARISTA
        user.is_active = True
    await session.flush()
    return user


async def create_invite(session: AsyncSession, name: str, created_by: int) -> Invite:
    invite = Invite(token=secrets.token_urlsafe(12), name=name, created_by=created_by)
    session.add(invite)
    await session.flush()
    return invite


async def use_invite(session: AsyncSession, token: str, tg_user: TgUser) -> tuple[User | None, str]:
    """Регистрирует бариста по приглашению. Возвращает (user, ошибка)."""
    if tg_user.id in get_config().manager_ids:
        return None, "Вы менеджер — приглашение не нужно."
    invite = await session.scalar(select(Invite).where(Invite.token == token))
    if invite is None or not invite.is_active or invite.used_by is not None:
        return None, "Ссылка недействительна или уже использована. Попросите менеджера новую."
    user = await upsert_barista(session, tg_user.id, invite.name)
    invite.used_by = user.id
    invite.used_at = utcnow()
    invite.is_active = False
    await session.commit()
    return user, ""


# --- товары ---------------------------------------------------------------------

_UNIT_ORDER = {Unit.PCS: 0, Unit.G: 1, Unit.ML: 2}


def sort_products(products: Sequence[Product]) -> list[Product]:
    return sorted(products, key=lambda p: (_UNIT_ORDER.get(p.unit_type, 9), p.name.lower()))


async def list_products(
    session: AsyncSession,
    active_only: bool = True,
    unit_type: str | None = None,
    sellable: bool | None = None,
) -> list[Product]:
    q = select(Product)
    if active_only:
        q = q.where(Product.is_active.is_(True))
    if unit_type:
        q = q.where(Product.unit_type == unit_type)
    if sellable is not None:
        q = q.where(Product.is_sellable.is_(sellable))
    res = await session.scalars(q)
    products = sort_products(res.all())
    if not active_only:
        products.sort(key=lambda p: not p.is_active)
    return products


async def get_product(session: AsyncSession, product_id: int) -> Product | None:
    return await session.get(Product, product_id)


async def get_products(session: AsyncSession, ids: set[int]) -> dict[int, Product]:
    if not ids:
        return {}
    res = await session.scalars(select(Product).where(Product.id.in_(ids)))
    return {p.id: p for p in res}


async def product_name_taken(session: AsyncSession, name: str, exclude_id: int | None = None) -> bool:
    # SQLite lower() понимает только латиницу — сравниваем в Python
    q = select(Product.name).where(Product.is_active.is_(True))
    if exclude_id:
        q = q.where(Product.id != exclude_id)
    target = name.casefold()
    return any(n.casefold() == target for n in (await session.scalars(q)).all())


# --- меню -----------------------------------------------------------------------


async def list_drinks(session: AsyncSession, active_only: bool = True) -> list[Drink]:
    q = select(Drink).order_by(Drink.is_active.desc(), Drink.name)
    if active_only:
        q = q.where(Drink.is_active.is_(True))
    return list((await session.scalars(q)).all())


async def list_sellable_drinks(session: AsyncSession) -> list[Drink]:
    """Активные напитки, у которых есть хотя бы один активный размер."""
    q = (
        select(Drink)
        .where(Drink.is_active.is_(True))
        .where(
            Drink.id.in_(select(DrinkSize.drink_id).where(DrinkSize.is_active.is_(True)))
        )
        .order_by(Drink.name)
    )
    return list((await session.scalars(q)).all())


async def get_drink(session: AsyncSession, drink_id: int) -> Drink | None:
    return await session.get(Drink, drink_id)


def _size_order(ds: DrinkSize) -> int:
    return SIZES.index(ds.size) if ds.size in SIZES else 9


async def list_sizes(session: AsyncSession, drink_id: int, active_only: bool = True) -> list[DrinkSize]:
    q = select(DrinkSize).where(DrinkSize.drink_id == drink_id)
    if active_only:
        q = q.where(DrinkSize.is_active.is_(True))
    return sorted((await session.scalars(q)).all(), key=_size_order)


async def get_size(session: AsyncSession, size_id: int) -> DrinkSize | None:
    return await session.get(DrinkSize, size_id)


async def get_size_with_drink(session: AsyncSession, size_id: int) -> tuple[DrinkSize, Drink] | None:
    row = (
        await session.execute(
            select(DrinkSize, Drink).join(Drink, Drink.id == DrinkSize.drink_id).where(
                DrinkSize.id == size_id
            )
        )
    ).first()
    return (row[0], row[1]) if row else None


async def size_labels(session: AsyncSession, size_ids: set[int]) -> dict[int, str]:
    """{drink_size_id: «Капучино M»} по текущим названиям."""
    if not size_ids:
        return {}
    rows = await session.execute(
        select(DrinkSize.id, Drink.name, DrinkSize.size)
        .join(Drink, Drink.id == DrinkSize.drink_id)
        .where(DrinkSize.id.in_(size_ids))
    )
    return {sid: f"{name} {size}" for sid, name, size in rows}


async def list_addons(session: AsyncSession, active_only: bool = True) -> list[Addon]:
    q = select(Addon).order_by(Addon.is_active.desc(), Addon.name)
    if active_only:
        q = q.where(Addon.is_active.is_(True))
    return list((await session.scalars(q)).all())


async def get_addon(session: AsyncSession, addon_id: int) -> Addon | None:
    return await session.get(Addon, addon_id)


async def get_addons(session: AsyncSession, ids: Sequence[int]) -> list[Addon]:
    if not ids:
        return []
    res = await session.scalars(select(Addon).where(Addon.id.in_(list(ids))))
    by_id = {a.id: a for a in res}
    return [by_id[i] for i in ids if i in by_id]


async def list_quick_buttons(session: AsyncSession) -> list[QuickButton]:
    q = select(QuickButton).where(QuickButton.is_active.is_(True)).order_by(QuickButton.id)
    return list((await session.scalars(q)).all())


async def get_quick_button(session: AsyncSession, qb_id: int) -> QuickButton | None:
    return await session.get(QuickButton, qb_id)


async def linked_product_ids(session: AsyncSession) -> set[int]:
    """Товары, которые списываются продажами (стаканы, добавки, продаваемые)."""
    ids: set[int] = set()
    ids.update(
        x for x in (await session.scalars(select(DrinkSize.cup_product_id))).all() if x
    )
    ids.update(x for x in (await session.scalars(select(Addon.product_id))).all() if x)
    ids.update(
        (await session.scalars(select(Product.id).where(Product.is_sellable.is_(True)))).all()
    )
    return ids


# --- смены ----------------------------------------------------------------------


async def get_shift(session: AsyncSession, shift_id: int) -> Shift | None:
    return await session.get(Shift, shift_id)


async def get_open_shift(session: AsyncSession, barista_id: int) -> Shift | None:
    return await session.scalar(
        select(Shift)
        .where(Shift.barista_id == barista_id, Shift.status == Status.OPEN)
        .order_by(Shift.id.desc())
    )


async def list_shifts(
    session: AsyncSession, status: str | None = None, offset: int = 0, limit: int = 10
) -> tuple[list[Shift], bool]:
    """Смены (открытые первыми, затем новые). Возвращает (список, есть_ещё)."""
    q = select(Shift)
    if status:
        q = q.where(Shift.status == status)
    q = q.order_by((Shift.status == Status.OPEN).desc(), Shift.opened_at.desc())
    rows = list((await session.scalars(q.offset(offset).limit(limit + 1))).all())
    return rows[:limit], len(rows) > limit


# --- продажи и расходы ---------------------------------------------------------


async def get_sale(session: AsyncSession, sale_id: int) -> Sale | None:
    return await session.get(Sale, sale_id)


async def list_shift_sales(
    session: AsyncSession, shift_id: int, offset: int = 0, limit: int = 10
) -> tuple[list[Sale], bool]:
    q = (
        select(Sale)
        .where(Sale.shift_id == shift_id)
        .order_by(Sale.created_at.desc(), Sale.id.desc())
        .offset(offset)
        .limit(limit + 1)
    )
    rows = list((await session.scalars(q)).all())
    return rows[:limit], len(rows) > limit


async def last_active_sales(session: AsyncSession, shift_id: int, limit: int = 5) -> list[Sale]:
    q = (
        select(Sale)
        .where(Sale.shift_id == shift_id, Sale.status == Status.ACTIVE)
        .order_by(Sale.created_at.desc(), Sale.id.desc())
        .limit(limit)
    )
    return list((await session.scalars(q)).all())


async def pending_request_for_sale(session: AsyncSession, sale_id: int) -> CancelRequest | None:
    return await session.scalar(
        select(CancelRequest).where(
            CancelRequest.sale_id == sale_id, CancelRequest.status == Status.PENDING
        )
    )


async def list_pending_requests(session: AsyncSession) -> list[CancelRequest]:
    q = (
        select(CancelRequest)
        .where(CancelRequest.status == Status.PENDING)
        .order_by(CancelRequest.created_at)
    )
    return list((await session.scalars(q)).all())


async def get_cancel_request(session: AsyncSession, request_id: int) -> CancelRequest | None:
    return await session.get(CancelRequest, request_id)


async def list_shift_expenses(session: AsyncSession, shift_id: int) -> list[Expense]:
    q = select(Expense).where(Expense.shift_id == shift_id).order_by(Expense.created_at.desc())
    return list((await session.scalars(q)).all())


async def get_expense(session: AsyncSession, expense_id: int) -> Expense | None:
    return await session.get(Expense, expense_id)
