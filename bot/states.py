"""Состояния FSM для многошаговых сценариев."""
from aiogram.fsm.state import State, StatesGroup

# --- менеджер ---------------------------------------------------------------

class ProductAdd(StatesGroup):
    unit = State()
    name = State()
    sellable = State()
    price = State()
    min_stock = State()


class ProductEdit(StatesGroup):
    name = State()
    price = State()
    min_stock = State()


class DrinkAdd(StatesGroup):
    name = State()
    sizes = State()
    price = State()
    cup = State()


class DrinkEdit(StatesGroup):
    name = State()
    size_price = State()
    new_size_price = State()
    new_size_cup = State()


class AddonAdd(StatesGroup):
    name = State()
    price = State()
    link = State()
    product = State()
    qty = State()


class AddonEdit(StatesGroup):
    name = State()
    price = State()
    product = State()
    qty = State()


class QuickAdd(StatesGroup):
    kind = State()
    item = State()
    size = State()
    addons = State()
    qty = State()
    payment = State()
    title = State()


class Income(StatesGroup):
    product = State()
    qty = State()
    comment = State()


class Writeoff(StatesGroup):
    product = State()
    qty = State()
    reason = State()


class StaffAdd(StatesGroup):
    tg_id = State()
    name = State()


class StaffInvite(StatesGroup):
    name = State()


class StaffRename(StatesGroup):
    name = State()


class SaleCancel(StatesGroup):
    reason = State()


class ExpenseCancel(StatesGroup):
    reason = State()


# --- бариста ----------------------------------------------------------------

class ShiftOpen(StatesGroup):
    cash = State()


class SaleFlow(StatesGroup):
    kind = State()
    item = State()
    size = State()
    addons = State()
    qty = State()
    qty_input = State()
    payment = State()


class CancelRequestFlow(StatesGroup):
    sale = State()
    reason = State()


class ExpenseFlow(StatesGroup):
    amount = State()
    description = State()
    photo = State()


class ShiftClose(StatesGroup):
    count = State()
    cash = State()
    terminal = State()
    qr = State()
    confirm = State()
