"""💸 Расход из наличных смены: сумма → описание → фото чека (обязательно)."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.db.models import Expense, User
from bot.filters import TEXT_INPUT
from bot.handlers.barista.guard import require_shift
from bot.keyboards.inline import cancel_kb
from bot.keyboards.reply import MB
from bot.services.audit import audit
from bot.services.formatting import InputError, clean_text, fmt_money, fmt_time, parse_money
from bot.services.notify import notify_managers
from bot.states import ExpenseFlow
from bot.ui import finish, render_input, start_flow, with_error

router = Router(name="barista_expense")

HEAD = "💸 Расход (из наличных кассы)"
AMOUNT_PROMPT = f"{HEAD}\n\nШаг 1/3. Введите сумму:"


def _desc_prompt(amount: int) -> str:
    return f"{HEAD}: {fmt_money(amount)}\n\nШаг 2/3. На что потрачено?"


def _photo_prompt(amount: int, desc: str) -> str:
    return f"{HEAD}: {fmt_money(amount)} — {desc}\n\nШаг 3/3. Отправьте фото чека 📷 (обязательно):"


@router.message(F.text == MB.EXPENSE)
async def expense_entry(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    await finish(state)
    if await require_shift(message, session, user) is None:
        return
    await state.set_state(ExpenseFlow.amount)
    await start_flow(message, state, AMOUNT_PROMPT, cancel_kb())


@router.message(ExpenseFlow.amount, TEXT_INPUT)
async def amount_input(message: Message, state: FSMContext) -> None:
    try:
        amount = parse_money(message.text)
    except InputError as e:
        await render_input(message, state, with_error(AMOUNT_PROMPT, str(e)), cancel_kb())
        return
    await state.update_data(amount=amount)
    await state.set_state(ExpenseFlow.description)
    await render_input(message, state, _desc_prompt(amount), cancel_kb())


@router.message(ExpenseFlow.description, TEXT_INPUT)
async def description_input(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        desc = clean_text(message.text, max_len=300, field="Описание")
    except InputError as e:
        await render_input(message, state, with_error(_desc_prompt(data["amount"]), str(e)), cancel_kb())
        return
    await state.update_data(description=desc)
    await state.set_state(ExpenseFlow.photo)
    await render_input(message, state, _photo_prompt(data["amount"], desc), cancel_kb())


@router.message(ExpenseFlow.photo, F.photo)
async def photo_input(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    data = await state.get_data()
    shift = await crud.get_open_shift(session, user.id)
    if shift is None:
        await finish(state)
        await render_input(message, state, "⚠️ Смена закрыта — расход не записан.")
        return
    photo_id = message.photo[-1].file_id
    expense = Expense(
        shift_id=shift.id,
        barista_id=user.id,
        amount=data["amount"],
        description=data["description"],
        photo_file_id=photo_id,
    )
    session.add(expense)
    await session.commit()
    audit(user, "expense", expense=expense.id, shift=shift.id, amount=expense.amount, description=expense.description)
    await finish(state)
    await render_input(message, state, f"✅ Расход записан: {fmt_money(expense.amount)} — {expense.description}")
    await notify_managers(
        message.bot,
        f"💸 Расход: {user.name}\nСумма: {fmt_money(expense.amount)}\nНа что: {expense.description}\n"
        f"Время: {fmt_time(expense.created_at)} (смена с {fmt_time(shift.opened_at)})",
        photo=photo_id,
    )


@router.message(ExpenseFlow.photo)
async def photo_missing(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    error = "Нужно именно фото чека (не файлом и не текстом)."
    await render_input(message, state, with_error(_photo_prompt(data["amount"], data["description"]), error), cancel_kb())
