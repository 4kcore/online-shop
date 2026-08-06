"""
Конфигурация бота. Все секреты берутся из переменных окружения (.env),
чтобы не хранить токены в коде.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# Токен бота, полученный от @BotFather
BOT_TOKEN = os.environ["BOT_TOKEN"]

# ID чата/канала, куда бот шлёт уведомления о новых заказах
# (число, например -1001234567890 для группы, или просто ID пользователя)
ADMIN_CHAT_ID = int(os.environ["ADMIN_CHAT_ID"])

# Google Sheets
GOOGLE_SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
GOOGLE_CREDENTIALS_FILE = os.environ.get(
    "GOOGLE_CREDENTIALS_FILE", "credentials.json"
)
PRODUCTS_SHEET_NAME = os.environ.get("PRODUCTS_SHEET_NAME", "Товары")

# Как часто (в секундах) бот перечитывает таблицу с товарами
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "180"))  # 3 минуты

# Валюта, добавляемая к отформатированной цене
CURRENCY_LABEL = os.environ.get("CURRENCY_LABEL", "сум")

# Файл, где хранится последний номер заказа (чтобы не сбрасывался при рестарте)
ORDER_COUNTER_FILE = os.environ.get("ORDER_COUNTER_FILE", "data/order_counter.json")
