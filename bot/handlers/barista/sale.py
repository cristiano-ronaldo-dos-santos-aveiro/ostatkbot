"""🛒 Продажа (одно сообщение, inline-шаги) и ⭐ Быстрые продажи."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.callback_answer import CallbackAnswer
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.db.models import SaleType, User
from bot.filters import TEXT_INPUT
from bot.handlers.barista.guard import require_shift
from bot.keyboards.callbacks import CB
from bot.keyboards.inline import (
    addons_rows,
    back_btn,
    btn,
    close_btn,
    drinks_rows,
    grid,
    markup,
    nav_row,
    pay_rows,
    qty_rows,
    sizes_rows,
)
from bot.keyboards.reply import MB
from bot.services.audit import audit
from bot.services.formatting import InputError, fmt_money, parse_qty
from bot.services.notify import notify_low_stock
from bot.services.sales import SaleError, SaleSpec, create_sale, preview, quick_spec, sale_text
from bot.states import SaleFlow
from bot.ui import EXPIRED, alert, finish, render, render_input, start_flow, with_error

router = Router(name="barista_sale")

HEAD = "🛒 Новая продажа"
IN_SALE = StateFilter(SaleFlow)


def kind_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    text = f"{HEAD}\n\nЧто продаём?"
    if note:
        text = f"{note}\n\n{text}"
    return text, markup([[btn("☕ Напитки", "s_drinks"), btn("🥪 Товары", "s_products")], nav_row()])


@router.message(F.text == MB.SALE)
async def sale_entry(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    await finish(state)
    if await require_shift(message, session, user) is None:
        return
    await state.set_state(SaleFlow.kind)
    await start_flow(message, state, *kind_view())


@router.callback_query(CB.filter(F.a == "s_new"))
async def cb_new(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, user: User,
    callback_answer: CallbackAnswer,
) -> None:
    await finish(state)
    if await crud.get_open_shift(session, user.id) is None:
        alert(callback_answer, "Смена не открыта")
        return
    await state.set_state(SaleFlow.kind)
    await render(callback, state, *kind_view())


@router.callback_query(IN_SALE, CB.filter(F.a == "s_drinks"))
async def cb_drinks(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, callback_answer: CallbackAnswer,
) -> None:
    drinks = await crud.list_sellable_drinks(session)
    if not drinks:
        alert(callback_answer, "В меню нет напитков — обратитесь к менеджеру")
        return
    await state.set_state(SaleFlow.item)
    await render(callback, state, f"{HEAD}\n\n☕ Выберите напиток:",
                 markup(drinks_rows(drinks, "s_drink") + [nav_row(back_btn("s_new"))]))


@router.callback_query(IN_SALE, CB.filter(F.a == "s_products"))
async def cb_products(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, callback_answer: CallbackAnswer,
) -> None:
    products = [p for p in await crud.list_products(session, sellable=True) if p.price is not None]
    if not products:
        alert(callback_answer, "Нет товаров для продажи — обратитесь к менеджеру")
        return
    rows = grid([btn(f"{p.name} — {fmt_money(p.price)}", "s_prod", p.id) for p in products], 1)
    await state.set_state(SaleFlow.item)
    await render(callback, state, f"{HEAD}\n\n🥪 Выберите товар:", markup(rows + [nav_row(back_btn("s_new"))]))


@router.callback_query(IN_SALE, CB.filter(F.a == "s_drink"))
async def cb_drink(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession,
    callback_answer: CallbackAnswer,
) -> None:
    drink = await crud.get_drink(session, callback_data.id)
    sizes = await crud.list_sizes(session, callback_data.id)
    if drink is None or not sizes:
        alert(callback_answer, "Напиток недоступен")
        return
    await state.update_data(drink_id=drink.id)
    await state.set_state(SaleFlow.size)
    await render(callback, state, f"{HEAD}\n\n☕ {drink.name} — размер:",
                 markup(sizes_rows(sizes, "s_size") + [nav_row(back_btn("s_drinks"))]))


async def _summary(session: AsyncSession, data: dict) -> str:
    spec = SaleSpec(data["kind"], data["item_id"], list(data.get("addons", [])), data.get("qty", 1))
    pv = await preview(session, spec)
    return f"{pv.title} — {fmt_money(pv.unit_price)}"


async def _addons_view(session: AsyncSession, data: dict) -> tuple[str, InlineKeyboardMarkup]:
    addons = await crud.list_addons(session)
    rows = addons_rows(addons, data.get("addons", []), "s_addon", "s_addons_done")
    rows.append(nav_row(back_btn("s_drink", data["drink_id"])))
    return f"{HEAD}\n\n{await _summary(session, data)}\n\nДобавки (можно несколько):", markup(rows)


async def _qty_view(session: AsyncSession, data: dict) -> tuple[str, InlineKeyboardMarkup]:
    rows = qty_rows("s_qty", "s_qty_input") + [nav_row(back_btn("s_new"))]
    return f"{HEAD}\n\n{await _summary(session, data)}\n\nКоличество:", markup(rows)


async def _pay_view(session: AsyncSession, data: dict) -> tuple[str, InlineKeyboardMarkup]:
    spec = SaleSpec(data["kind"], data["item_id"], list(data.get("addons", [])), data["qty"])
    pv = await preview(session, spec)
    qty = f" ×{spec.qty}" if spec.qty > 1 else ""
    return (
        f"{HEAD}\n\n{pv.title}{qty}\n💰 К оплате: {fmt_money(pv.amount(spec.qty))}\n\nОплата:",
        markup(pay_rows("s_pay") + [nav_row(back_btn("s_new"))]),
    )


async def _show(callback: CallbackQuery, state: FSMContext, callback_answer: CallbackAnswer, view) -> None:
    """Рисует шаг; если позиция стала недоступна — сообщает и возвращает в начало."""
    try:
        text, kb = await view
    except SaleError as e:
        alert(callback_answer, str(e))
        await state.set_state(SaleFlow.kind)
        await render(callback, state, *kind_view())
        return
    await render(callback, state, text, kb)


@router.callback_query(IN_SALE, CB.filter(F.a == "s_size"))
async def cb_size(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession,
    callback_answer: CallbackAnswer,
) -> None:
    await state.update_data(kind=SaleType.DRINK, item_id=callback_data.id, addons=[], qty=1)
    data = await state.get_data()
    if await crud.list_addons(session):
        await state.set_state(SaleFlow.addons)
        await _show(callback, state, callback_answer, _addons_view(session, data))
    else:
        await state.set_state(SaleFlow.qty)
        await _show(callback, state, callback_answer, _qty_view(session, data))


@router.callback_query(SaleFlow.addons, CB.filter(F.a == "s_addon"))
async def cb_addon(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession,
    callback_answer: CallbackAnswer,
) -> None:
    selected = list((await state.get_data()).get("addons", []))
    if callback_data.id in selected:
        selected.remove(callback_data.id)
    else:
        selected.append(callback_data.id)
    await state.update_data(addons=selected)
    await _show(callback, state, callback_answer, _addons_view(session, await state.get_data()))


@router.callback_query(SaleFlow.addons, CB.filter(F.a == "s_addons_done"))
async def cb_addons_done(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, callback_answer: CallbackAnswer,
) -> None:
    await state.set_state(SaleFlow.qty)
    await _show(callback, state, callback_answer, _qty_view(session, await state.get_data()))


@router.callback_query(IN_SALE, CB.filter(F.a == "s_prod"))
async def cb_product(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession,
    callback_answer: CallbackAnswer,
) -> None:
    await state.update_data(kind=SaleType.PRODUCT, item_id=callback_data.id, addons=[], qty=1)
    await state.set_state(SaleFlow.qty)
    await _show(callback, state, callback_answer, _qty_view(session, await state.get_data()))


@router.callback_query(SaleFlow.qty, CB.filter(F.a == "s_qty"))
async def cb_qty(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession,
    callback_answer: CallbackAnswer,
) -> None:
    await state.update_data(qty=max(1, callback_data.id))
    await state.set_state(SaleFlow.payment)
    await _show(callback, state, callback_answer, _pay_view(session, await state.get_data()))


QTY_PROMPT = "Введите количество (целое число):"


@router.callback_query(SaleFlow.qty, CB.filter(F.a == "s_qty_input"))
async def cb_qty_input(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.set_state(SaleFlow.qty_input)
    data = await state.get_data()
    await render(callback, state, f"{HEAD}\n\n{await _summary(session, data)}\n\n{QTY_PROMPT}",
                 markup([nav_row(back_btn("s_new"))]))


@router.message(SaleFlow.qty_input, TEXT_INPUT)
async def qty_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    try:
        qty = parse_qty(message.text, "pcs")
        if qty > 100:
            raise InputError("Слишком много за одну продажу (максимум 100).")
    except InputError as e:
        await render_input(message, state, with_error(f"{HEAD}\n\n{QTY_PROMPT}", str(e)),
                           markup([nav_row(back_btn("s_new"))]))
        return
    await state.update_data(qty=qty)
    await state.set_state(SaleFlow.payment)
    try:
        text, kb = await _pay_view(session, await state.get_data())
    except SaleError as e:
        await state.set_state(SaleFlow.kind)
        text, kb = kind_view(f"⚠️ {e}")
    await render_input(message, state, text, kb)


@router.callback_query(SaleFlow.payment, CB.filter(F.a == "s_pay"))
async def cb_pay(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession, user: User,
    callback_answer: CallbackAnswer,
) -> None:
    data = await state.get_data()
    if "item_id" not in data:
        alert(callback_answer, EXPIRED)
        await finish(state)
        return
    shift = await crud.get_open_shift(session, user.id)
    if shift is None:
        alert(callback_answer, "Смена не открыта")
        await finish(state)
        return
    spec = SaleSpec(data["kind"], data["item_id"], list(data.get("addons", [])), data.get("qty", 1), callback_data.v)
    try:
        sale, low = await create_sale(session, user, shift, spec)
    except SaleError as e:
        alert(callback_answer, str(e))
        return
    audit(user, "sale", sale=sale.id, shift=shift.id, title=sale.title, qty=sale.qty, amount=sale.amount,
          payment=sale.payment_type)
    await finish(state)
    callback_answer.text = "Продано ✅"
    await render(callback, state, f"✅ Продано: {sale_text(sale)}",
                 markup([[btn("🛒 Новая продажа", "s_new")]]))
    await notify_low_stock(callback.bot, low)


# --- ⭐ Быстрые продажи ---------------------------------------------------------------

async def quick_kb(session: AsyncSession) -> InlineKeyboardMarkup | None:
    buttons = await crud.list_quick_buttons(session)
    if not buttons:
        return None
    return markup(grid([btn(f"⭐ {qb.title}", "qs_sell", qb.id) for qb in buttons], 2) + [[close_btn()]])


@router.message(F.text == MB.QUICK)
async def quick_entry(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    await finish(state)
    if await require_shift(message, session, user) is None:
        return
    kb = await quick_kb(session)
    if kb is None:
        await message.answer("⭐ Быстрых кнопок пока нет — менеджер настраивает их в «☕ Меню».")
        return
    await start_flow(message, state, "⭐ Быстрые продажи — одно нажатие = одна продажа:", kb)


@router.callback_query(CB.filter(F.a == "qs_sell"))
async def cb_quick_sell(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession, user: User,
    callback_answer: CallbackAnswer,
) -> None:
    shift = await crud.get_open_shift(session, user.id)
    if shift is None:
        alert(callback_answer, "Смена не открыта")
        return
    qb = await crud.get_quick_button(session, callback_data.id)
    if qb is None or not qb.is_active:
        alert(callback_answer, "Кнопка больше не активна")
        return
    try:
        sale, low = await create_sale(session, user, shift, quick_spec(qb))
    except SaleError as e:
        alert(callback_answer, str(e))
        return
    audit(user, "sale_quick", sale=sale.id, shift=shift.id, button=qb.id, title=sale.title, qty=sale.qty,
          amount=sale.amount, payment=sale.payment_type)
    callback_answer.text = "Продано ✅"
    kb = await quick_kb(session)
    await render(callback, state, f"✅ Продано: {sale_text(sale)}\n\n⭐ Быстрые продажи — нажмите для следующей:", kb)
    await notify_low_stock(callback.bot, low)
