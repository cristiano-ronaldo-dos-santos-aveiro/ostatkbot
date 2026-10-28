"""Единая фабрика callback_data: действие + id + произвольное значение."""
from aiogram.filters.callback_data import CallbackData


class CB(CallbackData, prefix="c"):
    a: str  # действие
    id: int = 0
    v: str = ""
