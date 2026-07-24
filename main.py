import asyncio
import html
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

import aiohttp
import aiosqlite
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("PriceMonitor")


@dataclass
class Product:
    """Модель товара Steam."""
    id: Optional[int]
    url: str
    title: Optional[str] = None
    original_price: Optional[float] = None
    current_price: Optional[float] = None
    discount_percent: int = 0
    currency: str = ""


class Database:
    """Слой работы с базой данных SQLite."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    async def init_db(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER DEFAULT 0,
                    title TEXT,
                    url TEXT,
                    original_price REAL,
                    current_price REAL,
                    last_price REAL,
                    discount_percent INTEGER,
                    currency TEXT
              )
         ''')
            
            # Автоматическая миграция новых колонок
            columns_to_add = [
                ("original_price", "REAL DEFAULT 0.0"),
                ("discount_percent", "INTEGER DEFAULT 0"),
                ("currency", "TEXT DEFAULT ''")
            ]
            for col_name, col_type in columns_to_add:
                try:
                await db.execute("ALTER TABLE products ADD COLUMN user_id INTEGER DEFAULT 0")
            except Exception:
                pass  # Колонка уже есть, идем дальше

            try:
                await db.execute("ALTER TABLE products ADD COLUMN last_price REAL")
            except Exception:
                pass  # Колонка уже есть, идем дальше
                
            await db.commit()

    async def get_all_products(self) -> list[Product]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT id, url, title, original_price, last_price, discount_percent, currency FROM products"
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    Product(
                        id=row["id"],
                        url=row["url"],
                        title=row["title"],
                        original_price=row["original_price"],
                        current_price=row["last_price"],
                        discount_percent=row["discount_percent"] or 0,
                        currency=row["currency"] or ""
                    )
                    for row in rows
                ]

    # Добавление игры с привязкой к пользователю
async def add_product(self, user_id: int, title: str, url: str, orig_price: float, current_price: float, discount: int, currency: str):
    async with aiosqlite.connect(self.db_path) as db:
        await db.execute(
            "INSERT INTO products (user_id, title, url, original_price, current_price, discount_percent, currency) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, title, url, orig_price, current_price, discount, currency)
        )
        await db.commit()

# Получение списка игр конкретного пользователя (для /list, /deals, /clear)
async def get_user_products(self, user_id: int):
    async with aiosqlite.connect(self.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM products WHERE user_id = ?", (user_id,)) as cursor:
        return await cursor.fetchall()
            
            ...

    async def delete_product(self, product_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM products WHERE id = ?", (product_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def update_product_data(
        self,
        product_id: int,
        title: str,
        original_price: float,
        current_price: float,
        discount_percent: int,
        currency: str
    ) -> None:
        """Обновляет полное состояние игры в базе данных."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE products 
                SET title = ?, original_price = ?, last_price = ?, discount_percent = ?, currency = ?, updated_at = CURRENT_TIMESTAMP 
                WHERE id = ?
                """,
                (title, original_price, current_price, discount_percent, currency, product_id)
            )
            await db.commit()


    async def clear_all_products(self) -> int:
        """Полностью очищает таблицу продуктов и возвращает количество удаленных строк."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM products")
            await db.commit()
            return cursor.rowcount


class ScraperService:
    """Сервис поиска и парсинга Steam Store."""

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9,ru;q=0.8"
    }

    # Обход проверки возраста (18+)
    COOKIES = {
        "birthtime": "568028401",
        "lastagecheckage": "1-0-1988",
        "wants_mature_content": "1"
    }

    async def search_steam_game(self, session: aiohttp.ClientSession, query: str) -> Optional[str]:
        """Ищет игру в Steam по названию и возвращает ссылку на первый результат."""
        # 1. Поиск через официальный API Steam
        search_api_url = "https://store.steampowered.com/api/storesearch/"
        params = {"term": query, "l": "russian", "cc": "US"}
        try:
            async with session.get(search_api_url, headers=self.HEADERS, params=params, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    items = data.get("items", [])
                    if items:
                        app_id = items[0]["id"]
                        return f"https://store.steampowered.com/app/{app_id}/"
        except Exception as e:
            logger.error(f"Ошибка поиска через API: {e}")

        # 2. Резервный вариант через парсинг результатов поиска HTML
        try:
            html_search_url = f"https://store.steampowered.com/search/?term={quote(query)}"
            async with session.get(html_search_url, headers=self.HEADERS, cookies=self.COOKIES, timeout=10) as resp:
                if resp.status == 200:
                    html_text = await resp.text()
                    soup = BeautifulSoup(html_text, "html.parser")
                    first_result = soup.select_one("a.search_result_row")
                    if first_result and "href" in first_result.attrs:
                        return first_result["href"]
        except Exception as e:
            logger.error(f"Ошибка поиска через HTML: {e}")

        return None

    @staticmethod
    def _extract_number_and_currency(raw_str: str) -> tuple[Optional[float], str]:
        """Извлекает числовое значение и символ валюты из строки."""
        currency = ""
        if "£" in raw_str:
            currency = "£"
        elif "$" in raw_str:
            currency = "$"
        elif "€" in raw_str:
            currency = "€"
        elif "₼" in raw_str or "AZN" in raw_str:
            currency = "₼"
        elif "₽" in raw_str or "руб" in raw_str.lower() or "pуб" in raw_str.lower():
            currency = "₽"

        clean_str = re.sub(r"[^\d.,]", "", raw_str).replace(",", ".")
        try:
            return float(clean_str), currency
        except ValueError:
            return None, currency

    async def fetch_steam_game(self, session: aiohttp.ClientSession, url: str) -> tuple[Optional[str], Optional[float], Optional[float], int, str]:
        """Парсит страницу Steam: название, оригинальную цену, текущую цену, % скидки и валюту."""
        try:
            async with session.get(url, headers=self.HEADERS, cookies=self.COOKIES, timeout=15) as response:
                if response.status != 200:
                    return None, None, None, 0, ""

                html_text = await response.text()
                soup = BeautifulSoup(html_text, "html.parser")

                # Название игры
                title_tag = (
                    soup.find("div", class_="apphub_AppName") or
                    soup.find("span", id="largeiteminfo_item_name") or
                    soup.find("meta", property="og:title")
                )
                title = title_tag.text.strip() if hasattr(title_tag, "text") else "Игра Steam"
                if hasattr(title_tag, "get") and title_tag.get("content"):
                    title = title_tag.get("content").strip()

                # Парсинг скидочных элементов Steam Store
                disc_pct_elem = soup.select_one(".discount_pct")
                disc_orig_elem = soup.select_one(".discount_original_price")
                disc_final_elem = soup.select_one(".discount_final_price")
                regular_price_elem = soup.select_one(".game_purchase_price, .market_listing_price_with_fee")

                discount_percent = 0
                if disc_pct_elem:
                    pct_digits = re.sub(r"[^\d]", "", disc_pct_elem.text)
                    discount_percent = int(pct_digits) if pct_digits else 0

                # 1. Вариант: Игра сейчас со скидкой
                if disc_orig_elem and disc_final_elem:
                    orig_price, currency = self._extract_number_and_currency(disc_orig_elem.text.strip())
                    final_price, _ = self._extract_number_and_currency(disc_final_elem.text.strip())
                    return title, orig_price, final_price, discount_percent, currency

                # 2. Вариант: Игра продается по обычной цене (без скидки)
                if regular_price_elem:
                    price, currency = self._extract_number_and_currency(regular_price_elem.text.strip())
                    # Бесплатные игры (Free to Play)
                    if "free" in regular_price_elem.text.lower() or "бесплатно" in regular_price_elem.text.lower():
                        return title, 0.0, 0.0, 0, currency
                    return title, price, price, 0, currency

                return title, None, None, 0, ""

        except Exception as e:
            logger.error(f"Ошибка парсинга Steam {url}: {e}")
            return None, None, None, 0, ""


# --- ИНИЦИАЛИЗАЦИЯ И КЛАВИАТУРЫ ---

BOT_TOKEN = os.getenv("BOT_TOKEN", "8616500960:AAG8IFKucBCHWFwZjnRirO25PU36PdFxdsk")
CHAT_ID = os.getenv("CHAT_ID", "791444289")
DB_PATH = os.getenv("DB_PATH", "monitor.db")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "300"))

db = Database(DB_PATH)
scraper = ScraperService()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def get_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Список игр")],
            [KeyboardButton(text="➕ Добавить игру"), KeyboardButton(text="❓ Помощь")]
        ],
        resize_keyboard=True
    )


def get_delete_inline_btn(product_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🗑️ Удалить из отслеживания", 
                callback_data=f"delete_{product_id}"  # <-- Важно: префикс delete_
            )
        ]
    ])

# --- ХЭНДЛЕРЫ БОТА ---

from aiogram.types import ReplyKeyboardRemove

@dp.message(Command("start"))
@dp.message(F.text == "❓ Помощь")
async def cmd_start(message: types.Message):
    text = (
        "🎮 <b>Мониторинг скидок Steam</b>\n\n"
        "Вам больше не нужно отслеживать распродажи вручную!\n"
        "Отправьте название или ссылку на игру в Steam, и бот будет следить за скидками.\n\n"
        "<b>Примеры:</b>\n"
        "• <code>/add Witcher 3</code>\n"
        "• <code>/add https://store.steampowered.com/app/271590/Grand_Theft_Auto_V/</code>"
    )

    # Отправляем текст приветствия и одновременно убираем старую нижнюю клавиатуру у пользователя
    await message.answer(
        text, 
        parse_mode="HTML", 
        reply_markup=ReplyKeyboardRemove()
    )
@dp.message(F.text == "➕ Добавить игру")
async def cmd_how_to_add(message: types.Message):
    text = (
        "📝 <b>Добавление игры:</b>\n\n"
        "Отправьте название игры или прямую ссылку командой:\n"
        "<code>/add Название игры</code>\n\n"
        "<b>Пример:</b> <code>/add GTA 5</code>"
    )
    await message.answer(text, parse_mode="HTML")


@dp.message(Command("add"))
async def cmd_add(message: types.Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "❌ <b>Ошибка!</b> Укажите название игры или ссылку:\n<code>/add Witcher 3</code>",
            parse_mode="HTML"
        )
        return

    user_input = args[1].strip()
    loading_msg = await message.answer("🔍 <i>Поиск игры в Steam...</i>", parse_mode="HTML")

    async with aiohttp.ClientSession() as session:
        # Если передан URL — используем его, иначе ищем через поиск Steam
        if user_input.startswith("http://") or user_input.startswith("https://"):
            target_url = user_input
        else:
            target_url = await scraper.search_steam_game(session, user_input)

        if not target_url:
            await loading_msg.edit_text(
                f"❌ <b>Игра «{html.escape(user_input)}» не найдена в Steam.</b>\n"
                "Попробуйте уточнить название или отправить прямую ссылку.",
                parse_mode="HTML"
            )
            return

        # Пробуем сохранить ссылку в базу
        success = await db.add_product(target_url)
        if not success:
            await loading_msg.edit_text("⚠️ Эта игра уже есть в вашем списке отслеживания.")
            return

        # Парсим цены и данные со страницы Steam
        await loading_msg.edit_text("⏳ <i>Игра найдена! Получаем цены...</i>", parse_mode="HTML")
        title, orig_price, current_price, discount_pct, currency = await scraper.fetch_steam_game(session, target_url)

    products = await db.get_all_products()
    added_product = next((p for p in products if p.url == target_url), None)

    if added_product and current_price is not None:
        title_to_save = title or user_input
        await db.update_product_data(
            added_product.id,
            title_to_save,
            orig_price or current_price,
            current_price,
            discount_pct,
            currency
        )

        curr = f" {currency}" if currency else ""
        safe_title = html.escape(title_to_save)
        safe_url = html.escape(target_url)

        if discount_pct > 0:
            price_info = (
                f"🏷️ Оригинальная цена: <s>{orig_price}{curr}</s>\n"
                f"🔥 <b>Скидка {discount_pct}%!</b>\n"
                f"💰 Текущая цена: <b>{current_price}{curr}</b>"
            )
        else:
            price_info = f"💰 Текущая цена: <b>{current_price}{curr}</b> (без скидки)"

        await loading_msg.edit_text(
            f"✅ <b>Игра успешно добавлена!</b>\n\n"
            f"🎮 <b>{safe_title}</b>\n"
            f"{price_info}\n\n"
            f"🔗 <a href='{safe_url}'>Открыть в Steam</a>",
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    else:
        await loading_msg.edit_text(
            "✅ <b>Игра сохранена!</b>\n⚠️ Не удалось сразу прочитать цену. Бот подтянет данные при следующей фоновой проверке.",
            parse_mode="HTML"
        )


@dp.message(Command("list"))
@dp.message(F.text == "📋 Список игр")
async def cmd_list(message: types.Message):
    try:
        user_products = await db.get_user_products(message.from_user.id)
        if not products:
            await message.answer("📭 Список отслеживаемых игр пуст.", reply_markup=get_main_keyboard())
            return

        await message.answer("📋 <b>Отслеживаемые игры Steam:</b>", parse_mode="HTML")

        for p in products:
            raw_title = p.title or "Новая игра (идет получение данных...)"
            title = html.escape(raw_title)
            safe_url = html.escape(p.url)
            curr = f" {p.currency}" if p.currency else ""

            if p.current_price is not None:
                if p.discount_percent > 0:
                    price_block = (
                        f"🏷️ Оригинальная цена: <s>{p.original_price}{curr}</s>\n"
                        f"🔥 <b>Скидка {p.discount_percent}%!</b>\n"
                        f"💰 Текущая цена: <b>{p.current_price}{curr}</b>"
                    )
                else:
                    price_block = f"💰 Текущая цена: <b>{p.current_price}{curr}</b> (без скидки)"
            else:
                price_block = "⏳ <i>Данные обновляются...</i>"

            item_text = (
                f"🎮 <b>{title}</b>\n"
                f"{price_block}\n"
                f"🔗 <a href='{safe_url}'>Открыть в Steam</a>"
            )

            await message.answer(
                item_text,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=get_delete_inline_btn(p.id)
            )
    except Exception as e:
        logger.error(f"Ошибка при выводе списка игр: {e}")
        await message.answer("⚠️ Произошла ошибка при получении списка игр.")


@dp.callback_query(F.data.startswith("delete_"))
async def process_delete_game(callback: CallbackQuery):
    try:
        # Достаем ID игры из "delete_12" -> 12
        product_id = int(callback.data.split("_")[1])
        
        # Вызываем ваш метод из БД
        success = await db.delete_product(product_id)
        
        if success:
            # Обязательно гасим анимацию загрузки на кнопке
            await callback.answer("Игра удалена из списка!")
            # Обновляем текст сообщения
            await callback.message.edit_text("🗑️ <i>Игра удалена из отслеживания.</i>", parse_mode="HTML")
        else:
            await callback.answer("⚠️ Игра не найдена в базе.", show_alert=True)
            
    except Exception as e:
        logger.error(f"Ошибка при удалении: {e}")
        await callback.answer("❌ Произошла ошибка при удалении.", show_alert=True)
        

@dp.message(Command("deals"))
@dp.message(F.text == "🔥 Скидки")
async def cmd_deals(message: types.Message):
    try:
        products = await db.get_all_products()
        deals = [p for p in products if p.discount_percent > 0]
        
        if not deals:
            await message.answer("📭 Прямо сейчас в вашем списке нет игр со скидками.")
            return

        await message.answer("🔥 <b>Игры со скидками в вашем списке:</b>", parse_mode="HTML")
        
        for p in deals:
            title = html.escape(p.title or "Игра Steam")
            safe_url = html.escape(p.url)
            curr = f" {p.currency}" if p.currency else ""
            
            item_text = (
                f"🎮 <b>{title}</b>\n"
                f"🏷️ <s>{p.original_price}{curr}</s> ➔ <b>{p.current_price}{curr}</b>\n"
                f"💥 Скидка: <b>-{p.discount_percent}%</b>\n"
                f"🔗 <a href='{safe_url}'>Открыть в Steam</a>"
            )
            await message.answer(
                item_text,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=get_delete_inline_btn(p.id)
            )
    except Exception as e:
        logger.error(f"Ошибка в /deals: {e}")
        await message.answer("⚠️ Произошла ошибка при загрузке скидок.")

@dp.message(Command("check"))
async def cmd_check(message: types.Message):
    status_msg = await message.answer("🔄 <i>Запущена принудительная проверка цен... Пожалуйста, подождите.</i>", parse_mode="HTML")
    checked_count = 0
    updated_count = 0
    
    try:
        products = await db.get_all_products()
        if not products:
            await status_msg.edit_text("📭 Список отслеживания пуст, проверять нечего.")
            return

        async with aiohttp.ClientSession() as session:
            for product in products:
                title, orig_price, current_price, discount_pct, currency = await scraper.fetch_steam_game(session, product.url)
                if current_price is not None:
                    title_to_save = title or product.title or "Игра Steam"
                    if product.id is not None:
                        await db.update_product_data(
                            product.id, title_to_save, orig_price or current_price, current_price, discount_pct, currency
                        )
                    updated_count += 1
                checked_count += 1

        await status_msg.edit_text(
            f"✅ <b>Проверка успешно завершена!</b>\n\n"
            f"• Проверено игр: <b>{checked_count}</b>\n"
            f"• Обновлено данных: <b>{updated_count}</b>",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка в /check: {e}")
        await status_msg.edit_text("⚠️ Произошла ошибка при проверке цен.")

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    try:
        products = await db.get_all_products()
        total_count = len(products)
        if total_count == 0:
            await message.answer("📊 <b>Статистика мониторинга:</b>\n\nСписок отслеживания пуст.", parse_mode="HTML")
            return

        deals_count = sum(1 for p in products if p.discount_percent > 0)
        
        text = (
            f"📊 <b>Статистика вашего Steam-монитора:</b>\n\n"
            f"🎮 Всего игр в списке: <b>{total_count}</b>\n"
            f"🔥 Игр со скидками: <b>{deals_count}</b>\n"
            f"📌 Без скидок / обычная цена: <b>{total_count - deals_count}</b>\n\n"
            f"⏱️ Интервал фоновой проверки: <b>{CHECK_INTERVAL // 60} мин.</b>"
        )
        await message.answer(text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Ошибка в /stats: {e}")
        await message.answer("⚠️ Ошибка при получении статистики.")

@dp.message(Command("clear"))
async def cmd_clear(message: types.Message):
    products = await db.get_all_products()
    if not products:
        await message.answer("📭 Список и так пуст.")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить всё", callback_data="confirm_clear"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_clear")
        ]
    ])
    await message.answer(
        f"⚠️ <b>Внимание!</b> Вы действительно хотите удалить все игры (<b>{len(products)} шт.</b>) из списка отслеживания?",
        parse_mode="HTML",
        reply_markup=keyboard
    )

@dp.callback_query(F.data == "confirm_clear")
async def process_confirm_clear(callback: CallbackQuery):
    try:
        count = await db.clear_all_products()
        await callback.answer("🗑️ Список полностью очищен!")
        await callback.message.edit_text(f"🗑️ <b>Список отслеживания очищен.</b> Удалено игр: {count}.", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Ошибка при очистке БД: {e}")
        await callback.answer("❌ Ошибка при очистке базы данных.", show_alert=True)

@dp.callback_query(F.data == "cancel_clear")
async def process_cancel_clear(callback: CallbackQuery):
    await callback.answer("Отменено")
    await callback.message.edit_text("❌ Очистка списка отменена.")


# --- ФОНОВЫЙ МОНИТОРИНГ ---

async def price_monitoring_loop():
    while True:
        try:
            products = await db.get_all_products()
            if products:
                async with aiohttp.ClientSession() as session:
                    for product in products:
                        title, orig_price, current_price, discount_pct, currency = await scraper.fetch_steam_game(
                            session, product.url
                        )
                        if current_price is None:
                            continue

                        title_to_save = title or product.title or "Игра Steam"
                        old_discount = product.discount_percent
                        is_first_run = product.current_price is None

                        if product.id is not None:
                            await db.update_product_data(
                                product.id,
                                title_to_save,
                                orig_price or current_price,
                                current_price,
                                discount_pct,
                                currency
                            )

                        # Уведомляем, если началась распродажа или увеличилась скидка
                        if not is_first_run and CHAT_ID:
                            if discount_pct > old_discount and discount_pct > 0:
                                curr_str = f" {currency}" if currency else ""
                                safe_title = html.escape(title_to_save)
                                safe_url = html.escape(product.url)
                                alert_msg = (
                                    f"🔥 <b>В STEAM НАЧАЛАСЬ РАСПРОДАЖА!</b>\n\n"
                                    f"🎮 <b>{safe_title}</b>\n"
                                    f"💥 Скидка: <b>-{discount_pct}%</b>\n"
                                    f"🏷️ Старая цена: <s>{orig_price}{curr_str}</s>\n"
                                    f"✅ Новая цена: <b>{current_price}{curr_str}</b>\n\n"
                                    f"🔗 <a href='{safe_url}'>Купить в Steam</a>"
                                )
                                await bot.send_message(chat_id=product.user_id, text=alert_msg, parse_mode="HTML")

        except Exception as e:
            logger.error(f"Ошибка в цикле проверки: {e}")

        await asyncio.sleep(CHECK_INTERVAL)


# --- ТОЧКА ВХОДА ---

async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand(command="start", description="🚀 Перезапустить бота / Меню"),
        BotCommand(command="add", description="➕ Добавить игру (название или ссылка)"),
        BotCommand(command="list", description="📋 Список всех игр"),
        BotCommand(command="deals", description="🔥 Игры со скидками прямо сейчас"),
        BotCommand(command="check", description="🔄 Принудительно проверить цены"),
        BotCommand(command="stats", description="📊 Статистика отслеживания"),
        BotCommand(command="clear", description="🗑️ Очистить весь список"),
        BotCommand(command="help", description="❓ Справка и помощь"),
    ]
    await bot.set_my_commands(commands)

async def main():
    await db.init_db()
    await set_bot_commands(bot)  # <-- Регистрируем меню команд в Telegram
    logger.info("База данных готова и меню команд обновлено. Запуск Steam-монитора...")

    asyncio.create_task(price_monitoring_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Программа остановлена.")