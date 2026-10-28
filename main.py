"""Точка входа: python main.py"""
from __future__ import annotations

import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from aiogram.utils.callback_answer import CallbackAnswerMiddleware

from bot.config import get_config
from bot.db.base import init_db, make_engine, make_sessionmaker
from bot.handlers import barista, common, manager
from bot.middlewares import AuthMiddleware, DbSessionMiddleware


def setup_logging(log_dir: str, level: str) -> None:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)
    app_file = RotatingFileHandler(Path(log_dir) / "bot.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    app_file.setFormatter(fmt)
    root.addHandler(app_file)
    # отдельный журнал операций: кто, когда, что
    audit_file = RotatingFileHandler(Path(log_dir) / "audit.log", maxBytes=5_000_000, backupCount=20, encoding="utf-8")
    audit_file.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logging.getLogger("audit").addHandler(audit_file)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


def build_dispatcher(sessionmaker) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(DbSessionMiddleware(sessionmaker))
    dp.message.outer_middleware(AuthMiddleware())
    dp.callback_query.outer_middleware(AuthMiddleware())
    dp.callback_query.middleware(CallbackAnswerMiddleware())
    dp.include_routers(
        common.router,
        manager.get_router(),
        barista.get_router(),
        common.fallback_router,
    )
    return dp


async def main() -> None:
    config = get_config()
    setup_logging(config.log_dir, config.log_level)
    log = logging.getLogger("main")
    if not config.bot_token:
        log.error("BOT_TOKEN не задан — заполните .env (см. .env.example)")
        sys.exit(1)
    if not config.manager_ids:
        log.warning("MANAGER_IDS пуст — никто не сможет управлять ботом")

    engine = make_engine(config.db_path)
    await init_db(engine)
    dp = build_dispatcher(make_sessionmaker(engine))
    bot = Bot(config.bot_token)
    await bot.set_my_commands([BotCommand(command="start", description="Главное меню")])
    me = await bot.get_me()
    log.info("Бот @%s запущен. Менеджеры: %s", me.username, sorted(config.manager_ids))
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
