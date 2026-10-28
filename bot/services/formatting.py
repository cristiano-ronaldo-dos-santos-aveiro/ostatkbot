"""Форматирование и разбор ввода: деньги, количества, даты.

Деньги — только целые числа (сумы). Количество — целые шт / г / мл.
"""
from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from bot.config import get_config
from bot.db.models import Pay, Unit, utcnow  # noqa: F401 (реэкспорт utcnow)

MAX_MONEY = 10**12
MAX_QTY = 10**9


class InputError(ValueError):
    """Ошибка ввода с понятным пользователю текстом."""


# --- деньги -----------------------------------------------------------------

def _group(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def fmt_money(amount: int) -> str:
    sign = "−" if amount < 0 else ""
    return f"{sign}{_group(abs(amount))} {get_config().currency}"


def fmt_money_signed(amount: int) -> str:
    if amount > 0:
        return "+" + fmt_money(amount)
    return fmt_money(amount)


_THOUSANDS_RE = re.compile(r"^\d{1,3}([.,]\d{3})+$")


def parse_money(text: str, allow_zero: bool = False) -> int:
    t = (text or "").lower()
    for junk in (" ", " ", " ", "сум", "sum", "so'm", "сўм"):
        t = t.replace(junk, "")
    mult = 1
    if t.endswith(("к", "k")):
        mult, t = 1000, t[:-1]
    if not t:
        raise InputError("Введите сумму числом, например 20000 или 20 000.")
    if mult == 1 and _THOUSANDS_RE.match(t):
        t = t.replace(".", "").replace(",", "")
    try:
        value = Decimal(t.replace(",", ".")) * mult
    except InvalidOperation:
        raise InputError("Введите сумму числом, например 20000 или 20 000.") from None
    if value != value.to_integral_value():
        raise InputError("Сумма должна быть целым числом.")
    value = int(value)
    if value < 0:
        raise InputError("Сумма не может быть отрицательной.")
    if value == 0 and not allow_zero:
        raise InputError("Сумма должна быть больше нуля.")
    if value > MAX_MONEY:
        raise InputError("Слишком большая сумма.")
    return value


# --- количества -----------------------------------------------------------------

UNIT_SHORT = {Unit.PCS: "шт", Unit.G: "г", Unit.ML: "мл"}
UNIT_TITLE = {Unit.PCS: "штучный", Unit.G: "граммовый", Unit.ML: "миллилитры"}
UNIT_GROUP = {Unit.PCS: "Штучные", Unit.G: "Граммовые", Unit.ML: "Миллилитры"}

_UNIT_FACTORS = {
    Unit.PCS: {"": 1, "шт": 1, "штук": 1, "штуки": 1, "штука": 1, "pcs": 1, "pc": 1},
    Unit.G: {"": 1, "г": 1, "гр": 1, "грамм": 1, "граммов": 1, "g": 1, "gr": 1,
             "кг": 1000, "kg": 1000, "килограмм": 1000},
    Unit.ML: {"": 1, "мл": 1, "ml": 1, "л": 1000, "l": 1000, "литр": 1000, "литра": 1000,
              "литров": 1000},
}
_UNIT_EXAMPLES = {
    Unit.PCS: "например 12",
    Unit.G: "например 500, 500г, 2кг, 1.5кг",
    Unit.ML: "например 750, 750мл, 1л, 1.5л",
}
_QTY_RE = re.compile(r"^(\d+(?:[.,]\d+)?)([a-zа-яё]*)\.?$")


def parse_qty(text: str, unit_type: str, allow_zero: bool = False) -> int:
    t = re.sub(r"\s+", "", (text or "").lower())
    m = _QTY_RE.match(t)
    if not m:
        raise InputError(f"Введите количество числом ({_UNIT_EXAMPLES[unit_type]}).")
    number, suffix = m.groups()
    factor = _UNIT_FACTORS[unit_type].get(suffix)
    if factor is None:
        raise InputError(
            f"Не понял единицу «{suffix}». Для этого товара: {_UNIT_EXAMPLES[unit_type]}."
        )
    value = Decimal(number.replace(",", ".")) * factor
    if unit_type == Unit.PCS and value != value.to_integral_value():
        raise InputError("Для штучного товара введите целое число.")
    value_int = int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if value_int == 0 and not allow_zero:
        raise InputError("Количество должно быть больше нуля.")
    if value_int > MAX_QTY:
        raise InputError("Слишком большое количество.")
    return value_int


def _strip_decimal(d: Decimal) -> str:
    s = f"{d:.3f}".rstrip("0").rstrip(".")
    return s


def fmt_qty(qty: int, unit_type: str) -> str:
    if unit_type == Unit.PCS:
        return f"{'−' if qty < 0 else ''}{abs(qty)} шт"
    big, small = ("кг", "г") if unit_type == Unit.G else ("л", "мл")
    if abs(qty) >= 1000:
        sign = "−" if qty < 0 else ""
        return f"{sign}{_strip_decimal(Decimal(abs(qty)) / 1000)} {big}"
    return f"{'−' if qty < 0 else ''}{abs(qty)} {small}"


def fmt_qty_signed(qty: int, unit_type: str) -> str:
    return ("+" if qty > 0 else "") + fmt_qty(qty, unit_type)


def qty_hint(unit_type: str) -> str:
    return _UNIT_EXAMPLES[unit_type]


# --- оплата ---------------------------------------------------------------------

PAY_LABEL = {Pay.CASH: "💵 Наличные", Pay.TERMINAL: "💳 Терминал", Pay.QR: "📱 QR"}
PAY_SHORT = {Pay.CASH: "нал", Pay.TERMINAL: "терминал", Pay.QR: "QR"}


# --- текст ---------------------------------------------------------------------

def clean_text(text: str | None, max_len: int = 64, field: str = "Название") -> str:
    value = " ".join((text or "").split())
    if not value:
        raise InputError(f"{field} не может быть пустым.")
    if len(value) > max_len:
        raise InputError(f"{field} слишком длинное (максимум {max_len} символов).")
    return value


# --- время ----------------------------------------------------------------------

def tz():
    return get_config().timezone


def to_local(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC).astimezone(tz())


def fmt_time(dt: datetime | None) -> str:
    return to_local(dt).strftime("%H:%M") if dt else "…"


def fmt_date(dt: datetime) -> str:
    return to_local(dt).strftime("%d.%m.%Y")


def fmt_dt(dt: datetime) -> str:
    return to_local(dt).strftime("%d.%m %H:%M")


def local_today() -> date:
    return datetime.now(tz()).date()


def local_day_start_utc(day: date) -> datetime:
    """Начало локальных суток в UTC (naive)."""
    return datetime.combine(day, time.min, tzinfo=tz()).astimezone(UTC).replace(tzinfo=None)


def local_range_utc(first_day: date, last_day: date) -> tuple[datetime, datetime]:
    """[начало first_day, начало дня после last_day) в UTC."""
    return local_day_start_utc(first_day), local_day_start_utc(last_day + timedelta(days=1))
