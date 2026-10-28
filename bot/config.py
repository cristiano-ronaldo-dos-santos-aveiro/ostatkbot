"""Настройки бота из переменных окружения / файла .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    bot_token: str
    manager_ids: frozenset[int]
    timezone: ZoneInfo
    currency: str
    db_path: str
    log_dir: str
    log_level: str


def _parse_ids(raw: str) -> frozenset[int]:
    ids: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if not part.lstrip("-").isdigit():
            raise ValueError(f"MANAGER_IDS: «{part}» не похоже на telegram_id")
        ids.add(int(part))
    return frozenset(ids)


@lru_cache(maxsize=1)
def get_config() -> Config:
    load_dotenv()
    return Config(
        bot_token=os.getenv("BOT_TOKEN", "").strip(),
        manager_ids=_parse_ids(os.getenv("MANAGER_IDS", "")),
        timezone=ZoneInfo(os.getenv("TIMEZONE", "Asia/Tashkent").strip() or "Asia/Tashkent"),
        currency=os.getenv("CURRENCY", "сум").strip() or "сум",
        db_path=os.getenv("DB_PATH", "data/coffee.db").strip() or "data/coffee.db",
        log_dir=os.getenv("LOG_DIR", "logs").strip() or "logs",
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
    )
