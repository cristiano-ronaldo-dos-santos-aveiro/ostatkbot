"""📊 Отчёты: сводка за день / неделю и отчёт по закрытой смене."""
from __future__ import annotations

from datetime import timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.callback_answer import CallbackAnswer
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.db.models import Status, User
from bot.handlers.manager.shift_picker import shift_rows
from bot.keyboards.callbacks import CB
from bot.keyboards.inline import back_btn, btn, close_btn, markup
from bot.keyboards.reply import MB
from bot.services.audit import audit
from bot.services.formatting import local_today
from bot.services.notify import send_long
from bot.services.reports import build_period_report, build_shift_report
from bot.ui import alert, finish, render, start_flow

router = Router(name="manager_reports")


def reports_view() -> tuple[str, InlineKeyboardMarkup]:
    return "📊 Отчёты\n\nВыберите период или смену:", markup(
        [
            [btn("📅 Сегодня", "rep_period", v="today"), btn("📆 Вчера", "rep_period", v="yesterday")],
            [btn("🗓 7 дней", "rep_period", v="week"), btn("🗓 30 дней", "rep_period", v="month")],
            [btn("📋 Отчёт по смене", "rep_shifts", v="0")],
            [close_btn()],
        ]
    )


@router.message(F.text == MB.REPORTS)
async def reports_entry(message: Message, state: FSMContext) -> None:
    await finish(state)
    await start_flow(message, state, *reports_view())


@router.callback_query(CB.filter(F.a == "rep_root"))
async def cb_root(callback: CallbackQuery, state: FSMContext) -> None:
    await finish(state)
    await render(callback, state, *reports_view())


@router.callback_query(CB.filter(F.a == "rep_period"))
async def cb_period(callback: CallbackQuery, callback_data: CB, session: AsyncSession, user: User) -> None:
    today = local_today()
    first, last = {
        "today": (today, today),
        "yesterday": (today - timedelta(days=1), today - timedelta(days=1)),
        "week": (today - timedelta(days=6), today),
        "month": (today - timedelta(days=29), today),
    }.get(callback_data.v, (today, today))
    text = await build_period_report(session, first, last)
    audit(user, "report_period", first=str(first), last=str(last))
    await send_long(callback.bot, callback.message.chat.id, text)


@router.callback_query(CB.filter(F.a == "rep_shifts"))
async def cb_shifts(callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession) -> None:
    rows, any_shifts = await shift_rows(session, "rep_shift", "rep_shifts", int(callback_data.v or 0),
                                        status=Status.CLOSED)
    rows.append([back_btn("rep_root"), close_btn()])
    text = "📋 Выберите закрытую смену:" if any_shifts else "📋 Закрытых смен пока нет."
    await render(callback, state, text, markup(rows))


@router.callback_query(CB.filter(F.a == "rep_shift"))
async def cb_shift(
    callback: CallbackQuery, callback_data: CB, session: AsyncSession, user: User,
    callback_answer: CallbackAnswer,
) -> None:
    shift = await crud.get_shift(session, callback_data.id)
    if shift is None:
        alert(callback_answer, "Смена не найдена")
        return
    audit(user, "report_shift", shift=shift.id)
    await send_long(callback.bot, callback.message.chat.id, await build_shift_report(session, shift))
