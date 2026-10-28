"""⚠️ Запросить отмену: бариста не отменяет сам — запрос уходит менеджеру."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.callback_answer import CallbackAnswer
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.db.models import CancelRequest, Sale, Status, User
from bot.filters import TEXT_INPUT
from bot.handlers.barista.guard import require_shift
from bot.handlers.manager.cancel_requests import request_kb, request_text
from bot.keyboards.callbacks import CB
from bot.keyboards.inline import back_btn, btn, markup, nav_row
from bot.keyboards.reply import MB
from bot.services.audit import audit
from bot.services.formatting import InputError, clean_text
from bot.services.notify import notify_managers
from bot.services.sales import sale_line, sale_text
from bot.states import CancelRequestFlow
from bot.ui import EXPIRED, alert, finish, render, render_input, start_flow, with_error

router = Router(name="barista_cancel")

HEAD = "⚠️ Запрос на отмену"


async def _sales_view(session: AsyncSession, shift_id: int) -> tuple[str, InlineKeyboardMarkup] | None:
    sales = await crud.last_active_sales(session, shift_id, 5)
    if not sales:
        return None
    rows = [[btn(sale_line(s), "cr_sale", s.id)] for s in sales]
    rows.append(nav_row())
    return f"{HEAD}\n\nВыберите продажу (последние 5 в смене):", markup(rows)


@router.message(F.text == MB.CANCEL_REQ)
async def cancel_entry(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    await finish(state)
    shift = await require_shift(message, session, user)
    if shift is None:
        return
    view = await _sales_view(session, shift.id)
    if view is None:
        await message.answer("В этой смене нет продаж, которые можно отменить.")
        return
    await state.set_state(CancelRequestFlow.sale)
    await start_flow(message, state, *view)


def _reason_prompt(sale: Sale) -> tuple[str, InlineKeyboardMarkup]:
    return (
        f"{HEAD}\n\n{sale_text(sale)}\n\nНапишите причину (обязательно):",
        markup([nav_row(back_btn("cr_back"))]),
    )


@router.callback_query(CancelRequestFlow.reason, CB.filter(F.a == "cr_back"))
async def cb_back(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, user: User,
    callback_answer: CallbackAnswer,
) -> None:
    shift = await crud.get_open_shift(session, user.id)
    view = await _sales_view(session, shift.id) if shift else None
    if view is None:
        await finish(state)
        alert(callback_answer, EXPIRED)
        return
    await state.set_state(CancelRequestFlow.sale)
    await render(callback, state, *view)


async def _check_sale(session: AsyncSession, sale_id: int, user: User) -> tuple[Sale | None, str]:
    sale = await crud.get_sale(session, sale_id)
    shift = await crud.get_open_shift(session, user.id)
    if sale is None or shift is None or sale.shift_id != shift.id:
        return None, "Можно отменять только продажи текущей смены."
    if sale.status != Status.ACTIVE:
        return None, "Эта продажа уже отменена."
    if await crud.pending_request_for_sale(session, sale.id):
        return None, "По этой продаже уже есть запрос — ждите решения менеджера."
    return sale, ""


@router.callback_query(CancelRequestFlow.sale, CB.filter(F.a == "cr_sale"))
async def cb_sale(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession, user: User,
    callback_answer: CallbackAnswer,
) -> None:
    sale, error = await _check_sale(session, callback_data.id, user)
    if sale is None:
        alert(callback_answer, error)
        return
    await state.update_data(sale_id=sale.id)
    await state.set_state(CancelRequestFlow.reason)
    await render(callback, state, *_reason_prompt(sale))


@router.message(CancelRequestFlow.reason, TEXT_INPUT)
async def reason_input(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    sale, error = await _check_sale(session, (await state.get_data()).get("sale_id", 0), user)
    if sale is None:
        await finish(state)
        await render_input(message, state, f"⚠️ {error}")
        return
    try:
        reason = clean_text(message.text, max_len=500, field="Причина")
    except InputError as e:
        text, kb = _reason_prompt(sale)
        await render_input(message, state, with_error(text, str(e)), kb)
        return
    req = CancelRequest(sale_id=sale.id, barista_id=user.id, reason=reason)
    session.add(req)
    await session.commit()
    audit(user, "cancel_request", request=req.id, sale=sale.id, amount=sale.amount, reason=reason)
    await finish(state)
    await render_input(
        message, state,
        f"📨 Запрос отправлен менеджеру:\n{sale_text(sale)}\nПричина: {reason}\n\nВы получите уведомление о решении.",
    )
    await notify_managers(message.bot, request_text(req, sale, user.name), reply_markup=request_kb(req))
