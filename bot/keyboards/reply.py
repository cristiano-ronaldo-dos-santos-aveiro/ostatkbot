"""Главные меню (reply-клавиатуры)."""
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


class MB:
    # менеджер
    INCOME = "➕ Приход"
    WRITEOFF = "➖ Списание"
    STOCK = "📦 Склад"
    REPORTS = "📊 Отчёты"
    SALES = "📜 Продажи"
    EXPENSES = "💸 Расходы"
    PRODUCTS = "⚙️ Товары"
    MENU = "☕ Меню"
    STAFF = "👥 Бариста"
    # бариста
    OPEN_SHIFT = "🟢 Открыть смену"
    SALE = "🛒 Продажа"
    QUICK = "⭐ Быстрые продажи"
    EXPENSE = "💸 Расход"
    CANCEL_REQ = "⚠️ Запросить отмену"
    CLOSE_SHIFT = "🔚 Закрыть смену"


def _kb(rows: list[list[str]]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t) for t in row] for row in rows],
        resize_keyboard=True,
        is_persistent=True,
    )


def manager_menu() -> ReplyKeyboardMarkup:
    return _kb(
        [
            [MB.INCOME, MB.WRITEOFF],
            [MB.STOCK, MB.REPORTS],
            [MB.SALES, MB.EXPENSES],
            [MB.PRODUCTS, MB.MENU],
            [MB.STAFF],
        ]
    )


def barista_menu(has_shift: bool) -> ReplyKeyboardMarkup:
    if not has_shift:
        return _kb([[MB.OPEN_SHIFT]])
    return _kb(
        [
            [MB.SALE, MB.QUICK],
            [MB.EXPENSE, MB.CANCEL_REQ],
            [MB.CLOSE_SHIFT],
        ]
    )


MENU_TEXTS = frozenset(v for k, v in vars(MB).items() if k.isupper())
