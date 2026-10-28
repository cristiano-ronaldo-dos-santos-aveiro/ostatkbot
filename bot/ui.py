"""Показ шагов сценария в одном сообщении (edit вместо новых сообщений)."""
from __future__ import annotations

import logging

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.callback_answer import CallbackAnswer

log = logging.getLogger(__name__)

FLOW_KEY = "_flow_msg"


def _not_modified(e: Exception) -> bool:
    return "message is not modified" in str(e)


async def safe_delete(message: Message) -> None:
    try:
        await message.delete()
    except TelegramAPIError:
        pass


async def start_flow(
    message: Message, state: FSMContext, text: str, kb: InlineKeyboardMarkup | None = None
) -> Message:
    """Новое сообщение сценария (из reply-меню)."""
    sent = await message.answer(text, reply_markup=kb)
    await state.update_data({FLOW_KEY: sent.message_id})
    return sent


async def render(
    callback: CallbackQuery,
    state: FSMContext | None,
    text: str,
    kb: InlineKeyboardMarkup | None = None,
) -> None:
    """Шаг после нажатия inline-кнопки — редактируем то же сообщение."""
    msg = callback.message
    if msg is None:  # сообщение слишком старое
        return
    if msg.text is not None:  # у фото нет текста — его не отредактировать
        try:
            await msg.edit_text(text, reply_markup=kb)
            if state is not None:
                await state.update_data({FLOW_KEY: msg.message_id})
            return
        except TelegramBadRequest as e:
            if _not_modified(e):
                return
    sent = await msg.answer(text, reply_markup=kb)
    if state is not None:
        await state.update_data({FLOW_KEY: sent.message_id})


async def render_input(
    message: Message,
    state: FSMContext,
    text: str,
    kb: InlineKeyboardMarkup | None = None,
) -> None:
    """Шаг после текстового ввода: удаляем ввод пользователя, правим сообщение сценария."""
    await safe_delete(message)
    mid = (await state.get_data()).get(FLOW_KEY)
    if mid:
        try:
            await message.bot.edit_message_text(
                text=text, chat_id=message.chat.id, message_id=mid, reply_markup=kb
            )
            return
        except TelegramBadRequest as e:
            if _not_modified(e):
                return
    sent = await message.answer(text, reply_markup=kb)
    await state.update_data({FLOW_KEY: sent.message_id})


async def finish(state: FSMContext) -> None:
    """Сбрасывает сценарий, но помнит его сообщение — чтобы финальный экран
    отредактировал его, а не прислал новое."""
    mid = (await state.get_data()).get(FLOW_KEY)
    await state.clear()
    if mid:
        await state.set_data({FLOW_KEY: mid})


def alert(callback_answer: CallbackAnswer, text: str) -> None:
    callback_answer.text = text
    callback_answer.show_alert = True


def with_error(prompt: str, error: str | None) -> str:
    return f"⚠️ {error}\n\n{prompt}" if error else prompt


EXPIRED = "Сценарий устарел — начните заново из меню."
