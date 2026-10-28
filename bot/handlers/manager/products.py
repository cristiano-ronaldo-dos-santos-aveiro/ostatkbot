"""⚙️ Товары: добавление, редактирование, скрытие."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.callback_answer import CallbackAnswer
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.db.models import Product, Unit, User
from bot.filters import TEXT_INPUT
from bot.keyboards.callbacks import CB
from bot.keyboards.inline import back_btn, btn, close_btn, markup, nav_row
from bot.keyboards.reply import MB
from bot.services.audit import audit
from bot.services.formatting import (
    UNIT_SHORT,
    UNIT_TITLE,
    InputError,
    clean_text,
    fmt_money,
    fmt_qty,
    parse_money,
    parse_qty,
    qty_hint,
)
from bot.states import ProductAdd, ProductEdit
from bot.ui import alert, finish, render, render_input, start_flow, with_error

router = Router(name="manager_products")


# --- представления ---------------------------------------------------------------

async def products_view(session: AsyncSession) -> tuple[str, InlineKeyboardMarkup]:
    products = await crud.list_products(session, active_only=False)
    rows = [[btn("➕ Добавить товар", "prod_add")]]
    for p in products:
        mark = "🙈 " if not p.is_active else ("🛒 " if p.is_sellable else "")
        rows.append([btn(f"{mark}{p.name} · {UNIT_SHORT[p.unit_type]}", "prod_card", p.id)])
    rows.append([close_btn()])
    text = "⚙️ Товары\n\n🛒 — продаётся отдельно, 🙈 — скрыт.\nВыберите товар или добавьте новый."
    if not products:
        text = "⚙️ Товары\n\nТоваров пока нет. Добавьте первый (стаканы, кофе, молоко…)."
    return text, markup(rows)


def card_text(p: Product, note: str = "") -> str:
    lines = [
        f"📦 {p.name}",
        f"Тип: {UNIT_TITLE[p.unit_type]}",
        f"Продаётся отдельно: {'да' if p.is_sellable else 'нет'}",
        f"Цена: {fmt_money(p.price) if p.price is not None else '—'}",
        f"Мин. остаток: {fmt_qty(p.min_stock, p.unit_type)}",
        f"Остаток: {fmt_qty(p.stock, p.unit_type)}" + (" ⚠️" if p.stock < p.min_stock else ""),
        f"Статус: {'активен' if p.is_active else '🙈 скрыт'}",
    ]
    text = "\n".join(lines)
    return f"{note}\n\n{text}" if note else text


def card_kb(p: Product) -> InlineKeyboardMarkup:
    return markup(
        [
            [btn("✏️ Название", "prod_edit", p.id, "name"), btn("💰 Цена", "prod_edit", p.id, "price")],
            [
                btn("📉 Мин. остаток", "prod_edit", p.id, "min"),
                btn(f"🛒 Продажа: {'вкл' if p.is_sellable else 'выкл'}", "prod_sell", p.id),
            ],
            [btn("🙈 Скрыть" if p.is_active else "👁 Вернуть", "prod_active", p.id)],
            [back_btn("prod_list"), close_btn()],
        ]
    )


@router.message(F.text == MB.PRODUCTS)
async def products_entry(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await finish(state)
    text, kb = await products_view(session)
    await start_flow(message, state, text, kb)


@router.callback_query(CB.filter(F.a == "prod_list"))
async def cb_list(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await finish(state)
    text, kb = await products_view(session)
    await render(callback, state, text, kb)


@router.callback_query(CB.filter(F.a == "prod_card"))
async def cb_card(
    callback: CallbackQuery,
    callback_data: CB,
    state: FSMContext,
    session: AsyncSession,
    callback_answer: CallbackAnswer,
) -> None:
    await finish(state)
    p = await crud.get_product(session, callback_data.id)
    if p is None:
        alert(callback_answer, "Товар не найден")
        return
    await render(callback, state, card_text(p), card_kb(p))


# --- добавление -------------------------------------------------------------------

def _add_prompt(step: str, data: dict) -> tuple[str, InlineKeyboardMarkup]:
    head = "➕ Новый товар"
    if data.get("name"):
        head += f": {data['name']}"
    if step == "unit":
        return f"{head}\n\nШаг 1/5. Выберите тип товара:", markup(
            [
                [btn("Штучный", "padd_unit", v=Unit.PCS)],
                [btn("Граммовый", "padd_unit", v=Unit.G)],
                [btn("Миллилитры", "padd_unit", v=Unit.ML)],
                nav_row(back_btn("prod_list")),
            ]
        )
    if step == "name":
        return f"{head}\n\nШаг 2/5. Отправьте название товара:", markup([nav_row()])
    if step == "sellable":
        return (
            f"{head}\n\nШаг 3/5. Это товар для продажи или расходник?\n\n"
            "🛒 Для продажи — покупатель берёт его отдельно: круассан, вода, шоколад.\n"
            "📦 Расходник — уходит в напитки: стаканы, крышки, кофе, молоко, сиропы.",
            markup(
                [
                    [btn("🛒 Продаётся отдельно", "padd_sell", v="1")],
                    [btn("📦 Расходник", "padd_sell", v="0")],
                    nav_row(),
                ]
            ),
        )
    if step == "price":
        return f"{head}\n\nШаг 4/5. Цена продажи за 1 шт:", markup([nav_row()])
    unit = data["unit"]
    return (
        f"{head}\n\nШаг 5/5. Минимальный остаток ({qty_hint(unit)}).\n"
        "Когда остаток опустится ниже — придёт уведомление. 0 — не следить.",
        markup([nav_row()]),
    )


@router.callback_query(CB.filter(F.a == "prod_add"))
async def cb_add(callback: CallbackQuery, state: FSMContext) -> None:
    await finish(state)
    await state.set_state(ProductAdd.unit)
    await render(callback, state, *_add_prompt("unit", {}))


@router.callback_query(ProductAdd.unit, CB.filter(F.a == "padd_unit"))
async def cb_add_unit(callback: CallbackQuery, callback_data: CB, state: FSMContext) -> None:
    if callback_data.v not in Unit.ALL:
        return
    await state.update_data(unit=callback_data.v)
    await state.set_state(ProductAdd.name)
    await render(callback, state, *_add_prompt("name", await state.get_data()))


@router.message(ProductAdd.name, TEXT_INPUT)
async def add_name(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    try:
        name = clean_text(message.text)
        if await crud.product_name_taken(session, name):
            raise InputError("Товар с таким названием уже есть.")
    except InputError as e:
        text, kb = _add_prompt("name", data)
        await render_input(message, state, with_error(text, str(e)), kb)
        return
    await state.update_data(name=name)
    await state.set_state(ProductAdd.sellable)
    await render_input(message, state, *_add_prompt("sellable", await state.get_data()))


@router.callback_query(ProductAdd.sellable, CB.filter(F.a == "padd_sell"))
async def cb_add_sellable(callback: CallbackQuery, callback_data: CB, state: FSMContext) -> None:
    sellable = callback_data.v == "1"
    await state.update_data(sellable=sellable, price=None)
    await state.set_state(ProductAdd.price if sellable else ProductAdd.min_stock)
    await render(callback, state, *_add_prompt("price" if sellable else "min", await state.get_data()))


@router.message(ProductAdd.price, TEXT_INPUT)
async def add_price(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        price = parse_money(message.text)
    except InputError as e:
        text, kb = _add_prompt("price", data)
        await render_input(message, state, with_error(text, str(e)), kb)
        return
    await state.update_data(price=price)
    await state.set_state(ProductAdd.min_stock)
    await render_input(message, state, *_add_prompt("min", await state.get_data()))


@router.message(ProductAdd.min_stock, TEXT_INPUT)
async def add_min_stock(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    data = await state.get_data()
    try:
        min_stock = parse_qty(message.text, data["unit"], allow_zero=True)
    except InputError as e:
        text, kb = _add_prompt("min", data)
        await render_input(message, state, with_error(text, str(e)), kb)
        return
    product = Product(
        name=data["name"],
        unit_type=data["unit"],
        is_sellable=data["sellable"],
        price=data.get("price"),
        min_stock=min_stock,
        stock=0,
    )
    session.add(product)
    await session.commit()
    audit(user, "product_add", id=product.id, name=product.name, unit=product.unit_type,
          sellable=product.is_sellable, price=product.price, min_stock=min_stock)
    await finish(state)
    await render_input(
        message, state, card_text(product, "✅ Товар добавлен. Остаток пополняется через «➕ Приход»."),
        card_kb(product),
    )


# --- редактирование ---------------------------------------------------------------

_EDIT_STATES = {"name": ProductEdit.name, "price": ProductEdit.price, "min": ProductEdit.min_stock}


def _edit_prompt(field: str, p: Product) -> tuple[str, InlineKeyboardMarkup]:
    kb = markup([nav_row(back_btn("prod_card", p.id))])
    if field == "name":
        return f"✏️ {p.name}\n\nОтправьте новое название:", kb
    if field == "price":
        return f"💰 {p.name}\n\nОтправьте цену продажи за 1 шт:", kb
    return f"📉 {p.name}\n\nОтправьте минимальный остаток ({qty_hint(p.unit_type)}):", kb


@router.callback_query(CB.filter(F.a == "prod_edit"))
async def cb_edit(
    callback: CallbackQuery,
    callback_data: CB,
    state: FSMContext,
    session: AsyncSession,
    callback_answer: CallbackAnswer,
) -> None:
    p = await crud.get_product(session, callback_data.id)
    if p is None or callback_data.v not in _EDIT_STATES:
        alert(callback_answer, "Товар не найден")
        return
    await finish(state)
    await state.set_state(_EDIT_STATES[callback_data.v])
    await state.update_data(product_id=p.id, make_sellable=False)
    await render(callback, state, *_edit_prompt(callback_data.v, p))


async def _edit_target(state: FSMContext, session: AsyncSession) -> Product | None:
    data = await state.get_data()
    return await crud.get_product(session, data.get("product_id", 0))


@router.message(ProductEdit.name, TEXT_INPUT)
async def edit_name(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    p = await _edit_target(state, session)
    if p is None:
        await finish(state)
        return
    try:
        name = clean_text(message.text)
        if await crud.product_name_taken(session, name, exclude_id=p.id):
            raise InputError("Товар с таким названием уже есть.")
    except InputError as e:
        text, kb = _edit_prompt("name", p)
        await render_input(message, state, with_error(text, str(e)), kb)
        return
    old, p.name = p.name, name
    await session.commit()
    audit(user, "product_rename", id=p.id, old=old, new=name)
    await finish(state)
    await render_input(message, state, card_text(p, "✅ Название изменено."), card_kb(p))


@router.message(ProductEdit.price, TEXT_INPUT)
async def edit_price(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    p = await _edit_target(state, session)
    if p is None:
        await finish(state)
        return
    try:
        price = parse_money(message.text)
    except InputError as e:
        text, kb = _edit_prompt("price", p)
        await render_input(message, state, with_error(text, str(e)), kb)
        return
    old, p.price = p.price, price
    if (await state.get_data()).get("make_sellable"):
        p.is_sellable = True
    await session.commit()
    audit(user, "product_price", id=p.id, old=old, new=price, sellable=p.is_sellable)
    await finish(state)
    await render_input(message, state, card_text(p, "✅ Цена сохранена."), card_kb(p))


@router.message(ProductEdit.min_stock, TEXT_INPUT)
async def edit_min(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    p = await _edit_target(state, session)
    if p is None:
        await finish(state)
        return
    try:
        value = parse_qty(message.text, p.unit_type, allow_zero=True)
    except InputError as e:
        text, kb = _edit_prompt("min", p)
        await render_input(message, state, with_error(text, str(e)), kb)
        return
    old, p.min_stock = p.min_stock, value
    await session.commit()
    audit(user, "product_min_stock", id=p.id, old=old, new=value)
    await finish(state)
    await render_input(message, state, card_text(p, "✅ Мин. остаток сохранён."), card_kb(p))


@router.callback_query(CB.filter(F.a == "prod_sell"))
async def cb_toggle_sellable(
    callback: CallbackQuery,
    callback_data: CB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    callback_answer: CallbackAnswer,
) -> None:
    p = await crud.get_product(session, callback_data.id)
    if p is None:
        alert(callback_answer, "Товар не найден")
        return
    await finish(state)
    if not p.is_sellable and p.price is None:
        # сначала нужна цена
        await state.set_state(ProductEdit.price)
        await state.update_data(product_id=p.id, make_sellable=True)
        await render(callback, state, *_edit_prompt("price", p))
        return
    p.is_sellable = not p.is_sellable
    await session.commit()
    audit(user, "product_sellable", id=p.id, value=p.is_sellable)
    await render(callback, state, card_text(p), card_kb(p))


@router.callback_query(CB.filter(F.a == "prod_active"))
async def cb_toggle_active(
    callback: CallbackQuery,
    callback_data: CB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    callback_answer: CallbackAnswer,
) -> None:
    p = await crud.get_product(session, callback_data.id)
    if p is None:
        alert(callback_answer, "Товар не найден")
        return
    if not p.is_active and await crud.product_name_taken(session, p.name, exclude_id=p.id):
        alert(callback_answer, "Есть активный товар с таким же названием — сначала переименуйте.")
        return
    p.is_active = not p.is_active
    await session.commit()
    audit(user, "product_active", id=p.id, value=p.is_active)
    note = "👁 Товар снова активен." if p.is_active else "🙈 Товар скрыт: не участвует в продажах, приходе и пересчёте."
    await render(callback, state, card_text(p, note), card_kb(p))
