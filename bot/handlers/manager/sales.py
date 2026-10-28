"""📜 Продажи: просмотр по сменам, отмена менеджером, список запросов на отмену."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.callback_answer import CallbackAnswer
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.db.models import Sale, Status, User
from bot.filters import TEXT_INPUT
from bot.handlers.manager.cancel_requests import request_text
from bot.handlers.manager.shift_picker import shift_label, shift_rows
from bot.keyboards.callbacks import CB
from bot.keyboards.inline import back_btn, btn, close_btn, markup, nav_row, pager_row
from bot.keyboards.reply import MB
from bot.services.audit import audit
from bot.services.formatting import PAY_LABEL, InputError, clean_text, fmt_dt, fmt_money
from bot.services.notify import notify_user
from bot.services.sales import SaleError, cancel_sale, sale_line, sale_text
from bot.services.shift import money_calc
from bot.states import SaleCancel
from bot.ui import EXPIRED, alert, finish, render, render_input, start_flow, with_error

router = Router(name="manager_sales")

PAGE = 10


async def shifts_view(session: AsyncSession, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    rows, any_shifts = await shift_rows(session, "ms_shift", "ms_shifts", page)
    pending = await crud.list_pending_requests(session)
    if pending:
        rows.insert(0, [btn(f"⏳ Запросы на отмену ({len(pending)})", "msr_pending")])
    rows.append([close_btn()])
    text = "📜 Продажи\n\nВыберите смену:" if any_shifts else "📜 Продажи\n\nСмен пока не было."
    return text, markup(rows)


@router.message(F.text == MB.SALES)
async def sales_entry(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await finish(state)
    await start_flow(message, state, *await shifts_view(session))


@router.callback_query(CB.filter(F.a == "ms_shifts"))
async def cb_shifts(callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession) -> None:
    await finish(state)
    page = int(callback_data.v or 0)
    await render(callback, state, *await shifts_view(session, page))


async def shift_sales_view(session: AsyncSession, shift_id: int, page: int) -> tuple[str, InlineKeyboardMarkup] | None:
    shift = await crud.get_shift(session, shift_id)
    if shift is None:
        return None
    barista = await crud.get_user(session, shift.barista_id)
    sales, has_more = await crud.list_shift_sales(session, shift.id, offset=page * PAGE, limit=PAGE)
    calc = await money_calc(session, shift)
    text = (
        f"📜 {shift_label(shift, barista.name if barista else '?')}\n"
        f"Выручка: {fmt_money(calc.revenue)} (нал {fmt_money(calc.cash)}, терминал {fmt_money(calc.terminal)}, "
        f"QR {fmt_money(calc.qr)})\n\n"
        + ("Выберите продажу (❌ — отменена):" if sales else "Продаж нет.")
    )
    rows = [[btn(sale_line(s), "ms_sale", s.id)] for s in sales]
    rows.append(pager_row("ms_shift", page, has_more, shift.id))
    rows.append([back_btn("ms_shifts", v="0"), close_btn()])
    return text, markup(rows)


@router.callback_query(CB.filter(F.a == "ms_shift"))
async def cb_shift(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession,
    callback_answer: CallbackAnswer,
) -> None:
    await finish(state)
    view = await shift_sales_view(session, callback_data.id, int(callback_data.v or 0))
    if view is None:
        alert(callback_answer, "Смена не найдена")
        return
    await render(callback, state, *view)


async def sale_view(session: AsyncSession, sale: Sale, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    barista = await crud.get_user(session, sale.barista_id)
    lines = [
        f"🧾 Продажа #{sale.id}",
        f"Время: {fmt_dt(sale.created_at)}",
        f"Бариста: {barista.name if barista else '?'}",
        f"Позиция: {sale.title}",
        f"Количество: {sale.qty}",
        f"Сумма: {fmt_money(sale.amount)}",
        f"Оплата: {PAY_LABEL.get(sale.payment_type, sale.payment_type)}",
    ]
    if sale.status == Status.CANCELLED:
        canceller = await crud.get_user(session, sale.cancelled_by) if sale.cancelled_by else None
        lines.append(
            f"Статус: ❌ отменена ({canceller.name if canceller else '?'}, "
            f"{fmt_dt(sale.cancelled_at) if sale.cancelled_at else ''})"
        )
        lines.append(f"Причина: {sale.cancel_reason or '—'}")
    else:
        lines.append("Статус: ✅ активна")
        pending = await crud.pending_request_for_sale(session, sale.id)
        if pending:
            lines.append(f"⏳ Есть запрос на отмену: {pending.reason}")
    text = "\n".join(lines)
    if note:
        text = f"{note}\n\n{text}"
    rows = []
    if sale.status == Status.ACTIVE:
        rows.append([btn("↩️ Отменить продажу", "ms_cancel", sale.id)])
    rows.append([btn("⬅️ К смене", "ms_shift", sale.shift_id, "0"), close_btn()])
    return text, markup(rows)


@router.callback_query(CB.filter(F.a == "ms_sale"))
async def cb_sale(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession,
    callback_answer: CallbackAnswer,
) -> None:
    await finish(state)
    sale = await crud.get_sale(session, callback_data.id)
    if sale is None:
        alert(callback_answer, "Продажа не найдена")
        return
    await render(callback, state, *await sale_view(session, sale))


def _reason_prompt(sale: Sale) -> tuple[str, InlineKeyboardMarkup]:
    return (
        f"↩️ Отмена продажи #{sale.id}: {sale_text(sale)}\n\nУкажите причину отмены:",
        markup([nav_row(back_btn("ms_sale", sale.id))]),
    )


@router.callback_query(CB.filter(F.a == "ms_cancel"))
async def cb_cancel(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession,
    callback_answer: CallbackAnswer,
) -> None:
    sale = await crud.get_sale(session, callback_data.id)
    if sale is None or sale.status != Status.ACTIVE:
        alert(callback_answer, "Продажа уже отменена или не найдена")
        return
    await finish(state)
    await state.set_state(SaleCancel.reason)
    await state.update_data(sale_id=sale.id)
    await render(callback, state, *_reason_prompt(sale))


@router.message(SaleCancel.reason, TEXT_INPUT)
async def cancel_reason(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    sale = await crud.get_sale(session, (await state.get_data()).get("sale_id", 0))
    if sale is None:
        await finish(state)
        await render_input(message, state, EXPIRED)
        return
    try:
        reason = clean_text(message.text, max_len=500, field="Причина")
    except InputError as e:
        text, kb = _reason_prompt(sale)
        await render_input(message, state, with_error(text, str(e)), kb)
        return
    await finish(state)
    try:
        returned, requests = await cancel_sale(session, sale, user, reason)
    except SaleError as e:
        await render_input(message, state, *await sale_view(session, sale, f"⚠️ {e}"))
        return
    audit(user, "sale_cancel", sale=sale.id, amount=sale.amount, reason=reason, returned=returned)
    note = "✅ Продажа отменена." + (
        " Товары возвращены на склад." if returned else
        " Смена уже закрыта — склад не корректируется (инвентаризация учла факт)."
    )
    await render_input(message, state, *await sale_view(session, sale, note))
    barista = await crud.get_user(session, sale.barista_id)
    if barista:
        text = f"↩️ Менеджер отменил продажу: {sale_text(sale)}\nПричина: {reason}"
        if requests:
            text = f"✅ Ваш запрос на отмену одобрен.\n{text}"
        await notify_user(message.bot, barista.telegram_id, text)


# --- список запросов на отмену ----------------------------------------------------------

@router.callback_query(CB.filter(F.a == "msr_pending"))
async def cb_pending(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await finish(state)
    requests = await crud.list_pending_requests(session)
    rows = []
    for req in requests:
        sale = await crud.get_sale(session, req.sale_id)
        barista = await crud.get_user(session, req.barista_id)
        if sale and barista:
            label = request_text(req, sale, barista.name).split("\n")[0].replace("⚠️ Бариста ", "")
            rows.append([btn(label[:60], "creq_view", req.id)])
    rows.append([back_btn("ms_shifts", v="0"), close_btn()])
    text = "⏳ Запросы на отмену\n\nВыберите запрос:" if requests else "⏳ Нет ожидающих запросов."
    await render(callback, state, text, markup(rows))
