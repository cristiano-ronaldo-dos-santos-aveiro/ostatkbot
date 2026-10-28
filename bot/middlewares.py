"""Middleware: сессия БД на каждый апдейт и проверка доступа."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.db import crud

NO_ACCESS = "⛔ Нет доступа. Обратитесь к менеджеру кофейни."


class DbSessionMiddleware(BaseMiddleware):
    def __init__(self, sessionmaker: async_sessionmaker):
        self.sessionmaker = sessionmaker

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self.sessionmaker() as session:
            data["session"] = session
            return await handler(event, data)


class AuthMiddleware(BaseMiddleware):
    """Кладёт в data["user"] пользователя с доступом; остальным — «Нет доступа».

    Исключение — /start (по нему работают пригласительные ссылки).
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            chat = event.chat
        elif isinstance(event, CallbackQuery):
            chat = event.message.chat if event.message else None
        else:
            return await handler(event, data)
        if chat is not None and chat.type != "private":
            return None  # бот работает только в личных сообщениях
        if event.from_user is None:
            return None

        user = await crud.resolve_user(data["session"], event.from_user)
        data["user"] = user
        if user is not None:
            return await handler(event, data)
        if isinstance(event, Message) and (event.text or "").startswith("/start"):
            return await handler(event, data)
        if isinstance(event, Message):
            await event.answer(NO_ACCESS)
        else:
            await event.answer(NO_ACCESS, show_alert=True)
        return None
