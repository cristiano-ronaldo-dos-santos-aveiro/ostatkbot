"""Общие для хендлеров функции."""
from __future__ import annotations

from aiogram.types import Message, ReplyKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.db.models import Role, User
from bot.keyboards.reply import barista_menu, manager_menu
from bot.services.formatting import fmt_money, fmt_time


async def main_menu(session: AsyncSession, user: User) -> tuple[str, ReplyKeyboardMarkup]:
    if user.role == Role.MANAGER:
        pending = len(await crud.list_pending_requests(session))
        text = f"👋 {user.name}, главное меню менеджера."
        if pending:
            text += f"\n⏳ Запросов на отмену: {pending} (📜 Продажи)"
        return text, manager_menu()
    shift = await crud.get_open_shift(session, user.id)
    if shift:
        text = (
            f"👋 {user.name}, смена открыта с {fmt_time(shift.opened_at)}, "
            f"размен {fmt_money(shift.opening_cash)}."
        )
    else:
        text = f"👋 {user.name}, смена не открыта. Нажмите «🟢 Открыть смену»."
    return text, barista_menu(shift is not None)


async def send_main_menu(message: Message, session: AsyncSession, user: User, prefix: str = "") -> None:
    text, kb = await main_menu(session, user)
    await message.answer(f"{prefix}\n\n{text}" if prefix else text, reply_markup=kb)
