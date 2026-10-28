from aiogram import Router

from bot.db.models import Role
from bot.filters import RoleFilter

from . import addons, cancel_requests, expenses, menu, products, quick, reports, sales, staff, warehouse


def get_router() -> Router:
    router = Router(name="manager")
    router.message.filter(RoleFilter(Role.MANAGER))
    router.callback_query.filter(RoleFilter(Role.MANAGER))
    router.include_routers(
        products.router,
        menu.router,
        addons.router,
        quick.router,
        warehouse.router,
        staff.router,
        sales.router,
        cancel_requests.router,
        expenses.router,
        reports.router,
    )
    return router
