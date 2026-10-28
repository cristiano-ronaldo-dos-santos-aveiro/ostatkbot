"""☕ Меню → ⭐ Быстрые кнопки (до 6 готовых комбинаций)."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.callback_answer import CallbackAnswer
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.db.models import QuickButton, SaleType, User
from bot.filters import TEXT_INPUT
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
    product_picker,
    qty_rows,
    sizes_rows,
)
from bot.services.audit import audit
from bot.services.formatting import PAY_LABEL, InputError, clean_text, fmt_money
from bot.services.sales import SaleError, SaleSpec, auto_quick_title, preview, quick_spec
from bot.states import QuickAdd
from bot.ui import EXPIRED, alert, finish, render, render_input, with_error

router = Router(name="manager_quick")

MAX_QUICK = 6


async def quick_view(session: AsyncSession, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    buttons = await crud.list_quick_buttons(session)
    rows = grid([btn(f"⭐ {qb.title}", "qb_card", qb.id) for qb in buttons], 2)
    if len(buttons) < MAX_QUICK:
        rows.append([btn("➕ Добавить быструю кнопку", "qb_new")])
    rows.append([back_btn("menu_root"), close_btn()])
    text = f"⭐ Быстрые кнопки ({len(buttons)}/{MAX_QUICK})\n\nПродажа готовой комбинации в одно нажатие."
    if note:
        text = f"{note}\n\n{text}"
    return text, markup(rows)


@router.callback_query(CB.filter(F.a == "qb_list"))
async def cb_list(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await finish(state)
    await render(callback, state, *await quick_view(session))


@router.callback_query(CB.filter(F.a == "qb_card"))
async def cb_card(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession,
    callback_answer: CallbackAnswer,
) -> None:
    await finish(state)
    qb = await crud.get_quick_button(session, callback_data.id)
    if qb is None or not qb.is_active:
        alert(callback_answer, "Кнопка не найдена")
        return
    spec = quick_spec(qb)
    try:
        pv = await preview(session, spec)
        what = f"{pv.title}\nКол-во: {spec.qty}\nОплата: {PAY_LABEL[spec.payment]}\nСумма: {fmt_money(pv.amount(spec.qty))}"
    except SaleError as e:
        what = f"⚠️ Сейчас не работает: {e}"
    await render(callback, state, f"⭐ {qb.title}\n\n{what}", markup(
        [[btn("🗑 Убрать кнопку", "qb_off", qb.id)], [back_btn("qb_list"), close_btn()]]
    ))


@router.callback_query(CB.filter(F.a == "qb_off"))
async def cb_off(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession, user: User,
) -> None:
    qb = await crud.get_quick_button(session, callback_data.id)
    if qb is not None and qb.is_active:
        qb.is_active = False
        await session.commit()
        audit(user, "quick_button_off", id=qb.id, title=qb.title)
    await render(callback, state, *await quick_view(session, "🗑 Кнопка убрана."))


# --- создание -------------------------------------------------------------------------

HEAD = "⭐ Новая быстрая кнопка"


@router.callback_query(CB.filter(F.a == "qb_new"))
async def cb_new(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, callback_answer: CallbackAnswer,
) -> None:
    if len(await crud.list_quick_buttons(session)) >= MAX_QUICK:
        alert(callback_answer, f"Максимум {MAX_QUICK} кнопок — уберите одну.")
        return
    await finish(state)
    await state.set_state(QuickAdd.kind)
    await render(callback, state, f"{HEAD}\n\nЧто продаём?", markup(
        [[btn("☕ Напиток", "qbn_kind", v=SaleType.DRINK), btn("🥪 Товар", "qbn_kind", v=SaleType.PRODUCT)],
         nav_row(back_btn("qb_list"))]
    ))


@router.callback_query(QuickAdd.kind, CB.filter(F.a == "qbn_kind"))
async def cb_kind(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession,
    callback_answer: CallbackAnswer,
) -> None:
    await state.update_data(kind=callback_data.v, addons=[])
    await state.set_state(QuickAdd.item)
    if callback_data.v == SaleType.DRINK:
        drinks = await crud.list_sellable_drinks(session)
        if not drinks:
            alert(callback_answer, "Нет напитков с активными размерами")
            return
        await render(callback, state, f"{HEAD}\n\nВыберите напиток:", markup(drinks_rows(drinks, "qbn_drink") + [nav_row()]))
    else:
        products = await crud.list_products(session, sellable=True)
        if not products:
            alert(callback_answer, "Нет товаров, которые продаются отдельно")
            return
        await render(callback, state, f"{HEAD}\n\nВыберите товар:", markup(product_picker(products, "qbn_prod") + [nav_row()]))


@router.callback_query(QuickAdd.item, CB.filter(F.a == "qbn_drink"))
async def cb_drink(callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession) -> None:
    sizes = await crud.list_sizes(session, callback_data.id)
    await state.set_state(QuickAdd.size)
    await render(callback, state, f"{HEAD}\n\nРазмер:", markup(sizes_rows(sizes, "qbn_size") + [nav_row()]))


async def _ask_qty(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(QuickAdd.qty)
    await render(callback, state, f"{HEAD}\n\nКоличество:", markup(qty_rows("qbn_qty") + [nav_row()]))


@router.callback_query(QuickAdd.item, CB.filter(F.a == "qbn_prod"))
async def cb_product(callback: CallbackQuery, callback_data: CB, state: FSMContext) -> None:
    await state.update_data(item_id=callback_data.id)
    await _ask_qty(callback, state)


async def _show_addons(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    addons = await crud.list_addons(session)
    if not addons:
        await _ask_qty(callback, state)
        return
    selected = (await state.get_data()).get("addons", [])
    await state.set_state(QuickAdd.addons)
    await render(callback, state, f"{HEAD}\n\nДобавки:",
                 markup(addons_rows(addons, selected, "qbn_addon", "qbn_addons_done") + [nav_row()]))


@router.callback_query(QuickAdd.size, CB.filter(F.a == "qbn_size"))
async def cb_size(callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession) -> None:
    await state.update_data(item_id=callback_data.id, addons=[])
    await _show_addons(callback, state, session)


@router.callback_query(QuickAdd.addons, CB.filter(F.a == "qbn_addon"))
async def cb_addon(callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession) -> None:
    selected = list((await state.get_data()).get("addons", []))
    if callback_data.id in selected:
        selected.remove(callback_data.id)
    else:
        selected.append(callback_data.id)
    await state.update_data(addons=selected)
    await _show_addons(callback, state, session)


@router.callback_query(QuickAdd.addons, CB.filter(F.a == "qbn_addons_done"))
async def cb_addons_done(callback: CallbackQuery, state: FSMContext) -> None:
    await _ask_qty(callback, state)


@router.callback_query(QuickAdd.qty, CB.filter(F.a == "qbn_qty"))
async def cb_qty(callback: CallbackQuery, callback_data: CB, state: FSMContext) -> None:
    await state.update_data(qty=max(1, callback_data.id))
    await state.set_state(QuickAdd.payment)
    await render(callback, state, f"{HEAD}\n\nОплата:", markup(pay_rows("qbn_pay") + [nav_row()]))


def _spec(data: dict) -> SaleSpec:
    return SaleSpec(data["kind"], data["item_id"], list(data.get("addons", [])), data["qty"], data["payment"])


@router.callback_query(QuickAdd.payment, CB.filter(F.a == "qbn_pay"))
async def cb_pay(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession,
    callback_answer: CallbackAnswer,
) -> None:
    await state.update_data(payment=callback_data.v)
    data = await state.get_data()
    try:
        spec = _spec(data)
        pv = await preview(session, spec)
    except (SaleError, KeyError) as e:
        alert(callback_answer, str(e) if isinstance(e, SaleError) else EXPIRED)
        await finish(state)
        return
    auto = auto_quick_title(pv, spec)
    await state.update_data(auto_title=auto)
    await state.set_state(QuickAdd.title)
    await render(callback, state,
                 f"{HEAD}\n\n{pv.title}, ×{spec.qty}, {PAY_LABEL[spec.payment]} — {fmt_money(pv.amount(spec.qty))}\n\n"
                 f"Отправьте название кнопки или используйте предложенное:",
                 markup([[btn(f"✅ {auto}", "qbn_title_auto")], nav_row()]))


async def _save(session: AsyncSession, state: FSMContext, user: User, title: str) -> None:
    data = await state.get_data()
    spec = _spec(data)
    qb = QuickButton(
        title=title,
        drink_size_id=spec.item_id if spec.kind == SaleType.DRINK else None,
        product_id=spec.item_id if spec.kind == SaleType.PRODUCT else None,
        addon_ids=spec.addon_ids,
        payment_type=spec.payment,
        qty=spec.qty,
    )
    session.add(qb)
    await session.commit()
    audit(user, "quick_button_add", id=qb.id, title=title)
    await finish(state)


@router.callback_query(QuickAdd.title, CB.filter(F.a == "qbn_title_auto"))
async def cb_title_auto(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, user: User,
    callback_answer: CallbackAnswer,
) -> None:
    if len(await crud.list_quick_buttons(session)) >= MAX_QUICK:
        alert(callback_answer, f"Уже {MAX_QUICK} кнопок.")
        await finish(state)
        return
    await _save(session, state, user, (await state.get_data())["auto_title"])
    await render(callback, state, *await quick_view(session, "✅ Кнопка добавлена."))


@router.message(QuickAdd.title, TEXT_INPUT)
async def title_text(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    try:
        title = clean_text(message.text, max_len=40)
    except InputError as e:
        auto = (await state.get_data())["auto_title"]
        await render_input(message, state, with_error(f"{HEAD}\n\nОтправьте название кнопки:", str(e)),
                           markup([[btn(f"✅ {auto}", "qbn_title_auto")], nav_row()]))
        return
    if len(await crud.list_quick_buttons(session)) >= MAX_QUICK:
        await finish(state)
        await render_input(message, state, f"Уже {MAX_QUICK} кнопок — новая не добавлена.")
        return
    await _save(session, state, user, title)
    await render_input(message, state, *await quick_view(session, "✅ Кнопка добавлена."))
