"""🟢 Открыть смену / 🔚 Закрыть смену (пересчёт остатков и кассы).

Бариста НЕ видит ожидаемые суммы, остатки и расхождения.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.callback_answer import CallbackAnswer
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.db.models import Status, Unit, User
from bot.filters import TEXT_INPUT
from bot.handlers.barista.guard import require_shift
from bot.handlers.helpers import send_main_menu
from bot.keyboards.callbacks import CB
from bot.keyboards.inline import back_btn, btn, cancel_kb, markup, nav_row
from bot.keyboards.reply import MB
from bot.services.audit import audit
from bot.services.formatting import InputError, fmt_money, fmt_qty, fmt_time, parse_money, parse_qty, qty_hint
from bot.services.notify import notify_managers
from bot.services.reports import build_shift_report
from bot.services.shift import ShiftError, close_shift, open_shift
from bot.states import ShiftClose, ShiftOpen
from bot.ui import EXPIRED, alert, finish, render, render_input, start_flow, with_error

router = Router(name="barista_shift")


# --- открытие ----------------------------------------------------------------------

OPEN_PROMPT = "🟢 Открытие смены\n\nПересчитайте наличные в кассе и введите сумму размена (0, если пусто):"


@router.message(F.text == MB.OPEN_SHIFT)
async def open_entry(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    await finish(state)
    if await crud.get_open_shift(session, user.id):
        await send_main_menu(message, session, user, prefix="Смена уже открыта.")
        return
    await state.set_state(ShiftOpen.cash)
    await start_flow(message, state, OPEN_PROMPT, cancel_kb())


@router.message(ShiftOpen.cash, TEXT_INPUT)
async def open_cash(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    try:
        cash = parse_money(message.text, allow_zero=True)
    except InputError as e:
        await render_input(message, state, with_error(OPEN_PROMPT, str(e)), cancel_kb())
        return
    try:
        shift = await open_shift(session, user, cash)
    except ShiftError as e:
        await finish(state)
        await render_input(message, state, f"⚠️ {e}")
        await send_main_menu(message, session, user)
        return
    audit(user, "shift_open", shift=shift.id, opening_cash=cash)
    await finish(state)
    await render_input(message, state, f"✅ Смена открыта в {fmt_time(shift.opened_at)}. Размен: {fmt_money(cash)}")
    await send_main_menu(message, session, user)
    await notify_managers(
        message.bot, f"🟢 {user.name} открыл(а) смену в {fmt_time(shift.opened_at)}. Размен: {fmt_money(cash)}"
    )


# --- закрытие ------------------------------------------------------------------------

HEAD = "🔚 Закрытие смены"


async def _count_prompt(session: AsyncSession, data: dict) -> tuple[str, InlineKeyboardMarkup]:
    pids: list[int] = data["pids"]
    idx: int = data["idx"]
    p = await crud.get_product(session, pids[idx])
    how = (
        "Пересчитайте и введите количество, шт:"
        if p.unit_type == Unit.PCS
        else f"Взвесьте/измерьте и введите остаток ({qty_hint(p.unit_type)}):"
    )
    entered = data["counts"].get(str(p.id))
    prev = f"\n(введено ранее: {fmt_qty(entered, p.unit_type)})" if entered is not None else ""
    rows = [nav_row(back_btn("sc_back") if idx > 0 else None)]
    return f"{HEAD}\nШаг 1/4 — остатки: товар {idx + 1} из {len(pids)}\n\n📦 {p.name}\n{how}{prev}", markup(rows)


MONEY_PROMPTS = {
    "cash": "Шаг 2/4 — 💵 наличные.\nПересчитайте наличные в кассе и введите сумму:",
    "terminal": "Шаг 3/4 — 💳 терминал.\nВведите сумму из отчёта терминала (0, если не было):",
    "qr": "Шаг 4/4 — 📱 QR.\nВведите сумму оплат по QR из Click/Payme (0, если не было):",
}
MONEY_STATES = {"cash": ShiftClose.cash, "terminal": ShiftClose.terminal, "qr": ShiftClose.qr}


def _money_prompt(key: str) -> tuple[str, InlineKeyboardMarkup]:
    return f"{HEAD}\n{MONEY_PROMPTS[key]}", markup([nav_row(back_btn("sc_back"))])


async def _confirm_view(session: AsyncSession, data: dict) -> tuple[str, InlineKeyboardMarkup]:
    products = await crud.get_products(session, {int(k) for k in data["counts"]})
    lines = [f"{HEAD}\nПроверьте введённые данные:", ""]
    if data["pids"]:
        lines.append("📦 Остатки:")
        for pid in data["pids"]:
            p = products.get(pid)
            if p is not None and str(pid) in data["counts"]:
                lines.append(f" — {p.name}: {fmt_qty(data['counts'][str(pid)], p.unit_type)}")
        lines.append("")
    lines += [
        f"💵 Наличные: {fmt_money(data['cash'])}",
        f"💳 Терминал: {fmt_money(data['terminal'])}",
        f"📱 QR: {fmt_money(data['qr'])}",
        "",
        "Всё верно? После подтверждения смена закроется.",
    ]
    return "\n".join(lines), markup(
        [
            [btn("✅ Подтвердить и закрыть", "sc_confirm")],
            [btn("🔄 Ввести заново", "sc_restart"), back_btn("sc_back")],
            nav_row(),
        ]
    )


async def _begin(session: AsyncSession, state: FSMContext, shift_id: int) -> tuple[str, InlineKeyboardMarkup]:
    products = await crud.list_products(session)
    data = {"shift_id": shift_id, "pids": [p.id for p in products], "idx": 0, "counts": {}}
    await state.set_data(data)
    if products:
        await state.set_state(ShiftClose.count)
        return await _count_prompt(session, data)
    await state.set_state(ShiftClose.cash)
    return _money_prompt("cash")


@router.message(F.text == MB.CLOSE_SHIFT)
async def close_entry(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    await finish(state)
    shift = await require_shift(message, session, user)
    if shift is None:
        return
    text, kb = await _begin(session, state, shift.id)
    await start_flow(message, state, text, kb)


@router.callback_query(ShiftClose.confirm, CB.filter(F.a == "sc_restart"))
async def cb_restart(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    text, kb = await _begin(session, state, data["shift_id"])
    await render(callback, state, text, kb)


@router.message(ShiftClose.count, TEXT_INPUT)
async def count_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    p = await crud.get_product(session, data["pids"][data["idx"]])
    try:
        qty = parse_qty(message.text, p.unit_type, allow_zero=True)
    except InputError as e:
        text, kb = await _count_prompt(session, data)
        await render_input(message, state, with_error(text, str(e)), kb)
        return
    counts = dict(data["counts"])
    counts[str(p.id)] = qty
    idx = data["idx"] + 1
    await state.update_data(counts=counts, idx=min(idx, len(data["pids"]) - 1))
    if idx < len(data["pids"]):
        await render_input(message, state, *await _count_prompt(session, await state.get_data()))
        return
    await state.set_state(ShiftClose.cash)
    await render_input(message, state, *_money_prompt("cash"))


async def _money_input(message: Message, state: FSMContext, session: AsyncSession, key: str) -> None:
    try:
        value = parse_money(message.text, allow_zero=True)
    except InputError as e:
        text, kb = _money_prompt(key)
        await render_input(message, state, with_error(text, str(e)), kb)
        return
    await state.update_data({key: value})
    order = ["cash", "terminal", "qr"]
    if key != "qr":
        nxt = order[order.index(key) + 1]
        await state.set_state(MONEY_STATES[nxt])
        await render_input(message, state, *_money_prompt(nxt))
        return
    await state.set_state(ShiftClose.confirm)
    await render_input(message, state, *await _confirm_view(session, await state.get_data()))


@router.message(ShiftClose.cash, TEXT_INPUT)
async def cash_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await _money_input(message, state, session, "cash")


@router.message(ShiftClose.terminal, TEXT_INPUT)
async def terminal_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await _money_input(message, state, session, "terminal")


@router.message(ShiftClose.qr, TEXT_INPUT)
async def qr_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await _money_input(message, state, session, "qr")


@router.callback_query(CB.filter(F.a == "sc_back"))
async def cb_back(callback: CallbackQuery, state: FSMContext, session: AsyncSession, callback_answer: CallbackAnswer) -> None:
    current = await state.get_state()
    data = await state.get_data()
    if current is None or "pids" not in data:
        alert(callback_answer, EXPIRED)
        return
    if current == ShiftClose.count.state:
        await state.update_data(idx=max(0, data["idx"] - 1))
        await render(callback, state, *await _count_prompt(session, await state.get_data()))
    elif current == ShiftClose.cash.state:
        if data["pids"]:
            await state.set_state(ShiftClose.count)
            await state.update_data(idx=len(data["pids"]) - 1)
            await render(callback, state, *await _count_prompt(session, await state.get_data()))
        else:
            alert(callback_answer, "Это первый шаг")
    elif current == ShiftClose.terminal.state:
        await state.set_state(ShiftClose.cash)
        await render(callback, state, *_money_prompt("cash"))
    elif current == ShiftClose.qr.state:
        await state.set_state(ShiftClose.terminal)
        await render(callback, state, *_money_prompt("terminal"))
    elif current == ShiftClose.confirm.state:
        await state.set_state(ShiftClose.qr)
        await render(callback, state, *_money_prompt("qr"))


@router.callback_query(ShiftClose.confirm, CB.filter(F.a == "sc_confirm"))
async def cb_confirm(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, user: User,
    callback_answer: CallbackAnswer,
) -> None:
    data = await state.get_data()
    shift = await crud.get_shift(session, data.get("shift_id", 0))
    if shift is None or shift.barista_id != user.id or shift.status != Status.OPEN:
        await finish(state)
        alert(callback_answer, "Смена уже закрыта или не найдена")
        return
    counts = {int(k): v for k, v in data["counts"].items()}
    try:
        await close_shift(session, shift, counts, data["cash"], data["terminal"], data["qr"])
    except ShiftError as e:
        await finish(state)
        alert(callback_answer, str(e))
        return
    audit(user, "shift_close", shift=shift.id, cash=data["cash"], terminal=data["terminal"], qr=data["qr"],
          counts=len(counts))
    await finish(state)
    await render(callback, state, f"Смена закрыта ✅\n{fmt_time(shift.opened_at)}–{fmt_time(shift.closed_at)}. Спасибо!")
    if callback.message:
        await send_main_menu(callback.message, session, user)
    report = await build_shift_report(session, shift)
    await notify_managers(callback.bot, report)
