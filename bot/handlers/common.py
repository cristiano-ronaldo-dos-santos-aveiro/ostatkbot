"""/start, отмена сценариев, служебные кнопки, fallback."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.callback_answer import CallbackAnswer
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.db.models import Role, User
from bot.handlers.helpers import send_main_menu
from bot.keyboards.callbacks import CB
from bot.middlewares import NO_ACCESS
from bot.services.audit import audit
from bot.services.notify import notify_managers
from bot.ui import safe_delete

router = Router(name="common")
fallback_router = Router(name="fallback")


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    state: FSMContext,
    user: User | None = None,
) -> None:
    await state.clear()
    payload = (command.args or "").strip()
    if payload.startswith("inv_") and (user is None or user.role != Role.MANAGER):
        new_user, error = await crud.use_invite(session, payload[4:], message.from_user)
        if new_user is None:
            if user is None:
                await message.answer(f"⛔ {error}")
                return
        else:
            user = new_user
            audit(user, "barista_registered_by_invite")
            await notify_managers(
                message.bot, f"👤 Новый бариста по приглашению: {user.name} (id {user.telegram_id})"
            )
            await send_main_menu(message, session, user, prefix="✅ Добро пожаловать в команду!")
            return
    if user is None:
        await message.answer(NO_ACCESS + f"\nВаш telegram_id: {message.from_user.id}")
        return
    await send_main_menu(message, session, user)


@router.callback_query(CB.filter(F.a == "cancel"))
async def cb_cancel(callback: CallbackQuery, state: FSMContext, callback_answer: CallbackAnswer) -> None:
    await state.clear()
    callback_answer.text = "Отменено"
    if callback.message:
        try:
            await callback.message.edit_text("❌ Отменено. Ничего не сохранено.")
        except Exception:  # noqa: BLE001 - сообщение могло быть фото/старым
            await safe_delete(callback.message)


@router.callback_query(CB.filter(F.a == "close"))
async def cb_close(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if callback.message:
        await safe_delete(callback.message)


@router.callback_query(CB.filter(F.a == "noop"))
async def cb_noop(callback: CallbackQuery) -> None:
    pass


@fallback_router.callback_query()
async def cb_unknown(callback: CallbackQuery, callback_answer: CallbackAnswer) -> None:
    callback_answer.text = "Кнопка устарела — откройте раздел заново из меню."
    callback_answer.show_alert = True


@fallback_router.message()
async def msg_unknown(
    message: Message, session: AsyncSession, state: FSMContext, user: User
) -> None:
    if await state.get_state() is not None:
        await message.answer("Сейчас ожидается другой ответ. Используйте кнопки или «❌ Отмена».")
        return
    await send_main_menu(message, session, user, prefix="Используйте кнопки меню 👇")
