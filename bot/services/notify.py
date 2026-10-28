"""Уведомления менеджерам и бариста."""
from __future__ import annotations

import logging
from collections.abc import Iterable

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup, Message

from bot.config import get_config
from bot.db.models import Product
from bot.services.formatting import fmt_qty

log = logging.getLogger(__name__)

TEXT_LIMIT = 4000
CAPTION_LIMIT = 1000


def split_text(text: str, limit: int = TEXT_LIMIT) -> list[str]:
    """Режет длинный текст по строкам на куски не длиннее limit."""
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current or not chunks:
        chunks.append(current)
    return chunks


async def send_long(bot: Bot, chat_id: int, text: str, reply_markup=None) -> Message | None:
    """Отправляет текст несколькими сообщениями; клавиатура — у последнего."""
    parts = split_text(text)
    last = None
    for i, part in enumerate(parts):
        last = await bot.send_message(
            chat_id, part, reply_markup=reply_markup if i == len(parts) - 1 else None
        )
    return last


async def notify_user(bot: Bot, telegram_id: int, text: str, reply_markup=None) -> Message | None:
    try:
        return await send_long(bot, telegram_id, text, reply_markup=reply_markup)
    except TelegramAPIError as e:
        log.warning("Не удалось отправить сообщение %s: %s", telegram_id, e)
        return None


async def notify_managers(
    bot: Bot,
    text: str,
    photo: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> list[Message]:
    sent: list[Message] = []
    for tg_id in sorted(get_config().manager_ids):
        try:
            if photo:
                caption = text if len(text) <= CAPTION_LIMIT else text[: CAPTION_LIMIT - 1] + "…"
                sent.append(
                    await bot.send_photo(tg_id, photo, caption=caption, reply_markup=reply_markup)
                )
            else:
                msg = await send_long(bot, tg_id, text, reply_markup=reply_markup)
                if msg:
                    sent.append(msg)
        except TelegramAPIError as e:
            log.warning("Не удалось уведомить менеджера %s: %s", tg_id, e)
    return sent


def low_stock_lines(products: Iterable[Product]) -> list[str]:
    return [
        f"— {p.name}: {fmt_qty(p.stock, p.unit_type)} (мин. {fmt_qty(p.min_stock, p.unit_type)})"
        for p in products
    ]


async def notify_low_stock(bot: Bot, products: list[Product]) -> None:
    if not products:
        return
    text = "⚠️ Товар ниже минимального остатка:\n" + "\n".join(low_stock_lines(products))
    await notify_managers(bot, text)
