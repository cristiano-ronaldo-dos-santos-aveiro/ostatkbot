"""Запросы бариста на отмену продажи: одобрить / отклонить."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup
from aiogram.utils.callback_answer import CallbackAnswer
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.db.models import CancelRequest, Sale, Status, User, utcnow
from bot.keyboards.callbacks import CB
from bot.keyboards.inline import back_btn, btn, close_btn, markup
from bot.services.audit import audit
from bot.services.formatting import PAY_SHORT, fmt_dt, fmt_money, fmt_time
from bot.services.notify import notify_user
from bot.services.sales import SaleError, cancel_sale, sale_text
from bot.ui import alert, render

router = Router(name="manager_cancel_requests")

STATUS_TEXT = {
    Status.PENDING: "⏳ ожидает решения",
    Status.APPROVED: "✅ одобрен — продажа отменена",
    Status.REJECTED: "❌ отклонён",
}


def request_text(req: CancelRequest, sale: Sale, barista_name: str) -> str:
    qty = f" ×{sale.qty}" if sale.qty > 1 else ""
    return (
        f"⚠️ Бариста {barista_name} просит отменить: {sale.title}{qty}, {fmt_money(sale.amount)}, "
        f"{PAY_SHORT.get(sale.payment_type, sale.payment_type)}, {fmt_time(sale.created_at)}.\n"
        f"Причина: {req.reason}"
    )


def request_kb(req: CancelRequest) -> InlineKeyboardMarkup:
    return markup([[btn("✅ Отменить", "creq_ok", req.id), btn("❌ Отклонить", "creq_no", req.id)]])


async def _load(session: AsyncSession, request_id: int) -> tuple[CancelRequest, Sale, User] | None:
    req = await crud.get_cancel_request(session, request_id)
    if req is None:
        return None
    sale = await crud.get_sale(session, req.sale_id)
    barista = await crud.get_user(session, req.barista_id)
    if sale is None or barista is None:
        return None
    return req, sale, barista


async def _resolved_text(session: AsyncSession, req: CancelRequest, sale: Sale, barista: User) -> str:
    resolver = await crud.get_user(session, req.resolved_by) if req.resolved_by else None
    tail = STATUS_TEXT.get(req.status, req.status)
    if resolver and req.resolved_at:
        tail += f" ({resolver.name}, {fmt_dt(req.resolved_at)})"
    return f"{request_text(req, sale, barista.name)}\n\nСтатус: {tail}"


@router.callback_query(CB.filter(F.a == "creq_view"))
async def cb_view(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession,
    callback_answer: CallbackAnswer,
) -> None:
    loaded = await _load(session, callback_data.id)
    if loaded is None:
        alert(callback_answer, "Запрос не найден")
        return
    req, sale, barista = loaded
    if req.status == Status.PENDING:
        kb = request_kb(req)
        kb.inline_keyboard.append([back_btn("msr_pending"), close_btn()])
        await render(callback, state, request_text(req, sale, barista.name), kb)
    else:
        await render(callback, state, await _resolved_text(session, req, sale, barista),
                     markup([[back_btn("msr_pending"), close_btn()]]))


@router.callback_query(CB.filter(F.a == "creq_ok"))
async def cb_approve(
    callback: CallbackQuery, callback_data: CB, session: AsyncSession, user: User,
    callback_answer: CallbackAnswer,
) -> None:
    loaded = await _load(session, callback_data.id)
    if loaded is None:
        alert(callback_answer, "Запрос не найден")
        return
    req, sale, barista = loaded
    if req.status != Status.PENDING:
        callback_answer.text = "Запрос уже обработан"
        await render(callback, None, await _resolved_text(session, req, sale, barista))
        return
    try:
        returned, _ = await cancel_sale(session, sale, user, req.reason)
    except SaleError as e:
        # продажу уже отменили напрямую — закрываем запрос как одобренный
        req.status, req.resolved_by, req.resolved_at = Status.APPROVED, user.id, utcnow()
        await session.commit()
        callback_answer.text = str(e)
        await render(callback, None, await _resolved_text(session, req, sale, barista))
        return
    await session.refresh(req)
    audit(user, "cancel_request_approve", request=req.id, sale=sale.id, amount=sale.amount, returned=returned)
    callback_answer.text = "Продажа отменена"
    await render(callback, None, await _resolved_text(session, req, sale, barista))
    await notify_user(
        callback.bot, barista.telegram_id,
        f"✅ Отмена одобрена: {sale_text(sale)}\nПродажа не учитывается в смене"
        + (", товары возвращены на склад." if returned else "."),
    )


@router.callback_query(CB.filter(F.a == "creq_no"))
async def cb_reject(
    callback: CallbackQuery, callback_data: CB, session: AsyncSession, user: User,
    callback_answer: CallbackAnswer,
) -> None:
    loaded = await _load(session, callback_data.id)
    if loaded is None:
        alert(callback_answer, "Запрос не найден")
        return
    req, sale, barista = loaded
    if req.status != Status.PENDING:
        callback_answer.text = "Запрос уже обработан"
        await render(callback, None, await _resolved_text(session, req, sale, barista))
        return
    req.status, req.resolved_by, req.resolved_at = Status.REJECTED, user.id, utcnow()
    await session.commit()
    audit(user, "cancel_request_reject", request=req.id, sale=sale.id)
    callback_answer.text = "Запрос отклонён"
    await render(callback, None, await _resolved_text(session, req, sale, barista))
    await notify_user(
        callback.bot, barista.telegram_id,
        f"❌ Отмена отклонена: {sale_text(sale)}\nПродажа остаётся в смене.",
    )
