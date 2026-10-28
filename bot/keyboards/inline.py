"""Построители inline-клавиатур."""
from __future__ import annotations

from collections.abc import Iterable, Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.db.models import Addon, Drink, DrinkSize, Pay, Product, Unit
from bot.keyboards.callbacks import CB
from bot.services.formatting import PAY_LABEL, UNIT_GROUP, fmt_money, fmt_qty

Row = list[InlineKeyboardButton]


def btn(text: str, a: str, id: int = 0, v: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=CB(a=a, id=id, v=v).pack())


def markup(rows: Iterable[Row]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


def cancel_btn() -> InlineKeyboardButton:
    return btn("❌ Отмена", "cancel")


def close_btn() -> InlineKeyboardButton:
    return btn("✖️ Закрыть", "close")


def back_btn(a: str, id: int = 0, v: str = "") -> InlineKeyboardButton:
    return btn("⬅️ Назад", a, id, v)


def nav_row(back: InlineKeyboardButton | None = None, cancel: bool = True) -> Row:
    row: Row = []
    if back:
        row.append(back)
    if cancel:
        row.append(cancel_btn())
    return row


def cancel_kb(back: InlineKeyboardButton | None = None) -> InlineKeyboardMarkup:
    return markup([nav_row(back)])


def grid(buttons: Sequence[InlineKeyboardButton], cols: int = 2) -> list[Row]:
    return [list(buttons[i : i + cols]) for i in range(0, len(buttons), cols)]


def pager_row(action: str, page: int, has_more: bool, id: int = 0) -> Row:
    row: Row = []
    if page > 0:
        row.append(btn("◀️", action, id, str(page - 1)))
    if has_more:
        row.append(btn("▶️", action, id, str(page + 1)))
    return row


def yes_no_rows(action: str) -> list[Row]:
    return [[btn("✅ Да", action, v="1"), btn("❌ Нет", action, v="0")]]


def product_picker(
    products: Sequence[Product], action: str, show_stock: bool = False, cols: int = 2
) -> list[Row]:
    """Товары кнопками, сгруппированные по типу (шт / г / мл)."""
    rows: list[Row] = []
    for unit in Unit.ALL:
        group = [p for p in products if p.unit_type == unit]
        if not group:
            continue
        rows.append([btn(f"— {UNIT_GROUP[unit]} —", "noop")])
        buttons = [
            btn(
                f"{p.name} ({fmt_qty(p.stock, p.unit_type)})" if show_stock else p.name,
                action,
                p.id,
            )
            for p in group
        ]
        rows += grid(buttons, cols)
    return rows


def drinks_rows(drinks: Sequence[Drink], action: str) -> list[Row]:
    return grid([btn(d.name, action, d.id) for d in drinks], 2)


def sizes_rows(sizes: Sequence[DrinkSize], action: str) -> list[Row]:
    return grid([btn(f"{s.size} — {fmt_money(s.price)}", action, s.id) for s in sizes], 3)


def addons_rows(
    addons: Sequence[Addon], selected: Iterable[int], toggle: str, done: str
) -> list[Row]:
    chosen = set(selected)
    buttons = [
        btn(
            f"{'✅ ' if a.id in chosen else ''}{a.name} +{fmt_money(a.price)}"
            if a.price
            else f"{'✅ ' if a.id in chosen else ''}{a.name}",
            toggle,
            a.id,
        )
        for a in addons
    ]
    rows = grid(buttons, 2)
    rows.append([btn("✅ Готово" if chosen else "Без добавок", done)])
    return rows


def qty_rows(action: str, input_action: str | None = None) -> list[Row]:
    row = [btn(f"×{n}", action, n) for n in (1, 2, 3)]
    if input_action:
        row.append(btn("✏️ Ввести", input_action))
    return [row]


def pay_rows(action: str) -> list[Row]:
    return [[btn(PAY_LABEL[p], action, v=p) for p in Pay.ALL]]
