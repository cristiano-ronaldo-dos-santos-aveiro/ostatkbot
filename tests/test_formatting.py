import pytest

from bot.services.formatting import InputError, fmt_money, fmt_qty, parse_money, parse_qty


@pytest.mark.parametrize(
    ("text", "unit", "expected"),
    [
        ("500", "g", 500),
        ("500г", "g", 500),
        ("500 гр", "g", 500),
        ("2кг", "g", 2000),
        ("1.5кг", "g", 1500),
        ("1,2 кг", "g", 1200),
        ("750мл", "ml", 750),
        ("1л", "ml", 1000),
        ("1.5 л", "ml", 1500),
        ("12", "pcs", 12),
        ("12 шт", "pcs", 12),
    ],
)
def test_parse_qty(text, unit, expected):
    assert parse_qty(text, unit) == expected


@pytest.mark.parametrize(
    ("text", "unit"),
    [("abc", "g"), ("1л", "g"), ("2кг", "ml"), ("1.5", "pcs"), ("0", "pcs"), ("-5", "g"), ("", "g")],
)
def test_parse_qty_errors(text, unit):
    with pytest.raises(InputError):
        parse_qty(text, unit)


def test_parse_qty_zero_allowed():
    assert parse_qty("0", "pcs", allow_zero=True) == 0


@pytest.mark.parametrize(
    ("text", "expected"),
    [("20000", 20000), ("20 000", 20000), ("20к", 20000), ("1.5k", 1500), ("1.200.000", 1200000),
     ("15 000 сум", 15000)],
)
def test_parse_money(text, expected):
    assert parse_money(text) == expected


@pytest.mark.parametrize("text", ["0", "-100", "abc", "10.5", ""])
def test_parse_money_errors(text):
    with pytest.raises(InputError):
        parse_money(text)


def test_formatters():
    assert fmt_money(1200000) == "1 200 000 сум"
    assert fmt_money(-5000) == "−5 000 сум"
    assert fmt_qty(1500, "g") == "1.5 кг"
    assert fmt_qty(500, "ml") == "500 мл"
    assert fmt_qty(2000, "ml") == "2 л"
    assert fmt_qty(-200, "g") == "−200 г"
    assert fmt_qty(7, "pcs") == "7 шт"
