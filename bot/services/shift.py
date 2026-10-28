"""Смена: открытие, денежные расчёты, закрытие с инвентаризацией."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.db.models import Expense, Move, Pay, Product, Sale, Shift, ShiftCount, Status, User, utcnow
from bot.services.stock import move


class ShiftError(Exception):
    pass


@dataclass
class MoneyCalc:
    opening: int
    cash: int
    terminal: int
    qr: int
    expenses: int

    @property
    def revenue(self) -> int:
        return self.cash + self.terminal + self.qr

    @property
    def expected_cash(self) -> int:
        return self.opening + self.cash - self.expenses

    @property
    def expected_terminal(self) -> int:
        return self.terminal

    @property
    def expected_qr(self) -> int:
        return self.qr


async def money_calc(session: AsyncSession, shift: Shift) -> MoneyCalc:
    """Учитываются только активные (не отменённые) продажи и расходы."""
    rows = await session.execute(
        select(Sale.payment_type, func.coalesce(func.sum(Sale.amount), 0))
        .where(Sale.shift_id == shift.id, Sale.status == Status.ACTIVE)
        .group_by(Sale.payment_type)
    )
    by_pay = {pay: int(total) for pay, total in rows}
    expenses = await session.scalar(
        select(func.coalesce(func.sum(Expense.amount), 0)).where(
            Expense.shift_id == shift.id, Expense.status == Status.ACTIVE
        )
    )
    return MoneyCalc(
        opening=shift.opening_cash,
        cash=by_pay.get(Pay.CASH, 0),
        terminal=by_pay.get(Pay.TERMINAL, 0),
        qr=by_pay.get(Pay.QR, 0),
        expenses=int(expenses or 0),
    )


async def open_shift(session: AsyncSession, barista: User, opening_cash: int) -> Shift:
    if await crud.get_open_shift(session, barista.id):
        raise ShiftError("У вас уже есть открытая смена.")
    shift = Shift(barista_id=barista.id, opening_cash=opening_cash, status=Status.OPEN)
    session.add(shift)
    await session.commit()
    return shift


async def close_shift(
    session: AsyncSession,
    shift: Shift,
    counts: dict[int, int],
    fact_cash: int,
    fact_terminal: int,
    fact_qr: int,
) -> None:
    """Закрывает смену: пишет shift_counts, приводит остатки к факту (movements inventory)."""
    if shift.status != Status.OPEN:
        raise ShiftError("Смена уже закрыта.")
    now = utcnow()
    products = (
        await session.scalars(
            select(Product)
            .where(Product.id.in_(list(counts)))
            .execution_options(populate_existing=True)
        )
    ).all()
    for product in products:
        fact = counts[product.id]
        expected = product.stock
        diff = fact - expected
        session.add(
            ShiftCount(
                shift_id=shift.id,
                product_id=product.id,
                expected_qty=expected,
                fact_qty=fact,
                diff=diff,
            )
        )
        if diff:
            await move(
                session,
                product.id,
                diff,
                Move.INVENTORY,
                user_id=shift.barista_id,
                shift_id=shift.id,
                comment="Инвентаризация при закрытии смены",
                created_at=now,
            )
    shift.fact_cash = fact_cash
    shift.fact_terminal = fact_terminal
    shift.fact_qr = fact_qr
    shift.closed_at = now
    shift.status = Status.CLOSED
    await session.commit()
