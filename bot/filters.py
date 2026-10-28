from aiogram import F
from aiogram.filters import Filter
from aiogram.types import TelegramObject

from bot.db.models import User
from bot.keyboards.reply import MENU_TEXTS


class RoleFilter(Filter):
    def __init__(self, role: str):
        self.role = role

    async def __call__(self, event: TelegramObject, user: User | None = None) -> bool:
        return user is not None and user.role == self.role


# Текстовый ввод в сценарии. Кнопки главного меню сюда не попадают —
# они прерывают сценарий и открывают свой раздел.
TEXT_INPUT = F.text & ~F.text.in_(MENU_TEXTS) & ~F.text.startswith("/")
