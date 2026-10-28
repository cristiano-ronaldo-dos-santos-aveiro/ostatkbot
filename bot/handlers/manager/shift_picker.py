"""Выбор смены списком с пагинацией (для продаж, расходов, отчётов)."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.db.models import Shift, Status
from bot.keyboards.inline import Row, btn, pager_row
from bot.services.formatting import fmt_dt, fmt_time

PAGE = 8


def shift_label(shift: Shift, name: str) -> str:
    if shift.status == Status.OPEN:
        return f"🟢 {name} · {fmt_dt(shift.opened_at)}–…"
    return f"{name} · {fmt_dt(shift.opened_at)}–{fmt_time(shift.closed_at)}"


async def shift_rows(
    session: AsyncSession, pick_action: str, page_action: str, page: int, status: str | None = None
) -> tuple[list[Row], bool]:
    """Кнопки смен страницы page. Возвращает (ряды, есть_смены)."""
    shifts, has_more = await crud.list_shifts(session, status=status, offset=page * PAGE, limit=PAGE)
    users = await crud.list_users(session, {s.barista_id for s in shifts})
    rows: list[Row] = [
        [btn(shift_label(s, users[s.barista_id].name if s.barista_id in users else "?"), pick_action, s.id)]
        for s in shifts
    ]
    rows.append(pager_row(page_action, page, has_more))
    return rows, bool(shifts) or page > 0
