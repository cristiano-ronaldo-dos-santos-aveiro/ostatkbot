from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.db.models import Shift, User
from bot.handlers.helpers import send_main_menu


async def require_shift(message: Message, session: AsyncSession, user: User) -> Shift | None:
    """Открытая смена бариста; если её нет — подсказка и актуальное меню."""
    shift = await crud.get_open_shift(session, user.id)
    if shift is None:
        await send_main_menu(message, session, user, prefix="⚠️ Сначала откройте смену.")
    return shift
