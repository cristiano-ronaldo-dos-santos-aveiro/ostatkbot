from aiogram import Router

from bot.db.models import Role
from bot.filters import RoleFilter

from . import cancel, expense, sale, shift


def get_router() -> Router:
    router = Router(name="barista")
    router.message.filter(RoleFilter(Role.BARISTA))
    router.callback_query.filter(RoleFilter(Role.BARISTA))
    router.include_routers(shift.router, sale.router, cancel.router, expense.router)
    return router
