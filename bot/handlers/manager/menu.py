"""☕ Меню: корневой экран и напитки с размерами."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.callback_answer import CallbackAnswer
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.db.models import SIZES, Drink, DrinkSize, Product, Unit, User
from bot.filters import TEXT_INPUT
from bot.keyboards.callbacks import CB
from bot.keyboards.inline import Row, back_btn, btn, close_btn, grid, markup, nav_row
from bot.keyboards.reply import MB
from bot.services.audit import audit
from bot.services.formatting import InputError, clean_text, fmt_money, parse_money
from bot.states import DrinkAdd, DrinkEdit
from bot.ui import EXPIRED, alert, finish, render, render_input, start_flow, with_error

router = Router(name="manager_menu")


def menu_root_view() -> tuple[str, InlineKeyboardMarkup]:
    return "☕ Меню\n\nЧто настраиваем?", markup(
        [
            [btn("☕ Напитки", "drk_list"), btn("➕ Добавки", "add_list")],
            [btn("⭐ Быстрые кнопки", "qb_list")],
            [close_btn()],
        ]
    )


@router.message(F.text == MB.MENU)
async def menu_entry(message: Message, state: FSMContext) -> None:
    await finish(state)
    await start_flow(message, state, *menu_root_view())


@router.callback_query(CB.filter(F.a == "menu_root"))
async def cb_menu_root(callback: CallbackQuery, state: FSMContext) -> None:
    await finish(state)
    await render(callback, state, *menu_root_view())


# --- общие куски ------------------------------------------------------------------

def cup_rows(cups: list[Product], action: str, v: str = "") -> list[Row]:
    rows = grid([btn(p.name, action, p.id, v) for p in cups], 2)
    rows.append([btn("Без стакана", action, 0, v)])
    return rows


async def active_cups(session: AsyncSession) -> list[Product]:
    return await crud.list_products(session, unit_type=Unit.PCS)


# --- список и карточка напитка ----------------------------------------------------

async def drinks_view(session: AsyncSession) -> tuple[str, InlineKeyboardMarkup]:
    drinks = await crud.list_drinks(session, active_only=False)
    rows = [[btn("➕ Добавить напиток", "drk_add")]]
    rows += grid([btn(("🙈 " if not d.is_active else "") + d.name, "drk_card", d.id) for d in drinks], 2)
    rows.append([back_btn("menu_root"), close_btn()])
    text = "☕ Напитки\n\nВыберите напиток или добавьте новый."
    if not drinks:
        text = "☕ Напитки\n\nНапитков пока нет."
    return text, markup(rows)


async def drink_view(session: AsyncSession, drink: Drink, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    sizes = await crud.list_sizes(session, drink.id, active_only=False)
    cups = await crud.get_products(session, {s.cup_product_id for s in sizes if s.cup_product_id})
    lines = [f"☕ {drink.name}" + ("" if drink.is_active else " (🙈 скрыт)"), "", "Размеры:"]
    for s in sizes:
        cup = cups[s.cup_product_id].name if s.cup_product_id in cups else "без стакана"
        hidden = " (🙈 скрыт)" if not s.is_active else ""
        lines.append(f" {s.size} — {fmt_money(s.price)} · стакан: {cup}{hidden}")
    if not sizes:
        lines.append(" — нет (добавьте размер)")
    text = "\n".join(lines)
    if note:
        text = f"{note}\n\n{text}"
    rows: list[Row] = [[btn("✏️ Название", "drk_rename", drink.id)]]
    rows += grid([btn(f"⚙️ {s.size}", "dsz_card", s.id) for s in sizes], 3)
    if len(sizes) < len(SIZES):
        rows.append([btn("➕ Добавить размер", "drk_addsize", drink.id)])
    rows.append([btn("🙈 Скрыть напиток" if drink.is_active else "👁 Вернуть напиток", "drk_active", drink.id)])
    rows.append([back_btn("drk_list"), close_btn()])
    return text, markup(rows)


async def size_view(session: AsyncSession, size: DrinkSize, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    drink = await crud.get_drink(session, size.drink_id)
    cup = await crud.get_product(session, size.cup_product_id) if size.cup_product_id else None
    text = (
        f"☕ {drink.name if drink else '?'} {size.size}\n\n"
        f"Цена: {fmt_money(size.price)}\n"
        f"Стакан: {cup.name if cup else 'без стакана'}\n"
        f"Статус: {'активен' if size.is_active else '🙈 скрыт'}"
    )
    if note:
        text = f"{note}\n\n{text}"
    return text, markup(
        [
            [btn("💰 Цена", "dsz_price", size.id), btn("🥤 Стакан", "dsz_cup", size.id)],
            [btn("🙈 Скрыть размер" if size.is_active else "👁 Вернуть размер", "dsz_active", size.id)],
            [btn("⬅️ К напитку", "drk_card", size.drink_id), close_btn()],
        ]
    )


@router.callback_query(CB.filter(F.a == "drk_list"))
async def cb_drinks(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await finish(state)
    await render(callback, state, *await drinks_view(session))


@router.callback_query(CB.filter(F.a == "drk_card"))
async def cb_drink_card(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession,
    callback_answer: CallbackAnswer,
) -> None:
    await finish(state)
    drink = await crud.get_drink(session, callback_data.id)
    if drink is None:
        alert(callback_answer, "Напиток не найден")
        return
    await render(callback, state, *await drink_view(session, drink))


@router.callback_query(CB.filter(F.a == "drk_active"))
async def cb_drink_active(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession, user: User,
    callback_answer: CallbackAnswer,
) -> None:
    drink = await crud.get_drink(session, callback_data.id)
    if drink is None:
        alert(callback_answer, "Напиток не найден")
        return
    drink.is_active = not drink.is_active
    await session.commit()
    audit(user, "drink_active", id=drink.id, value=drink.is_active)
    await render(callback, state, *await drink_view(session, drink))


# --- добавление напитка -----------------------------------------------------------

def _sizes_kb(selected: list[str]) -> InlineKeyboardMarkup:
    return markup(
        [
            [btn(("✅ " if s in selected else "") + s, "dadd_size", v=s) for s in SIZES],
            [btn("Готово ➡️", "dadd_sizes_done")],
            nav_row(),
        ]
    )


def _price_prompt(data: dict) -> tuple[str, InlineKeyboardMarkup]:
    size = data["sizes"][data["idx"]]
    return (
        f"☕ Новый напиток: {data['name']}\n\nЦена для размера {size}:",
        markup([nav_row()]),
    )


@router.callback_query(CB.filter(F.a == "drk_add"))
async def cb_drink_add(callback: CallbackQuery, state: FSMContext) -> None:
    await finish(state)
    await state.set_state(DrinkAdd.name)
    await render(callback, state, "☕ Новый напиток\n\nОтправьте название (например, Капучино):",
                 markup([nav_row(back_btn("drk_list"))]))


@router.message(DrinkAdd.name, TEXT_INPUT)
async def drink_add_name(message: Message, state: FSMContext) -> None:
    try:
        name = clean_text(message.text)
    except InputError as e:
        await render_input(message, state, with_error("☕ Новый напиток\n\nОтправьте название:", str(e)),
                           markup([nav_row()]))
        return
    await state.update_data(name=name, sizes=[])
    await state.set_state(DrinkAdd.sizes)
    await render_input(message, state, f"☕ Новый напиток: {name}\n\nВыберите размеры (можно несколько):",
                       _sizes_kb([]))


@router.callback_query(DrinkAdd.sizes, CB.filter(F.a == "dadd_size"))
async def cb_drink_add_size(callback: CallbackQuery, callback_data: CB, state: FSMContext) -> None:
    data = await state.get_data()
    selected: list[str] = list(data.get("sizes", []))
    if callback_data.v in selected:
        selected.remove(callback_data.v)
    elif callback_data.v in SIZES:
        selected.append(callback_data.v)
    selected.sort(key=SIZES.index)
    await state.update_data(sizes=selected)
    await render(callback, state, f"☕ Новый напиток: {data['name']}\n\nВыберите размеры (можно несколько):",
                 _sizes_kb(selected))


@router.callback_query(DrinkAdd.sizes, CB.filter(F.a == "dadd_sizes_done"))
async def cb_drink_sizes_done(callback: CallbackQuery, state: FSMContext, callback_answer: CallbackAnswer) -> None:
    data = await state.get_data()
    if not data.get("sizes"):
        alert(callback_answer, "Выберите хотя бы один размер")
        return
    await state.update_data(idx=0, prices={}, cups={})
    await state.set_state(DrinkAdd.price)
    await render(callback, state, *_price_prompt(await state.get_data()))


@router.message(DrinkAdd.price, TEXT_INPUT)
async def drink_add_price(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    try:
        price = parse_money(message.text)
    except InputError as e:
        text, kb = _price_prompt(data)
        await render_input(message, state, with_error(text, str(e)), kb)
        return
    size = data["sizes"][data["idx"]]
    prices = dict(data["prices"])
    prices[size] = price
    await state.update_data(prices=prices)
    await state.set_state(DrinkAdd.cup)
    cups = await active_cups(session)
    await render_input(
        message, state,
        f"☕ Новый напиток: {data['name']}\n\nКакой стакан списывать для размера {size}?",
        markup(cup_rows(cups, "dadd_cup") + [nav_row()]),
    )


@router.callback_query(DrinkAdd.cup, CB.filter(F.a == "dadd_cup"))
async def cb_drink_add_cup(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession, user: User,
) -> None:
    data = await state.get_data()
    size = data["sizes"][data["idx"]]
    cups = dict(data["cups"])
    cups[size] = callback_data.id or None
    idx = data["idx"] + 1
    await state.update_data(cups=cups, idx=idx)
    if idx < len(data["sizes"]):
        await state.set_state(DrinkAdd.price)
        await render(callback, state, *_price_prompt(await state.get_data()))
        return
    drink = Drink(name=data["name"])
    session.add(drink)
    await session.flush()
    for s in data["sizes"]:
        session.add(DrinkSize(drink_id=drink.id, size=s, price=data["prices"][s], cup_product_id=cups[s]))
    await session.commit()
    audit(user, "drink_add", id=drink.id, name=drink.name, prices=data["prices"], cups=cups)
    await finish(state)
    await render(callback, state, *await drink_view(session, drink, "✅ Напиток добавлен."))


# --- переименование ---------------------------------------------------------------

@router.callback_query(CB.filter(F.a == "drk_rename"))
async def cb_drink_rename(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession,
    callback_answer: CallbackAnswer,
) -> None:
    drink = await crud.get_drink(session, callback_data.id)
    if drink is None:
        alert(callback_answer, "Напиток не найден")
        return
    await finish(state)
    await state.set_state(DrinkEdit.name)
    await state.update_data(drink_id=drink.id)
    await render(callback, state, f"✏️ {drink.name}\n\nОтправьте новое название:",
                 markup([nav_row(back_btn("drk_card", drink.id))]))


@router.message(DrinkEdit.name, TEXT_INPUT)
async def drink_rename(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    drink = await crud.get_drink(session, (await state.get_data()).get("drink_id", 0))
    if drink is None:
        await finish(state)
        return
    try:
        name = clean_text(message.text)
    except InputError as e:
        await render_input(message, state, with_error(f"✏️ {drink.name}\n\nОтправьте новое название:", str(e)),
                           markup([nav_row(back_btn("drk_card", drink.id))]))
        return
    old, drink.name = drink.name, name
    await session.commit()
    audit(user, "drink_rename", id=drink.id, old=old, new=name)
    await finish(state)
    await render_input(message, state, *await drink_view(session, drink, "✅ Название изменено."))


# --- размер: карточка, цена, стакан, скрытие --------------------------------------

async def _size_or_alert(session: AsyncSession, size_id: int, callback_answer: CallbackAnswer) -> DrinkSize | None:
    size = await crud.get_size(session, size_id)
    if size is None:
        alert(callback_answer, "Размер не найден")
    return size


@router.callback_query(CB.filter(F.a == "dsz_card"))
async def cb_size_card(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession,
    callback_answer: CallbackAnswer,
) -> None:
    await finish(state)
    if size := await _size_or_alert(session, callback_data.id, callback_answer):
        await render(callback, state, *await size_view(session, size))


@router.callback_query(CB.filter(F.a == "dsz_active"))
async def cb_size_active(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession, user: User,
    callback_answer: CallbackAnswer,
) -> None:
    if size := await _size_or_alert(session, callback_data.id, callback_answer):
        size.is_active = not size.is_active
        await session.commit()
        audit(user, "drink_size_active", id=size.id, value=size.is_active)
        await render(callback, state, *await size_view(session, size))


@router.callback_query(CB.filter(F.a == "dsz_price"))
async def cb_size_price(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession,
    callback_answer: CallbackAnswer,
) -> None:
    if size := await _size_or_alert(session, callback_data.id, callback_answer):
        await finish(state)
        await state.set_state(DrinkEdit.size_price)
        await state.update_data(size_id=size.id)
        await render(callback, state, f"💰 Размер {size.size}\n\nОтправьте новую цену:",
                     markup([nav_row(back_btn("dsz_card", size.id))]))


@router.message(DrinkEdit.size_price, TEXT_INPUT)
async def size_price(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    size = await crud.get_size(session, (await state.get_data()).get("size_id", 0))
    if size is None:
        await finish(state)
        return
    try:
        price = parse_money(message.text)
    except InputError as e:
        await render_input(message, state, with_error(f"💰 Размер {size.size}\n\nОтправьте новую цену:", str(e)),
                           markup([nav_row(back_btn("dsz_card", size.id))]))
        return
    old, size.price = size.price, price
    await session.commit()
    audit(user, "drink_size_price", id=size.id, old=old, new=price)
    await finish(state)
    await render_input(message, state, *await size_view(session, size, "✅ Цена сохранена."))


@router.callback_query(CB.filter(F.a == "dsz_cup"))
async def cb_size_cup(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession,
    callback_answer: CallbackAnswer,
) -> None:
    if size := await _size_or_alert(session, callback_data.id, callback_answer):
        await finish(state)
        cups = await active_cups(session)
        await render(callback, state, f"🥤 Размер {size.size}\n\nКакой стакан списывать?",
                     markup(cup_rows(cups, "dsz_setcup", str(size.id)) + [nav_row(back_btn("dsz_card", size.id), cancel=False)]))


@router.callback_query(CB.filter(F.a == "dsz_setcup"))
async def cb_size_setcup(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession, user: User,
    callback_answer: CallbackAnswer,
) -> None:
    if not callback_data.v.isdigit():
        return
    if size := await _size_or_alert(session, int(callback_data.v), callback_answer):
        size.cup_product_id = callback_data.id or None
        await session.commit()
        audit(user, "drink_size_cup", id=size.id, cup=size.cup_product_id)
        await render(callback, state, *await size_view(session, size, "✅ Стакан сохранён."))


# --- новый размер у существующего напитка ----------------------------------------

@router.callback_query(CB.filter(F.a == "drk_addsize"))
async def cb_drink_addsize(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession,
    callback_answer: CallbackAnswer,
) -> None:
    drink = await crud.get_drink(session, callback_data.id)
    if drink is None:
        alert(callback_answer, "Напиток не найден")
        return
    existing = {s.size for s in await crud.list_sizes(session, drink.id, active_only=False)}
    missing = [s for s in SIZES if s not in existing]
    if not missing:
        alert(callback_answer, "Все размеры уже есть")
        return
    await finish(state)
    await render(callback, state, f"☕ {drink.name}\n\nКакой размер добавить?",
                 markup([[btn(s, "dnew_size", drink.id, s) for s in missing],
                         nav_row(back_btn("drk_card", drink.id), cancel=False)]))


@router.callback_query(CB.filter(F.a == "dnew_size"))
async def cb_new_size(callback: CallbackQuery, callback_data: CB, state: FSMContext) -> None:
    if callback_data.v not in SIZES:
        return
    await finish(state)
    await state.set_state(DrinkEdit.new_size_price)
    await state.update_data(drink_id=callback_data.id, size=callback_data.v)
    await render(callback, state, f"Размер {callback_data.v}\n\nОтправьте цену:",
                 markup([nav_row(back_btn("drk_card", callback_data.id))]))


@router.message(DrinkEdit.new_size_price, TEXT_INPUT)
async def new_size_price(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    try:
        price = parse_money(message.text)
    except InputError as e:
        await render_input(message, state, with_error(f"Размер {data['size']}\n\nОтправьте цену:", str(e)),
                           markup([nav_row(back_btn("drk_card", data["drink_id"]))]))
        return
    await state.update_data(price=price)
    await state.set_state(DrinkEdit.new_size_cup)
    cups = await active_cups(session)
    await render_input(message, state, f"Размер {data['size']}\n\nКакой стакан списывать?",
                       markup(cup_rows(cups, "dnew_cup") + [nav_row()]))


@router.callback_query(DrinkEdit.new_size_cup, CB.filter(F.a == "dnew_cup"))
async def cb_new_size_cup(
    callback: CallbackQuery, callback_data: CB, state: FSMContext, session: AsyncSession, user: User,
    callback_answer: CallbackAnswer,
) -> None:
    data = await state.get_data()
    drink = await crud.get_drink(session, data.get("drink_id", 0))
    if drink is None or "price" not in data:
        alert(callback_answer, EXPIRED)
        await finish(state)
        return
    existing = {s.size for s in await crud.list_sizes(session, drink.id, active_only=False)}
    if data["size"] in existing:
        alert(callback_answer, "Такой размер уже есть")
        await finish(state)
        return
    size = DrinkSize(drink_id=drink.id, size=data["size"], price=data["price"],
                     cup_product_id=callback_data.id or None)
    session.add(size)
    await session.commit()
    audit(user, "drink_size_add", drink=drink.id, size=size.size, price=size.price, cup=size.cup_product_id)
    await finish(state)
    await render(callback, state, *await drink_view(session, drink, f"✅ Размер {size.size} добавлен."))
