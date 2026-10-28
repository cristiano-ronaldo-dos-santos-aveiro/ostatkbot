"""💸 Расходы: просмотр по сменам, чек, отмена с причиной."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.callback_answer import CallbackAnswer
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.db.models import Expense, Status, User, utcnow
from bot.filters import TEXT_INPUT
from bot.handlers.manager.shift_picker import shift_label, shift_rows
from bot.keyboards.callbacks import CB
from bot.keyboards.inline import back_btn, btn, close_btn, markup, nav_row
from bot.keyboards.reply import MB
from bot.services.audit import audit
from bot.services.formatting import InputError, clean_text, fmt_dt, fmt_money, fmt_time
from bot.services.notify import notify_user
from bot.states import ExpenseCancel
from bot.ui import EXPIRED, alert, finish, render, render_input, start_flow, with_error

router = Router(name="manager_expenses")


async def shifts_view(session: AsyncSession, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    rows, any_shifts = await shift_rows(session, "me_shift", "me_shifts", page)
    rows.append([close_btn()])
    return ("💸 Расходы\n\nВыберите смену:" if any_shifts else "💸 Расходы\n\nСмен пока не было."), markup(rows)


@router.message(F.text == MB.EXPENSES)
async def expenses_entry(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await finish(state)
    await start_flow(message, state, *await shifts_view(session))


@router.callback_query(CB.filter(F.a == "me_shifts"))
async def cb_shifts(callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession) -> None:
    await finish(state)
    await render(callback, state, *await shifts_view(session, int(callback_data.v or 0)))


def _label(e: Expense) -> str:
    mark = "❌ " if e.status == Status.CANCELLED else ""
    return f"{mark}{fmt_time(e.created_at)} {fmt_money(e.amount)} — {e.description}"[:60]


@router.callback_query(CB.filter(F.a == "me_shift"))
async def cb_shift(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession,
    callback_answer: CallbackAnswer,
) -> None:
    await finish(state)
    shift = await crud.get_shift(session, callback_data.id)
    if shift is None:
        alert(callback_answer, "Смена не найдена")
        return
    barista = await crud.get_user(session, shift.barista_id)
    expenses = await crud.list_shift_expenses(session, shift.id)
    total = sum(e.amount for e in expenses if e.status == Status.ACTIVE)
    text = (
        f"💸 {shift_label(shift, barista.name if barista else '?')}\n"
        f"Расходы: {fmt_money(total)}\n\n"
        + ("Выберите расход (❌ — отменён):" if expenses else "Расходов нет.")
    )
    rows = [[btn(_label(e), "me_exp", e.id)] for e in expenses]
    rows.append([back_btn("me_shifts", v="0"), close_btn()])
    await render(callback, state, text, markup(rows))


async def expense_view(session: AsyncSession, e: Expense, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    barista = await crud.get_user(session, e.barista_id)
    lines = [
        f"💸 Расход #{e.id}",
        f"Время: {fmt_dt(e.created_at)}",
        f"Бариста: {barista.name if barista else '?'}",
        f"Сумма: {fmt_money(e.amount)}",
        f"На что: {e.description}",
    ]
    if e.status == Status.CANCELLED:
        who = await crud.get_user(session, e.cancelled_by) if e.cancelled_by else None
        lines.append(f"Статус: ❌ отменён ({who.name if who else '?'}, {fmt_dt(e.cancelled_at) if e.cancelled_at else ''})")
        lines.append(f"Причина: {e.cancel_reason or '—'}")
    else:
        lines.append("Статус: ✅ учитывается")
    text = "\n".join(lines)
    if note:
        text = f"{note}\n\n{text}"
    rows = [[btn("🧾 Показать чек", "me_photo", e.id)]]
    if e.status == Status.ACTIVE:
        rows.append([btn("↩️ Отменить расход", "me_cancel", e.id)])
    rows.append([btn("⬅️ К смене", "me_shift", e.shift_id), close_btn()])
    return text, markup(rows)


@router.callback_query(CB.filter(F.a == "me_exp"))
async def cb_expense(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession,
    callback_answer: CallbackAnswer,
) -> None:
    await finish(state)
    e = await crud.get_expense(session, callback_data.id)
    if e is None:
        alert(callback_answer, "Расход не найден")
        return
    await render(callback, state, *await expense_view(session, e))


@router.callback_query(CB.filter(F.a == "me_photo"))
async def cb_photo(
    callback: CallbackQuery, callback_data: CB, session: AsyncSession, callback_answer: CallbackAnswer,
) -> None:
    e = await crud.get_expense(session, callback_data.id)
    if e is None or callback.message is None:
        alert(callback_answer, "Расход не найден")
        return
    await callback.message.answer_photo(
        e.photo_file_id, caption=f"🧾 Чек к расходу #{e.id}: {fmt_money(e.amount)} — {e.description}"[:1000]
    )


def _reason_prompt(e: Expense) -> tuple[str, InlineKeyboardMarkup]:
    return (
        f"↩️ Отмена расхода #{e.id}: {fmt_money(e.amount)} — {e.description}\n\nУкажите причину отмены:",
        markup([nav_row(back_btn("me_exp", e.id))]),
    )


@router.callback_query(CB.filter(F.a == "me_cancel"))
async def cb_cancel(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession,
    callback_answer: CallbackAnswer,
) -> None:
    e = await crud.get_expense(session, callback_data.id)
    if e is None or e.status != Status.ACTIVE:
        alert(callback_answer, "Расход уже отменён или не найден")
        return
    await finish(state)
    await state.set_state(ExpenseCancel.reason)
    await state.update_data(expense_id=e.id)
    await render(callback, state, *_reason_prompt(e))


@router.message(ExpenseCancel.reason, TEXT_INPUT)
async def cancel_reason(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    e = await crud.get_expense(session, (await state.get_data()).get("expense_id", 0))
    if e is None:
        await finish(state)
        await render_input(message, state, EXPIRED)
        return
    try:
        reason = clean_text(message.text, max_len=500, field="Причина")
    except InputError as err:
        text, kb = _reason_prompt(e)
        await render_input(message, state, with_error(text, str(err)), kb)
        return
    await finish(state)
    if e.status != Status.ACTIVE:
        await render_input(message, state, *await expense_view(session, e, "⚠️ Расход уже отменён."))
        return
    e.status = Status.CANCELLED
    e.cancelled_by = user.id
    e.cancel_reason = reason
    e.cancelled_at = utcnow()
    await session.commit()
    audit(user, "expense_cancel", expense=e.id, amount=e.amount, reason=reason)
    await render_input(message, state, *await expense_view(session, e, "✅ Расход отменён и не учитывается в смене."))
    barista = await crud.get_user(session, e.barista_id)
    if barista:
        await notify_user(
            message.bot, barista.telegram_id,
            f"↩️ Менеджер отменил расход: {fmt_money(e.amount)} — {e.description}\nПричина: {reason}",
        )
