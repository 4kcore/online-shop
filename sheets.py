"""
Работа с Google Таблицей как источником каталога товаров.

Структура листа "Товары" (первая строка — заголовки):
Категория | Название | Цена | Остаток | Описание | Фото

Кеш обновляется по таймеру (см. CACHE_TTL_SECONDS в config).
При заказе остаток уменьшается сразу в кеше И записывается обратно
в таблицу (двойное обновление), чтобы избежать рассинхрона при
перезапуске бота.
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

import config

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

# Ожидаемые заголовки колонок в листе "Товары" (порядок важен)
COL_CATEGORY = "Категория"
COL_NAME = "Название"
COL_PRICE = "Цена"
COL_STOCK = "Остаток"
COL_DESCRIPTION = "Описание"
COL_PHOTO = "Фото"


@dataclass
class Product:
    row: int  # номер строки в таблице (для записи остатка обратно)
    category: str
    name: str
    price: int
    stock: int
    description: str
    photo: str

    @property
    def id(self) -> int:
        # Строка таблицы служит стабильным идентификатором товара
        return self.row


class Catalog:
    """Потокобезопасный кеш каталога товаров с периодическим обновлением."""

    def __init__(self):
        self._lock = threading.Lock()
        self._products: list[Product] = []
        self._categories: list[str] = []
        self._client: Optional[gspread.Client] = None
        self._sheet = None
        self._stop_event = threading.Event()
        self._refresh_thread: Optional[threading.Thread] = None

    # ---------- подключение ----------

    def _connect(self):
        creds = Credentials.from_service_account_file(
            config.GOOGLE_CREDENTIALS_FILE, scopes=SCOPES
        )
        self._client = gspread.authorize(creds)
        spreadsheet = self._client.open_by_key(config.GOOGLE_SHEET_ID)
        self._sheet = spreadsheet.worksheet(config.PRODUCTS_SHEET_NAME)

    # ---------- чтение / кеш ----------

    def refresh(self):
        """Перечитать таблицу и обновить кеш в памяти."""
        try:
            if self._sheet is None:
                self._connect()

            records = self._sheet.get_all_records()
            products: list[Product] = []
            categories: list[str] = []

            for idx, row in enumerate(records, start=2):  # строка 1 — заголовки
                try:
                    category = str(row.get(COL_CATEGORY, "")).strip()
                    name = str(row.get(COL_NAME, "")).strip()
                    if not name:
                        continue  # пропускаем пустые строки

                    price_raw = row.get(COL_PRICE, 0)
                    stock_raw = row.get(COL_STOCK, 0)
                    price = int(float(price_raw)) if str(price_raw).strip() else 0
                    stock = int(float(stock_raw)) if str(stock_raw).strip() else 0

                    product = Product(
                        row=idx,
                        category=category or "Без категории",
                        name=name,
                        price=price,
                        stock=max(stock, 0),
                        description=str(row.get(COL_DESCRIPTION, "")).strip(),
                        photo=str(row.get(COL_PHOTO, "")).strip(),
                    )
                    products.append(product)
                    if product.category not in categories:
                        categories.append(product.category)
                except (ValueError, TypeError) as e:
                    logger.warning("Пропущена строка %s из-за ошибки данных: %s", idx, e)

            with self._lock:
                self._products = products
                self._categories = categories

            logger.info(
                "Каталог обновлён: %d товаров, %d категорий",
                len(products), len(categories),
            )
        except Exception:
            logger.exception("Не удалось обновить каталог из Google Sheets")

    def start_background_refresh(self):
        """Запустить фоновый поток, обновляющий каталог по таймеру."""
        self.refresh()  # первая загрузка сразу, синхронно

        def loop():
            while not self._stop_event.wait(config.CACHE_TTL_SECONDS):
                self.refresh()

        self._refresh_thread = threading.Thread(target=loop, daemon=True)
        self._refresh_thread.start()

    def stop(self):
        self._stop_event.set()

    # ---------- доступ к данным ----------

    def get_categories(self) -> list[str]:
        with self._lock:
            return list(self._categories)

    def get_products_by_category(self, category: str) -> list[Product]:
        with self._lock:
            return [p for p in self._products if p.category == category]

    def get_product(self, row_id: int) -> Optional[Product]:
        with self._lock:
            for p in self._products:
                if p.row == row_id:
                    return p
        return None

    # ---------- запись (остатки) ----------

    def try_decrement_stock(self, row_id: int, qty: int) -> bool:
        """
        Атомарно (в рамках процесса) проверить и уменьшить остаток.
        Возвращает True при успехе, False если товара не хватает
        (например, кто-то другой уже успел его заказать).
        Обновляет и кеш, и саму Google Таблицу.
        """
        with self._lock:
            product = next((p for p in self._products if p.row == row_id), None)
            if product is None or product.stock < qty:
                return False
            product.stock -= qty
            new_stock = product.stock

        # Запись в таблицу выполняется вне блокировки кеша, чтобы не
        # держать лок на время сетевого запроса
        try:
            if self._sheet is None:
                self._connect()
            # Определяем номер колонки "Остаток" по заголовкам листа
            header = self._sheet.row_values(1)
            col_idx = header.index(COL_STOCK) + 1
            self._sheet.update_cell(row_id, col_idx, new_stock)
        except Exception:
            logger.exception(
                "Не удалось записать новый остаток в таблицу (row=%s). "
                "Кеш уже обновлён, но таблица может временно не совпадать.",
                row_id,
            )
        return True
