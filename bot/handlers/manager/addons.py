"""☕ Меню → ➕ Добавки."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.callback_answer import CallbackAnswer
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.db.models import Addon, User
from bot.filters import TEXT_INPUT
from bot.keyboards.callbacks import CB
from bot.keyboards.inline import back_btn, btn, close_btn, grid, markup, nav_row, product_picker, yes_no_rows
from bot.services.audit import audit
from bot.services.formatting import InputError, clean_text, fmt_money, fmt_qty, parse_money, parse_qty, qty_hint
from bot.states import AddonAdd, AddonEdit
from bot.ui import EXPIRED, alert, finish, render, render_input, with_error

router = Router(name="manager_addons")


async def addons_view(session: AsyncSession) -> tuple[str, InlineKeyboardMarkup]:
    addons = await crud.list_addons(session, active_only=False)
    rows = [[btn("➕ Добавить добавку", "add_new")]]
    rows += grid([btn(("🙈 " if not a.is_active else "") + a.name, "add_card", a.id) for a in addons], 2)
    rows.append([back_btn("menu_root"), close_btn()])
    text = "➕ Добавки\n\nСиропы, доп. шот, альтернативное молоко и т.п."
    if not addons:
        text += "\n\nДобавок пока нет."
    return text, markup(rows)


async def addon_view(session: AsyncSession, a: Addon, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    product = await crud.get_product(session, a.product_id) if a.product_id else None
    writeoff = (
        f"{product.name} × {fmt_qty(a.product_qty, product.unit_type)}" if product else "не списывается"
    )
    text = (
        f"➕ {a.name}\n\n"
        f"Цена: {fmt_money(a.price)}\n"
        f"Списание со склада: {writeoff}\n"
        f"Статус: {'активна' if a.is_active else '🙈 скрыта'}"
    )
    if note:
        text = f"{note}\n\n{text}"
    return text, markup(
        [
            [btn("✏️ Название", "add_rename", a.id), btn("💰 Цена", "add_price", a.id)],
            [btn("📦 Списание товара", "add_link", a.id)],
            [btn("🙈 Скрыть" if a.is_active else "👁 Вернуть", "add_active", a.id)],
            [back_btn("add_list"), close_btn()],
        ]
    )


@router.callback_query(CB.filter(F.a == "add_list"))
async def cb_list(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await finish(state)
    await render(callback, state, *await addons_view(session))


async def _addon_or_alert(session: AsyncSession, addon_id: int, callback_answer: CallbackAnswer) -> Addon | None:
    a = await crud.get_addon(session, addon_id)
    if a is None:
        alert(callback_answer, "Добавка не найдена")
    return a


@router.callback_query(CB.filter(F.a == "add_card"))
async def cb_card(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession,
    callback_answer: CallbackAnswer,
) -> None:
    await finish(state)
    if a := await _addon_or_alert(session, callback_data.id, callback_answer):
        await render(callback, state, *await addon_view(session, a))


@router.callback_query(CB.filter(F.a == "add_active"))
async def cb_active(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession, user: User,
    callback_answer: CallbackAnswer,
) -> None:
    if a := await _addon_or_alert(session, callback_data.id, callback_answer):
        a.is_active = not a.is_active
        await session.commit()
        audit(user, "addon_active", id=a.id, value=a.is_active)
        await render(callback, state, *await addon_view(session, a))


# --- новая добавка ------------------------------------------------------------------

def _new_head(data: dict) -> str:
    return "➕ Новая добавка" + (f": {data['name']}" if data.get("name") else "")


@router.callback_query(CB.filter(F.a == "add_new"))
async def cb_new(callback: CallbackQuery, state: FSMContext) -> None:
    await finish(state)
    await state.set_state(AddonAdd.name)
    await render(callback, state, "➕ Новая добавка\n\nОтправьте название (например, Сироп ваниль):",
                 markup([nav_row(back_btn("add_list"))]))


@router.message(AddonAdd.name, TEXT_INPUT)
async def new_name(message: Message, state: FSMContext) -> None:
    try:
        name = clean_text(message.text)
    except InputError as e:
        await render_input(message, state, with_error("➕ Новая добавка\n\nОтправьте название:", str(e)),
                           markup([nav_row()]))
        return
    await state.update_data(name=name)
    await state.set_state(AddonAdd.price)
    await render_input(message, state, f"{_new_head({'name': name})}\n\nЦена добавки (0 — бесплатно):",
                       markup([nav_row()]))


@router.message(AddonAdd.price, TEXT_INPUT)
async def new_price(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        price = parse_money(message.text, allow_zero=True)
    except InputError as e:
        await render_input(message, state, with_error(f"{_new_head(data)}\n\nЦена добавки:", str(e)),
                           markup([nav_row()]))
        return
    await state.update_data(price=price)
    await state.set_state(AddonAdd.link)
    await render_input(message, state, f"{_new_head(data)}\n\nСписывать товар со склада при продаже?",
                       markup(yes_no_rows("aadd_link") + [nav_row()]))


async def _save_new(session: AsyncSession, state: FSMContext, user: User, product_id: int | None, qty: int) -> Addon:
    data = await state.get_data()
    addon = Addon(name=data["name"], price=data["price"], product_id=product_id, product_qty=qty)
    session.add(addon)
    await session.commit()
    audit(user, "addon_add", id=addon.id, name=addon.name, price=addon.price, product=product_id, qty=qty)
    await finish(state)
    return addon


@router.callback_query(AddonAdd.link, CB.filter(F.a == "aadd_link"))
async def cb_new_link(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession, user: User,
) -> None:
    if callback_data.v != "1":
        addon = await _save_new(session, state, user, None, 0)
        await render(callback, state, *await addon_view(session, addon, "✅ Добавка создана."))
        return
    data = await state.get_data()
    products = await crud.list_products(session)
    await state.set_state(AddonAdd.product)
    await render(callback, state, f"{_new_head(data)}\n\nКакой товар списывать?",
                 markup(product_picker(products, "aadd_prod") + [nav_row()]))


@router.callback_query(AddonAdd.product, CB.filter(F.a == "aadd_prod"))
async def cb_new_product(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession,
    callback_answer: CallbackAnswer,
) -> None:
    product = await crud.get_product(session, callback_data.id)
    if product is None:
        alert(callback_answer, "Товар не найден")
        return
    await state.update_data(product_id=product.id)
    await state.set_state(AddonAdd.qty)
    data = await state.get_data()
    await render(callback, state,
                 f"{_new_head(data)}\n\nСколько «{product.name}» списывать на 1 порцию? ({qty_hint(product.unit_type)})",
                 markup([nav_row()]))


@router.message(AddonAdd.qty, TEXT_INPUT)
async def new_qty(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    data = await state.get_data()
    product = await crud.get_product(session, data.get("product_id", 0))
    if product is None:
        await finish(state)
        await render_input(message, state, EXPIRED)
        return
    try:
        qty = parse_qty(message.text, product.unit_type)
    except InputError as e:
        await render_input(
            message, state,
            with_error(f"{_new_head(data)}\n\nСколько «{product.name}» списывать на 1 порцию?", str(e)),
            markup([nav_row()]),
        )
        return
    addon = await _save_new(session, state, user, product.id, qty)
    await render_input(message, state, *await addon_view(session, addon, "✅ Добавка создана."))


# --- редактирование -----------------------------------------------------------------

@router.callback_query(CB.filter(F.a.in_({"add_rename", "add_price"})))
async def cb_edit_field(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession,
    callback_answer: CallbackAnswer,
) -> None:
    if a := await _addon_or_alert(session, callback_data.id, callback_answer):
        await finish(state)
        is_name = callback_data.a == "add_rename"
        await state.set_state(AddonEdit.name if is_name else AddonEdit.price)
        await state.update_data(addon_id=a.id)
        prompt = "Отправьте новое название:" if is_name else "Отправьте новую цену (0 — бесплатно):"
        await render(callback, state, f"➕ {a.name}\n\n{prompt}", markup([nav_row(back_btn("add_card", a.id))]))


async def _edit_target(state: FSMContext, session: AsyncSession) -> Addon | None:
    return await crud.get_addon(session, (await state.get_data()).get("addon_id", 0))


@router.message(AddonEdit.name, TEXT_INPUT)
async def edit_name(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    a = await _edit_target(state, session)
    if a is None:
        await finish(state)
        return
    try:
        name = clean_text(message.text)
    except InputError as e:
        await render_input(message, state, with_error(f"➕ {a.name}\n\nОтправьте новое название:", str(e)),
                           markup([nav_row(back_btn("add_card", a.id))]))
        return
    old, a.name = a.name, name
    await session.commit()
    audit(user, "addon_rename", id=a.id, old=old, new=name)
    await finish(state)
    await render_input(message, state, *await addon_view(session, a, "✅ Название изменено."))


@router.message(AddonEdit.price, TEXT_INPUT)
async def edit_price(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    a = await _edit_target(state, session)
    if a is None:
        await finish(state)
        return
    try:
        price = parse_money(message.text, allow_zero=True)
    except InputError as e:
        await render_input(message, state, with_error(f"➕ {a.name}\n\nОтправьте новую цену:", str(e)),
                           markup([nav_row(back_btn("add_card", a.id))]))
        return
    old, a.price = a.price, price
    await session.commit()
    audit(user, "addon_price", id=a.id, old=old, new=price)
    await finish(state)
    await render_input(message, state, *await addon_view(session, a, "✅ Цена сохранена."))


@router.callback_query(CB.filter(F.a == "add_link"))
async def cb_edit_link(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession,
    callback_answer: CallbackAnswer,
) -> None:
    if a := await _addon_or_alert(session, callback_data.id, callback_answer):
        await finish(state)
        await state.set_state(AddonEdit.product)
        await state.update_data(addon_id=a.id)
        products = await crud.list_products(session)
        rows = product_picker(products, "aedit_prod")
        rows.append([btn("🚫 Не списывать", "aedit_prod", 0)])
        rows.append(nav_row(back_btn("add_card", a.id)))
        await render(callback, state, f"➕ {a.name}\n\nКакой товар списывать при продаже?", markup(rows))


@router.callback_query(AddonEdit.product, CB.filter(F.a == "aedit_prod"))
async def cb_edit_link_product(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession, user: User,
    callback_answer: CallbackAnswer,
) -> None:
    a = await _edit_target(state, session)
    if a is None:
        alert(callback_answer, EXPIRED)
        await finish(state)
        return
    if callback_data.id == 0:
        a.product_id, a.product_qty = None, 0
        await session.commit()
        audit(user, "addon_link", id=a.id, product=None)
        await finish(state)
        await render(callback, state, *await addon_view(session, a, "✅ Списание отключено."))
        return
    product = await crud.get_product(session, callback_data.id)
    if product is None:
        alert(callback_answer, "Товар не найден")
        return
    await state.update_data(product_id=product.id)
    await state.set_state(AddonEdit.qty)
    await render(callback, state,
                 f"➕ {a.name}\n\nСколько «{product.name}» списывать на 1 порцию? ({qty_hint(product.unit_type)})",
                 markup([nav_row(back_btn("add_card", a.id))]))


@router.message(AddonEdit.qty, TEXT_INPUT)
async def edit_link_qty(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    a = await _edit_target(state, session)
    product = await crud.get_product(session, (await state.get_data()).get("product_id", 0))
    if a is None or product is None:
        await finish(state)
        return
    try:
        qty = parse_qty(message.text, product.unit_type)
    except InputError as e:
        await render_input(message, state,
                           with_error(f"➕ {a.name}\n\nСколько «{product.name}» списывать на 1 порцию?", str(e)),
                           markup([nav_row(back_btn("add_card", a.id))]))
        return
    a.product_id, a.product_qty = product.id, qty
    await session.commit()
    audit(user, "addon_link", id=a.id, product=product.id, qty=qty)
    await finish(state)
    await render_input(message, state, *await addon_view(session, a, "✅ Списание сохранено."))
