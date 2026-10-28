"""Журнал операций: кто, когда, что (пишется в logs/audit.log и в общий лог)."""
from __future__ import annotations

import logging

from bot.db.models import User

log = logging.getLogger("audit")


def audit(user: User | None, action: str, **details: object) -> None:
    who = f"{user.name} (tg={user.telegram_id}, {user.role})" if user else "system"
    extra = " ".join(f"{k}={v!r}" for k, v in details.items())
    log.info("%s | %s | %s", who, action, extra)
