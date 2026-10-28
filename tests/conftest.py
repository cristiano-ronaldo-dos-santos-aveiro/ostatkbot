"""Тестовый стенд: настоящий Dispatcher + фейковый Telegram API."""
from __future__ import annotations

import itertools
import os
from datetime import datetime
from typing import Any

import pytest_asyncio

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ["MANAGER_IDS"] = "1"
os.environ["TIMEZONE"] = "Asia/Tashkent"
os.environ["CURRENCY"] = "сум"

from aiogram import Bot  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.methods import TelegramMethod  # noqa: E402
from aiogram.types import (  # noqa: E402
    CallbackQuery,
    Chat,
    InlineKeyboardMarkup,
    Message,
    PhotoSize,
    Update,
    User,
)

from bot.config import get_config  # noqa: E402

get_config.cache_clear()

from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402

from bot.db.base import init_db, make_engine, make_sessionmaker  # noqa: E402
from bot.middlewares import DbSessionMiddleware  # noqa: E402
from main import build_dispatcher  # noqa: E402

BOT_USER = User(id=999, is_bot=True, first_name="Bot", username="coffee_test_bot")


class FakeSession(BaseSession):
    """Запоминает все вызовы API и возвращает правдоподобные ответы."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod] = []
        self._ids = itertools.count(1000)
        # chat_id -> {message_id: (text, markup)}
        self.messages: dict[int, dict[int, tuple[str, Any]]] = {}
        self.result_ids: dict[int, int] = {}  # id(method) -> message_id

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: int | None = None) -> Any:
        self.calls.append(method)
        name = type(method).__name__
        if name == "GetMe":
            return BOT_USER
        if name in ("SendMessage", "SendPhoto"):
            mid = next(self._ids)
            text = getattr(method, "text", None) or getattr(method, "caption", None) or ""
            self.messages.setdefault(method.chat_id, {})[mid] = (text, method.reply_markup)
            self.result_ids[id(method)] = mid
            return Message(
                message_id=mid,
                date=datetime.now(),
                chat=Chat(id=method.chat_id, type="private"),
                text=text if name == "SendMessage" else None,
                caption=text if name == "SendPhoto" else None,
            )
        if name == "EditMessageText":
            chat = self.messages.setdefault(method.chat_id, {})
            chat[method.message_id] = (method.text, method.reply_markup)
            return Message(
                message_id=method.message_id,
                date=datetime.now(),
                chat=Chat(id=method.chat_id, type="private"),
                text=method.text,
            )
        if name in ("AnswerCallbackQuery", "DeleteMessage", "SetMyCommands"):
            return True
        raise NotImplementedError(name)

    async def close(self) -> None:  # pragma: no cover
        pass

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError


class Harness:
    def __init__(self, dp, bot: Bot, session: FakeSession, sessionmaker) -> None:
        self.dp, self.bot, self.api, self.sessionmaker = dp, bot, session, sessionmaker
        self._upd = itertools.count(1)
        self.flow: dict[int, int] = {}  # chat -> последнее сообщение сценария

    # --- отправка апдейтов ---
    async def send(self, uid: int, text: str | None = None, photo: bool = False, caption: str | None = None) -> None:
        n = next(self._upd)
        msg = Message(
            message_id=n,
            date=datetime.now(),
            chat=Chat(id=uid, type="private"),
            from_user=User(id=uid, is_bot=False, first_name=f"U{uid}"),
            text=text,
            caption=caption,
            photo=[PhotoSize(file_id=f"photo{n}", file_unique_id=f"u{n}", width=10, height=10)] if photo else None,
        )
        before = len(self.api.calls)
        await self.dp.feed_update(self.bot, Update(update_id=n, message=msg))
        self._track(uid, before)

    async def click(self, uid: int, label: str) -> str | None:
        """Нажимает inline-кнопку, текст которой содержит label (в последнем сообщении с клавиатурой)."""
        mid, markup = self.last_markup(uid)
        data = None
        for row in markup.inline_keyboard:
            for b in row:
                if label in b.text:
                    data = b.callback_data
                    break
            if data:
                break
        assert data, f"Кнопка «{label}» не найдена среди: {self.buttons(uid)}"
        self.last_click = (uid, mid, data)
        return await self.press(uid, mid, data)

    async def replay(self) -> str | None:
        """Повторное нажатие той же кнопки (например, на устаревшем сообщении)."""
        return await self.press(*self.last_click)

    async def press(self, uid: int, mid: int, data: str) -> str | None:
        n = next(self._upd)
        cq = CallbackQuery(
            id=str(n),
            from_user=User(id=uid, is_bot=False, first_name=f"U{uid}"),
            chat_instance="ci",
            data=data,
            message=Message(
                message_id=mid,
                date=datetime.now(),
                chat=Chat(id=uid, type="private"),
                text=self.api.messages[uid][mid][0],
            ),
        )
        before = len(self.api.calls)
        await self.dp.feed_update(self.bot, Update(update_id=n, callback_query=cq))
        self._track(uid, before)
        answers = [c for c in self.api.calls[before:] if type(c).__name__ == "AnswerCallbackQuery"]
        return answers[-1].text if answers else None

    def _track(self, uid: int, before: int) -> None:
        for c in self.api.calls[before:]:
            if getattr(c, "chat_id", None) == uid and type(c).__name__ in ("SendMessage", "EditMessageText"):
                if isinstance(c.reply_markup, InlineKeyboardMarkup):
                    mid = getattr(c, "message_id", None) or self.api.result_ids[id(c)]
                    self.flow[uid] = mid

    # --- чтение результата ---
    def last_markup(self, uid: int) -> tuple[int, InlineKeyboardMarkup]:
        mid = self.flow[uid]
        text, markup = self.api.messages[uid][mid]
        assert isinstance(markup, InlineKeyboardMarkup), f"Нет inline-клавиатуры: {text}"
        return mid, markup

    def buttons(self, uid: int) -> list[str]:
        _, markup = self.last_markup(uid)
        return [b.text for row in markup.inline_keyboard for b in row]

    def flow_text(self, uid: int) -> str:
        return self.api.messages[uid][self.flow[uid]][0]

    def texts(self, uid: int) -> list[str]:
        return [t for t, _ in self.api.messages.get(uid, {}).values()]

    def last_text(self, uid: int) -> str:
        msgs = self.api.messages.get(uid, {})
        return msgs[max(msgs)][0]


_DP = None


def _dispatcher(sm):
    """Роутеры модульные и подключаются один раз — переиспользуем Dispatcher между тестами."""
    global _DP
    if _DP is None:
        _DP = build_dispatcher(sm)
    for mw in _DP.update.outer_middleware:
        if isinstance(mw, DbSessionMiddleware):
            mw.sessionmaker = sm
    _DP.fsm.storage = MemoryStorage()
    return _DP


@pytest_asyncio.fixture
async def h(tmp_path):
    engine = make_engine(str(tmp_path / "test.db"))
    await init_db(engine)
    sm = make_sessionmaker(engine)
    dp = _dispatcher(sm)
    api = FakeSession()
    bot = Bot("123456:TEST", session=api)
    yield Harness(dp, bot, api, sm)
    await engine.dispose()
