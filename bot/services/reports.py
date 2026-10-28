"""Отчёты: по закрытой смене и сводка за период (только для менеджера)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.db.models import (
    CancelRequest,
    Expense,
    Move,
    Product,
    Sale,
    SaleType,
    Shift,
    ShiftCount,
    Status,
    Unit,
)
from bot.services.formatting import (
    fmt_date,
    fmt_money,
    fmt_money_signed,
    fmt_qty,
    fmt_qty_signed,
    fmt_time,
    local_range_utc,
)
from bot.services.shift import MoneyCalc, money_calc
from bot.services.stock import movement_sums, stock_before


@dataclass
class CountRow:
    product: Product
    expected: int
    fact: int
    diff: int
    linked: bool
    opening: int = 0
    income: int = 0
    writeoff: int = 0  # положительное число

    @property
    def consumption(self) -> int:
        """Расход за смену = остаток на начало + приход − списания − факт."""
        return self.opening + self.income - self.writeoff - self.fact


@dataclass
class ShiftAnalysis:
    shift: Shift
    barista_name: str
    money: MoneyCalc
    rows: list[CountRow] = field(default_factory=list)

    def money_diffs(self) -> list[tuple[str, int, int, int]]:
        s, m = self.shift, self.money
        return [
            ("Нал", m.expected_cash, s.fact_cash or 0, (s.fact_cash or 0) - m.expected_cash),
            ("Терминал", m.expected_terminal, s.fact_terminal or 0,
             (s.fact_terminal or 0) - m.expected_terminal),
            ("QR", m.expected_qr, s.fact_qr or 0, (s.fact_qr or 0) - m.expected_qr),
        ]

    def discrepancies(self) -> list[str]:
        out = [f"{name} {fmt_money_signed(diff)}" for name, _, _, diff in self.money_diffs() if diff]
        for r in self.rows:
            # для весовых без привязки к продажам «разница» — это просто расход
            if r.diff and (r.product.unit_type == Unit.PCS or r.linked):
                out.append(f"{r.product.name} {fmt_qty_signed(r.diff, r.product.unit_type)}")
        return out


async def analyze_shift(session: AsyncSession, shift: Shift) -> ShiftAnalysis:
    barista = await crud.get_user(session, shift.barista_id)
    analysis = ShiftAnalysis(
        shift=shift,
        barista_name=barista.name if barista else "?",
        money=await money_calc(session, shift),
    )
    counts = (
        await session.scalars(select(ShiftCount).where(ShiftCount.shift_id == shift.id))
    ).all()
    if not counts or shift.closed_at is None:
        return analysis
    products = await crud.get_products(session, {c.product_id for c in counts})
    linked = await crud.linked_product_ids(session)
    weight_ids = [c.product_id for c in counts if products[c.product_id].unit_type != Unit.PCS]
    opening = await stock_before(session, weight_ids, shift.opened_at)
    sums = await movement_sums(session, weight_ids, shift.opened_at, shift.closed_at)
    for c in counts:
        p = products[c.product_id]
        row = CountRow(p, c.expected_qty, c.fact_qty, c.diff, linked=c.product_id in linked)
        if p.unit_type != Unit.PCS:
            row.opening = opening.get(p.id, 0)
            row.income = sums[p.id].get(Move.INCOME, 0)
            row.writeoff = -sums[p.id].get(Move.WRITEOFF, 0)
        analysis.rows.append(row)
    analysis.rows.sort(key=lambda r: r.product.name.lower())
    return analysis


async def _sales_breakdown(session: AsyncSession, where) -> tuple[list[str], list[str]]:
    """Строки «— Капучино M: 12 шт, 240 000 сум» для напитков и товаров."""
    drinks = await session.execute(
        select(Sale.drink_size_id, func.sum(Sale.qty), func.sum(Sale.amount))
        .where(*where, Sale.type == SaleType.DRINK)
        .group_by(Sale.drink_size_id)
    )
    drinks = drinks.all()
    labels = await crud.size_labels(session, {d[0] for d in drinks if d[0]})
    drink_lines = sorted(
        (f" — {labels.get(sid, '?')}: {int(q)} шт, {fmt_money(int(a))}" for sid, q, a in drinks)
    )
    products = await session.execute(
        select(Sale.product_id, func.sum(Sale.qty), func.sum(Sale.amount))
        .where(*where, Sale.type == SaleType.PRODUCT)
        .group_by(Sale.product_id)
    )
    products = products.all()
    pmap = await crud.get_products(session, {p[0] for p in products if p[0]})
    product_lines = sorted(
        f" — {pmap[pid].name if pid in pmap else '?'}: {int(q)} шт, {fmt_money(int(a))}"
        for pid, q, a in products
    )
    return drink_lines, product_lines


async def build_shift_report(session: AsyncSession, shift: Shift) -> str:
    a = await analyze_shift(session, shift)
    lines = [
        f"📋 Смена: {a.barista_name}, {fmt_date(shift.opened_at)}, "
        f"{fmt_time(shift.opened_at)}–{fmt_time(shift.closed_at)}",
        "",
        f"💰 Выручка: {fmt_money(a.money.revenue)}",
    ]
    icons = {"Нал": "💵", "Терминал": "💳", "QR": "📱"}
    if shift.status == Status.CLOSED:
        for name, exp, fact, diff in a.money_diffs():
            lines.append(
                f"{icons[name]} {name}: ожид. {fmt_money(exp)} / факт {fmt_money(fact)} / "
                f"разница {fmt_money_signed(diff)}"
            )
    else:
        lines.append("Смена ещё открыта — сверки кассы нет.")
    lines.append(f"🏦 Размен: {fmt_money(shift.opening_cash)}")

    expenses = [
        e for e in await crud.list_shift_expenses(session, shift.id) if e.status == Status.ACTIVE
    ]
    lines += ["", f"💸 Расходы: {fmt_money(sum(e.amount for e in expenses))}"]
    lines += [f" — {fmt_money(e.amount)} — {e.description}" for e in reversed(expenses)]

    where = (Sale.shift_id == shift.id, Sale.status == Status.ACTIVE)
    drink_lines, product_lines = await _sales_breakdown(session, where)
    lines += ["", "☕ Продажи по напиткам и размерам:"] + (drink_lines or [" — нет"])
    lines += ["🥪 Продажи товаров:"] + (product_lines or [" — нет"])

    pcs_rows = [r for r in a.rows if r.product.unit_type == Unit.PCS]
    weight_rows = [r for r in a.rows if r.product.unit_type != Unit.PCS]
    if a.rows:
        lines += ["", "🥤 Остатки (ожид. / факт / разница):"]
        lines += [
            f" — {r.product.name}: {r.expected} / {r.fact} / {fmt_qty_signed(r.diff, Unit.PCS)}"
            for r in pcs_rows
        ] or [" — нет штучных товаров"]
        lines.append("⚖️ Весовые: расход за смену")
        for r in weight_rows:
            u = r.product.unit_type
            line = (
                f" — {r.product.name}: {fmt_qty(r.consumption, u)} "
                f"(начало {fmt_qty(r.opening, u)}, приход {fmt_qty(r.income, u)}, "
                f"списано {fmt_qty(r.writeoff, u)}, факт {fmt_qty(r.fact, u)})"
            )
            if r.linked:
                line += (
                    f"; ожид. {fmt_qty(r.expected, u)} / факт {fmt_qty(r.fact, u)} / "
                    f"разница {fmt_qty_signed(r.diff, u)}"
                )
            lines.append(line)
        if not weight_rows:
            lines.append(" — нет весовых товаров")

    cancelled = await session.scalar(
        select(func.count(Sale.id)).where(Sale.shift_id == shift.id, Sale.status == Status.CANCELLED)
    )
    requests = await session.scalar(
        select(func.count(CancelRequest.id))
        .join(Sale, Sale.id == CancelRequest.sale_id)
        .where(Sale.shift_id == shift.id)
    )
    lines += ["", f"↩️ Отмены: {cancelled or 0}, запросов: {requests or 0}"]

    low = [r for r in a.rows if r.fact < r.product.min_stock]
    if low:
        lines += [
            "",
            "⚠️ Ниже мин. остатка: "
            + ", ".join(
                f"{r.product.name} ({fmt_qty(r.fact, r.product.unit_type)} < "
                f"{fmt_qty(r.product.min_stock, r.product.unit_type)})"
                for r in low
            ),
        ]

    if shift.status == Status.CLOSED:
        diffs = a.discrepancies()
        lines += ["", "Итог: ✅ Всё сходится" if not diffs else "Итог: ⚠️ Расхождения: " + "; ".join(diffs)]
    return "\n".join(lines)


async def build_period_report(session: AsyncSession, first_day: date, last_day: date) -> str:
    start, end = local_range_utc(first_day, last_day)
    period = (
        first_day.strftime("%d.%m.%Y")
        if first_day == last_day
        else f"{first_day.strftime('%d.%m')}–{last_day.strftime('%d.%m.%Y')}"
    )
    in_period = (Sale.created_at >= start, Sale.created_at < end)
    active = (*in_period, Sale.status == Status.ACTIVE)

    by_pay = dict(
        (
            await session.execute(
                select(Sale.payment_type, func.sum(Sale.amount)).where(*active).group_by(Sale.payment_type)
            )
        ).all()
    )
    revenue = sum(int(v) for v in by_pay.values())
    n_active = await session.scalar(select(func.count(Sale.id)).where(*active)) or 0
    n_cancelled = (
        await session.scalar(
            select(func.count(Sale.id)).where(*in_period, Sale.status == Status.CANCELLED)
        )
        or 0
    )
    lines = [
        f"📊 Сводка: {period}",
        "",
        f"💰 Выручка: {fmt_money(revenue)}",
        f" 💵 Нал: {fmt_money(int(by_pay.get('cash', 0)))}",
        f" 💳 Терминал: {fmt_money(int(by_pay.get('terminal', 0)))}",
        f" 📱 QR: {fmt_money(int(by_pay.get('qr', 0)))}",
        f"🧾 Продаж: {n_active} (отменено: {n_cancelled})",
    ]
    drink_lines, product_lines = await _sales_breakdown(session, active)
    lines += ["", "☕ Напитки:"] + (drink_lines or [" — нет"])
    lines += ["🥪 Товары:"] + (product_lines or [" — нет"])

    exp_total, exp_count = (
        await session.execute(
            select(func.coalesce(func.sum(Expense.amount), 0), func.count(Expense.id)).where(
                Expense.created_at >= start, Expense.created_at < end, Expense.status == Status.ACTIVE
            )
        )
    ).one()
    lines += ["", f"💸 Расходы: {fmt_money(int(exp_total))} ({exp_count} шт)"]

    shifts = (
        await session.scalars(
            select(Shift)
            .where(Shift.status == Status.CLOSED, Shift.closed_at >= start, Shift.closed_at < end)
            .order_by(Shift.closed_at)
        )
    ).all()
    lines += ["", f"📋 Закрыто смен: {len(shifts)}", "⚖️ Расхождения:"]
    any_diff = False
    for shift in shifts:
        a = await analyze_shift(session, shift)
        diffs = a.discrepancies()
        if diffs:
            any_diff = True
            lines.append(f" — {a.barista_name} {fmt_date(shift.opened_at)}: " + "; ".join(diffs))
    if not any_diff:
        lines.append(" — ✅ нет")

    # по бариста: выручка, продажи, отмены, запросы
    stats: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for bid, total, cnt in await session.execute(
        select(Sale.barista_id, func.sum(Sale.amount), func.count(Sale.id))
        .where(*active)
        .group_by(Sale.barista_id)
    ):
        stats[bid]["revenue"] = int(total)
        stats[bid]["sales"] = int(cnt)
    for bid, cnt in await session.execute(
        select(Sale.barista_id, func.count(Sale.id))
        .where(*in_period, Sale.status == Status.CANCELLED)
        .group_by(Sale.barista_id)
    ):
        stats[bid]["cancelled"] = int(cnt)
    for bid, cnt in await session.execute(
        select(CancelRequest.barista_id, func.count(CancelRequest.id))
        .where(CancelRequest.created_at >= start, CancelRequest.created_at < end)
        .group_by(CancelRequest.barista_id)
    ):
        stats[bid]["requests"] = int(cnt)
    users = await crud.list_users(session, set(stats))
    lines += ["", "👤 По бариста:"]
    if not stats:
        lines.append(" — нет данных")
    for bid, st in sorted(stats.items(), key=lambda kv: -kv[1]["revenue"]):
        name = users[bid].name if bid in users else "?"
        lines.append(
            f" — {name}: выручка {fmt_money(st['revenue'])}, продаж {st['sales']}, "
            f"отмен {st['cancelled']}, запросов на отмену {st['requests']}"
        )
    return "\n".join(lines)
