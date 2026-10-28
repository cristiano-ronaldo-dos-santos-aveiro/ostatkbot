"""➕ Приход, ➖ Списание, 📦 Склад."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.db.models import Move, Product, Unit, User
from bot.filters import TEXT_INPUT
from bot.keyboards.callbacks import CB
from bot.keyboards.inline import back_btn, btn, close_btn, markup, nav_row, product_picker
from bot.keyboards.reply import MB
from bot.services.audit import audit
from bot.services.formatting import (
    UNIT_GROUP,
    InputError,
    clean_text,
    fmt_qty,
    fmt_qty_signed,
    parse_qty,
    qty_hint,
)
from bot.services.notify import notify_low_stock
from bot.services.stock import move
from bot.states import Income, Writeoff
from bot.ui import EXPIRED, finish, render, render_input, start_flow, with_error

router = Router(name="manager_warehouse")


# --- 📦 Склад ------------------------------------------------------------------------

async def stock_text(session: AsyncSession) -> str:
    products = await crud.list_products(session)
    if not products:
        return "📦 Склад пуст — добавьте товары в «⚙️ Товары»."
    lines = ["📦 Остатки на складе", ""]
    low = 0
    for unit in Unit.ALL:
        group = [p for p in products if p.unit_type == unit]
        if not group:
            continue
        lines.append(f"{UNIT_GROUP[unit]}:")
        for p in group:
            warn = p.stock < p.min_stock
            low += warn
            mark = "⚠️ " if warn else ""
            lines.append(
                f" {mark}{p.name}: {fmt_qty(p.stock, p.unit_type)}"
                + (f" (мин. {fmt_qty(p.min_stock, p.unit_type)})" if p.min_stock else "")
            )
        lines.append("")
    lines.append(f"⚠️ Ниже минимума: {low}" if low else "✅ Все остатки выше минимума")
    return "\n".join(lines)


@router.message(F.text == MB.STOCK)
async def stock_entry(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await finish(state)
    await message.answer(await stock_text(session))


# --- общий выбор товара ----------------------------------------------------------------

async def _picker(session: AsyncSession, action: str, title: str) -> tuple[str, InlineKeyboardMarkup]:
    products = await crud.list_products(session)
    if not products:
        return f"{title}\n\nТоваров нет — добавьте их в «⚙️ Товары».", markup([[close_btn()]])
    return f"{title}\n\nВыберите товар:", markup(
        product_picker(products, action, show_stock=True) + [nav_row()]
    )


# --- ➕ Приход --------------------------------------------------------------------------

INCOME_TITLE = "➕ Приход"


def _income_qty_prompt(p: Product) -> tuple[str, InlineKeyboardMarkup]:
    return (
        f"{INCOME_TITLE}: {p.name}\nСейчас: {fmt_qty(p.stock, p.unit_type)}\n\n"
        f"Сколько пришло? ({qty_hint(p.unit_type)})",
        markup([nav_row(back_btn("inc_restart"))]),
    )


def _income_comment_prompt(p: Product, qty: int) -> tuple[str, InlineKeyboardMarkup]:
    return (
        f"{INCOME_TITLE}: {p.name} {fmt_qty_signed(qty, p.unit_type)}\n\n"
        "Добавьте комментарий (поставщик, номер накладной) или фото накладной.\n"
        "Можно фото с подписью. Или нажмите «Пропустить».",
        markup([[btn("⏭ Пропустить", "inc_skip")], nav_row()]),
    )


@router.message(F.text == MB.INCOME)
async def income_entry(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await finish(state)
    await state.set_state(Income.product)
    await start_flow(message, state, *await _picker(session, "inc_prod", INCOME_TITLE))


@router.callback_query(CB.filter(F.a == "inc_restart"))
async def cb_income_restart(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await finish(state)
    await state.set_state(Income.product)
    await render(callback, state, *await _picker(session, "inc_prod", INCOME_TITLE))


@router.callback_query(Income.product, CB.filter(F.a == "inc_prod"))
async def cb_income_product(callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession) -> None:
    p = await crud.get_product(session, callback_data.id)
    if p is None:
        return
    await state.update_data(product_id=p.id)
    await state.set_state(Income.qty)
    await render(callback, state, *_income_qty_prompt(p))


@router.message(Income.qty, TEXT_INPUT)
async def income_qty(message: Message, state: FSMContext, session: AsyncSession) -> None:
    p = await crud.get_product(session, (await state.get_data()).get("product_id", 0))
    if p is None:
        await finish(state)
        return
    try:
        qty = parse_qty(message.text, p.unit_type)
    except InputError as e:
        text, kb = _income_qty_prompt(p)
        await render_input(message, state, with_error(text, str(e)), kb)
        return
    await state.update_data(qty=qty)
    await state.set_state(Income.comment)
    await render_input(message, state, *_income_comment_prompt(p, qty))


async def _save_income(
    session: AsyncSession, state: FSMContext, user: User, comment: str | None, photo: str | None
) -> str:
    data = await state.get_data()
    p = await crud.get_product(session, data.get("product_id", 0))
    if p is None or "qty" not in data:
        await finish(state)
        return EXPIRED
    qty = data["qty"]
    await move(session, p.id, qty, Move.INCOME, user_id=user.id, comment=comment, photo_file_id=photo)
    await session.commit()
    await session.refresh(p)
    audit(user, "income", product=p.id, name=p.name, qty=qty, comment=comment, photo=bool(photo))
    await finish(state)
    return (
        f"✅ Приход записан: {p.name} {fmt_qty_signed(qty, p.unit_type)}\n"
        f"Остаток: {fmt_qty(p.stock, p.unit_type)}"
        + (f"\nКомментарий: {comment}" if comment else "")
        + ("\n📎 Фото накладной сохранено" if photo else "")
    )


def _after_income_kb() -> InlineKeyboardMarkup:
    return markup([[btn("➕ Ещё приход", "inc_restart"), close_btn()]])


@router.callback_query(Income.comment, CB.filter(F.a == "inc_skip"))
async def cb_income_skip(callback: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    await render(callback, state, await _save_income(session, state, user, None, None), _after_income_kb())


@router.message(Income.comment, F.photo)
async def income_photo(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    comment = (message.caption or "").strip()[:500] or None
    text = await _save_income(session, state, user, comment, message.photo[-1].file_id)
    await render_input(message, state, text, _after_income_kb())


@router.message(Income.comment, TEXT_INPUT)
async def income_comment(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    text = await _save_income(session, state, user, message.text.strip()[:500], None)
    await render_input(message, state, text, _after_income_kb())


# --- ➖ Списание ------------------------------------------------------------------------

WO_TITLE = "➖ Списание"


def _wo_qty_prompt(p: Product) -> tuple[str, InlineKeyboardMarkup]:
    return (
        f"{WO_TITLE}: {p.name}\nСейчас: {fmt_qty(p.stock, p.unit_type)}\n\n"
        f"Сколько списать? ({qty_hint(p.unit_type)})",
        markup([nav_row(back_btn("wo_restart"))]),
    )


def _wo_reason_prompt(p: Product, qty: int) -> tuple[str, InlineKeyboardMarkup]:
    return (
        f"{WO_TITLE}: {p.name} −{fmt_qty(qty, p.unit_type)}\n\n"
        "Укажите причину (обязательно): испортилось, пролили, брак…",
        markup([nav_row()]),
    )


@router.message(F.text == MB.WRITEOFF)
async def writeoff_entry(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await finish(state)
    await state.set_state(Writeoff.product)
    await start_flow(message, state, *await _picker(session, "wo_prod", WO_TITLE))


@router.callback_query(CB.filter(F.a == "wo_restart"))
async def cb_wo_restart(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await finish(state)
    await state.set_state(Writeoff.product)
    await render(callback, state, *await _picker(session, "wo_prod", WO_TITLE))


@router.callback_query(Writeoff.product, CB.filter(F.a == "wo_prod"))
async def cb_wo_product(callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession) -> None:
    p = await crud.get_product(session, callback_data.id)
    if p is None:
        return
    await state.update_data(product_id=p.id)
    await state.set_state(Writeoff.qty)
    await render(callback, state, *_wo_qty_prompt(p))


@router.message(Writeoff.qty, TEXT_INPUT)
async def wo_qty(message: Message, state: FSMContext, session: AsyncSession) -> None:
    p = await crud.get_product(session, (await state.get_data()).get("product_id", 0))
    if p is None:
        await finish(state)
        return
    try:
        qty = parse_qty(message.text, p.unit_type)
    except InputError as e:
        text, kb = _wo_qty_prompt(p)
        await render_input(message, state, with_error(text, str(e)), kb)
        return
    await state.update_data(qty=qty)
    await state.set_state(Writeoff.reason)
    await render_input(message, state, *_wo_reason_prompt(p, qty))


@router.message(Writeoff.reason, TEXT_INPUT)
async def wo_reason(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    data = await state.get_data()
    p = await crud.get_product(session, data.get("product_id", 0))
    if p is None or "qty" not in data:
        await finish(state)
        await render_input(message, state, EXPIRED)
        return
    try:
        reason = clean_text(message.text, max_len=500, field="Причина")
    except InputError as e:
        text, kb = _wo_reason_prompt(p, data["qty"])
        await render_input(message, state, with_error(text, str(e)), kb)
        return
    crossed = await move(session, p.id, -data["qty"], Move.WRITEOFF, user_id=user.id, comment=reason)
    await session.commit()
    await session.refresh(p)
    audit(user, "writeoff", product=p.id, name=p.name, qty=data["qty"], reason=reason)
    await finish(state)
    warn = " ⚠️ ниже минимума" if p.stock < p.min_stock else ""
    await render_input(
        message, state,
        f"✅ Списано: {p.name} −{fmt_qty(data['qty'], p.unit_type)}\nПричина: {reason}\n"
        f"Остаток: {fmt_qty(p.stock, p.unit_type)}{warn}",
        markup([[btn("➖ Ещё списание", "wo_restart"), close_btn()]]),
    )
    if crossed:
        await notify_low_stock(message.bot, [p])
