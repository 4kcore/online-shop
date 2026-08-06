"""Мелкие вспомогательные функции."""

import json
import os
import threading

import config

_order_counter_lock = threading.Lock()


def format_price(amount: int) -> str:
    """150000 -> '150 000 сум'"""
    formatted = f"{amount:,}".replace(",", " ")
    return f"{formatted} {config.CURRENCY_LABEL}"


def next_order_id() -> int:
    """
    Выдать следующий номер заказа, сохраняя счётчик на диске,
    чтобы он не сбрасывался при перезапуске бота.
    """
    with _order_counter_lock:
        path = config.ORDER_COUNTER_FILE
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

        current = 0
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    current = json.load(f).get("last_order_id", 0)
            except (json.JSONDecodeError, OSError):
                current = 0

        new_id = current + 1
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"last_order_id": new_id}, f)

        return new_id
