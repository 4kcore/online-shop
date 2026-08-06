"""
Telegram-бот интернет-магазина.

Поток: выбор языка -> категории -> товары -> карточка товара -> корзина ->
оформление заказа (имя, телефон, адрес, комментарий) -> подтверждение ->
уведомление администратору в отдельный чат.

Запуск: python bot.py
"""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
from helpers import format_price, next_order_id
from locales import t
from sheets import Catalog

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

catalog = Catalog()


# ---------------------------------------------------------------------------
# Вспомогательные функции экрана
# ---------------------------------------------------------------------------

def get_lang(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get("lang", "ru")


def get_cart(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.user_data.setdefault("cart", {})  # {row_id: qty}


def cart_count(context: ContextTypes.DEFAULT_TYPE) -> int:
    return sum(get_cart(context).values())


async def send_screen(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    photo: str | None = None,
):
    """Удаляет предыдущий экран бота и отправляет новый (текст или фото)."""
    chat_id = update.effective_chat.id

    last_msg_id = context.user_data.get("last_msg_id")
    if last_msg_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=last_msg_id)
        except Exception:
            pass  # сообщение могло быть уже удалено или слишком старое

    if photo:
        msg = await context.bot.send_photo(
            chat_id=chat_id,
            photo=photo,
            caption=text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML,
        )
    else:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML,
        )
    context.user_data["last_msg_id"] = msg.message_id


def cart_button_row(context: ContextTypes.DEFAULT_TYPE) -> list[InlineKeyboardButton]:
    lang = get_lang(context)
    return [InlineKeyboardButton(t(lang, "cart_button", count=cart_count(context)), callback_data="cart")]


# ---------------------------------------------------------------------------
# Старт / выбор языка
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🇷🇺 Русский", callback_data="lang:ru")],
            [InlineKeyboardButton("🇺🇿 O'zbekcha", callback_data="lang:uz")],
        ]
    )
    await send_screen(update, context, "Выберите язык / Tilni tanlang", keyboard)


async def on_language_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = query.data.split(":", 1)[1]
    context.user_data["lang"] = lang
    context.user_data["cart"] = {}
    await show_categories(update, context)


# ---------------------------------------------------------------------------
# Категории
# ---------------------------------------------------------------------------

async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    categories = catalog.get_categories()

    if not categories:
        await send_screen(update, context, t(lang, "no_categories"))
        return

    rows = [
        [InlineKeyboardButton(cat, callback_data=f"cat:{idx}")]
        for idx, cat in enumerate(categories)
    ]
    rows.append(cart_button_row(context))
    keyboard = InlineKeyboardMarkup(rows)

    text = f"{t(lang, 'welcome')}\n\n{t(lang, 'categories_title')}"
    await send_screen(update, context, text, keyboard)


async def on_show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_categories(update, context)


# ---------------------------------------------------------------------------
# Товары в категории
# ---------------------------------------------------------------------------

async def on_category_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = get_lang(context)
    idx = int(query.data.split(":", 1)[1])
    categories = catalog.get_categories()

    if idx >= len(categories):
        await show_categories(update, context)
        return

    category = categories[idx]
    context.user_data["current_category_idx"] = idx
    products = catalog.get_products_by_category(category)

    if not products:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(t(lang, "back_to_categories"), callback_data="menu")]]
        )
        await send_screen(update, context, t(lang, "no_products"), keyboard)
        return

    # Товар остаётся кликабельным, даже если закончился — карточка товара
    # сама покажет "нет в наличии" и скроет кнопку "добавить в корзину"
    rows = [
        [InlineKeyboardButton(
            f"{p.name} — {format_price(p.price)}" if p.stock > 0 else f"{p.name} ❌",
            callback_data=f"prod:{p.row}",
        )]
        for p in products
    ]
    rows.append([InlineKeyboardButton(t(lang, "back_to_categories"), callback_data="menu")])
    rows.append(cart_button_row(context))
    keyboard = InlineKeyboardMarkup(rows)

    text = t(lang, "products_in_category", category=category)
    await send_screen(update, context, text, keyboard)


# ---------------------------------------------------------------------------
# Карточка товара
# ---------------------------------------------------------------------------

async def on_product_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = get_lang(context)
    row_id = int(query.data.split(":", 1)[1])
    product = catalog.get_product(row_id)

    if product is None:
        await show_categories(update, context)
        return

    cat_idx = context.user_data.get("current_category_idx", 0)

    text_lines = [f"<b>{product.name}</b>"]
    if product.description:
        text_lines.append(product.description)
    text_lines.append(f"\n{t(lang, 'price_label')}: {format_price(product.price)}")
    if product.stock <= 0:
        text_lines.append(f"\n{t(lang, 'out_of_stock')}")
    text = "\n".join(text_lines)

    rows = []
    if product.stock > 0:
        rows.append([InlineKeyboardButton(t(lang, "add_to_cart"), callback_data=f"add:{product.row}")])
    rows.append([InlineKeyboardButton(t(lang, "back"), callback_data=f"cat:{cat_idx}")])
    rows.append(cart_button_row(context))
    keyboard = InlineKeyboardMarkup(rows)

    photo = product.photo if product.photo else None
    await send_screen(update, context, text, keyboard, photo=photo)


async def on_add_to_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = get_lang(context)
    row_id = int(query.data.split(":", 1)[1])
    product = catalog.get_product(row_id)

    if product is None or product.stock <= 0:
        await query.answer(t(lang, "out_of_stock"), show_alert=True)
        return

    cart = get_cart(context)
    cart[row_id] = cart.get(row_id, 0) + 1
    await query.answer(t(lang, "added_to_cart", name=product.name))
    await on_product_chosen(update, context)  # перерисовать карточку с обновлённым счётчиком корзины


# ---------------------------------------------------------------------------
# Корзина
# ---------------------------------------------------------------------------

async def show_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    cart = get_cart(context)

    if not cart:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(t(lang, "back_to_categories"), callback_data="menu")]]
        )
        await send_screen(update, context, t(lang, "cart_empty"), keyboard)
        return

    lines = [t(lang, "cart_title"), ""]
    rows = []
    total = 0

    for row_id, qty in list(cart.items()):
        product = catalog.get_product(row_id)
        if product is None:
            del cart[row_id]
            continue
        subtotal = product.price * qty
        total += subtotal
        lines.append(
            t(lang, "cart_item_line", name=product.name, qty=qty,
              price=format_price(product.price), subtotal=format_price(subtotal))
        )
        rows.append([
            InlineKeyboardButton(t(lang, "decrease_qty"), callback_data=f"cart_dec:{row_id}"),
            InlineKeyboardButton(str(qty), callback_data="noop"),
            InlineKeyboardButton(t(lang, "increase_qty"), callback_data=f"cart_inc:{row_id}"),
            InlineKeyboardButton("❌", callback_data=f"cart_rm:{row_id}"),
        ])

    lines.append("")
    lines.append(t(lang, "cart_total", total=format_price(total)))

    rows.append([InlineKeyboardButton(t(lang, "checkout_button"), callback_data="checkout_start")])
    rows.append([InlineKeyboardButton(t(lang, "continue_shopping"), callback_data="menu")])
    rows.append([InlineKeyboardButton(t(lang, "clear_cart"), callback_data="cart_clear")])

    keyboard = InlineKeyboardMarkup(rows)
    await send_screen(update, context, "\n".join(lines), keyboard)


async def on_show_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_cart(update, context)


async def on_cart_inc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = get_lang(context)
    row_id = int(query.data.split(":", 1)[1])
    product = catalog.get_product(row_id)
    cart = get_cart(context)

    if product and cart.get(row_id, 0) < product.stock:
        cart[row_id] = cart.get(row_id, 0) + 1
    else:
        await query.answer(t(lang, "out_of_stock"), show_alert=True)
    await show_cart(update, context)


async def on_cart_dec(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    row_id = int(query.data.split(":", 1)[1])
    cart = get_cart(context)

    if cart.get(row_id, 0) > 1:
        cart[row_id] -= 1
    else:
        cart.pop(row_id, None)
    await show_cart(update, context)


async def on_cart_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    row_id = int(query.data.split(":", 1)[1])
    get_cart(context).pop(row_id, None)
    await show_cart(update, context)


async def on_cart_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    context.user_data["cart"] = {}
    query = update.callback_query
    await query.answer(t(lang, "cart_cleared"))
    await show_cart(update, context)


# ---------------------------------------------------------------------------
# Оформление заказа (пошаговый ввод текста)
# ---------------------------------------------------------------------------

CHECKOUT_FIELDS = ["name", "phone", "address", "comment"]
CHECKOUT_PROMPTS = {
    "name": "ask_name",
    "phone": "ask_phone",
    "address": "ask_address",
    "comment": "ask_comment",
}


async def on_checkout_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    cart = get_cart(context)
    if not cart:
        await show_cart(update, context)
        return

    context.user_data["checkout"] = {}
    context.user_data["awaiting"] = "name"
    await send_screen(update, context, t(lang, "ask_name"))


async def on_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает текстовые сообщения при пошаговом вводе данных заказа."""
    lang = get_lang(context)
    awaiting = context.user_data.get("awaiting")
    if not awaiting:
        return  # бот не ждёт текстового ввода — игнорируем

    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text(t(lang, "invalid_input"))
        return

    checkout = context.user_data.setdefault("checkout", {})

    if awaiting == "comment" and text == "-":
        text = t(lang, "no_comment")

    checkout[awaiting] = text

    # Определяем следующее поле
    if awaiting == "address" and context.user_data.get("editing_address"):
        context.user_data["editing_address"] = False
        context.user_data["awaiting"] = None
        await show_order_summary(update, context)
        return

    current_idx = CHECKOUT_FIELDS.index(awaiting)
    if current_idx + 1 < len(CHECKOUT_FIELDS):
        next_field = CHECKOUT_FIELDS[current_idx + 1]
        context.user_data["awaiting"] = next_field
        await update.message.reply_text(t(lang, CHECKOUT_PROMPTS[next_field]))
    else:
        context.user_data["awaiting"] = None
        await show_order_summary(update, context)


async def show_order_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    checkout = context.user_data.get("checkout", {})
    cart = get_cart(context)

    lines = [t(lang, "order_summary_title"), ""]
    total = 0
    for row_id, qty in cart.items():
        product = catalog.get_product(row_id)
        if product is None:
            continue
        subtotal = product.price * qty
        total += subtotal
        lines.append(t(lang, "cart_item_line", name=product.name, qty=qty,
                        price=format_price(product.price), subtotal=format_price(subtotal)))

    lines.append("")
    lines.append(t(lang, "cart_total", total=format_price(total)))
    lines.append("")
    lines.append(f"{t(lang, 'order_name')}: {checkout.get('name', '')}")
    lines.append(f"{t(lang, 'order_phone')}: {checkout.get('phone', '')}")
    lines.append(f"{t(lang, 'order_address')}: {checkout.get('address', '')}")
    lines.append(f"{t(lang, 'order_comment')}: {checkout.get('comment', '')}")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "confirm_order"), callback_data="confirm_order")],
        [InlineKeyboardButton(t(lang, "edit_address"), callback_data="edit_address")],
        [InlineKeyboardButton(t(lang, "start_over"), callback_data="menu")],
    ])

    # На этом шаге предыдущее сообщение — обычный текстовый ответ пользователю,
    # поэтому отправляем новое сообщение через bot.send_message напрямую.
    msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="\n".join(lines),
        reply_markup=keyboard,
    )
    context.user_data["last_msg_id"] = msg.message_id


async def on_edit_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    context.user_data["editing_address"] = True
    context.user_data["awaiting"] = "address"
    await send_screen(update, context, t(lang, "ask_address"))


async def on_confirm_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = get_lang(context)
    cart = get_cart(context)
    checkout = context.user_data.get("checkout", {})

    if not cart:
        await show_cart(update, context)
        return

    # Пытаемся списать остатки по каждому товару. При неудаче (кто-то
    # успел раньше) — сообщаем и просим поправить корзину.
    order_items = []
    total = 0
    for row_id, qty in list(cart.items()):
        product = catalog.get_product(row_id)
        if product is None:
            continue
        if not catalog.try_decrement_stock(row_id, qty):
            await query.answer()
            await send_screen(
                update, context,
                t(lang, "order_failed_stock", name=product.name),
            )
            return
        order_items.append((product, qty))
        total += product.price * qty

    order_id = next_order_id()

    # Уведомление администратору
    user = update.effective_user
    username = f"@{user.username}" if user.username else f"id:{user.id}"
    admin_lines = [
        f"🆕 Новый заказ #{order_id}",
        "",
        *[f"{p.name} — {qty} x {format_price(p.price)} = {format_price(p.price * qty)}" for p, qty in order_items],
        "",
        f"Итого: {format_price(total)}",
        "",
        f"Имя: {checkout.get('name', '')}",
        f"Телефон: {checkout.get('phone', '')}",
        f"Адрес: {checkout.get('address', '')}",
        f"Комментарий: {checkout.get('comment', '')}",
        f"Telegram: {username}",
    ]
    try:
        await context.bot.send_message(chat_id=config.ADMIN_CHAT_ID, text="\n".join(admin_lines))
    except Exception:
        logger.exception("Не удалось отправить уведомление о заказе #%s администратору", order_id)

    # Подтверждение клиенту
    await query.answer()
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=t(lang, "order_confirmed", order_id=order_id),
    )

    context.user_data["cart"] = {}
    context.user_data["checkout"] = {}
    context.user_data["awaiting"] = None
    await show_categories(update, context)


async def on_noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


# ---------------------------------------------------------------------------
# Запуск приложения
# ---------------------------------------------------------------------------

def main():
    catalog.start_background_refresh()

    application = Application.builder().token(config.BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", cmd_start))

    application.add_handler(CallbackQueryHandler(on_language_chosen, pattern=r"^lang:"))
    application.add_handler(CallbackQueryHandler(on_show_categories, pattern=r"^menu$"))
    application.add_handler(CallbackQueryHandler(on_category_chosen, pattern=r"^cat:\d+$"))
    application.add_handler(CallbackQueryHandler(on_product_chosen, pattern=r"^prod:\d+$"))
    application.add_handler(CallbackQueryHandler(on_add_to_cart, pattern=r"^add:\d+$"))
    application.add_handler(CallbackQueryHandler(on_show_cart, pattern=r"^cart$"))
    application.add_handler(CallbackQueryHandler(on_cart_inc, pattern=r"^cart_inc:\d+$"))
    application.add_handler(CallbackQueryHandler(on_cart_dec, pattern=r"^cart_dec:\d+$"))
    application.add_handler(CallbackQueryHandler(on_cart_remove, pattern=r"^cart_rm:\d+$"))
    application.add_handler(CallbackQueryHandler(on_cart_clear, pattern=r"^cart_clear$"))
    application.add_handler(CallbackQueryHandler(on_checkout_start, pattern=r"^checkout_start$"))
    application.add_handler(CallbackQueryHandler(on_edit_address, pattern=r"^edit_address$"))
    application.add_handler(CallbackQueryHandler(on_confirm_order, pattern=r"^confirm_order$"))
    application.add_handler(CallbackQueryHandler(on_noop, pattern=r"^noop$"))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_input))

    logger.info("Бот запущен")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
