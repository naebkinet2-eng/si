
#  TikTok Rewards Bot — весь код в одном файле
#  Запуск: python bot.py
#  Хостинг: Render (render.yaml) — бесплатно
# ============================================================

import asyncio
import logging
import os
import random
import re
import string

import aiosqlite
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  КОНФИГУРАЦИЯ
# ─────────────────────────────────────────────

BOT_TOKEN = os.getenv("BOT_TOKEN", "8206313463:AAFxbANmtioF9T0zo1glaUwUrcehayVGoIE")
OPERATOR_USERNAME = os.getenv("OPERATOR_USERNAME", "@OldSIWs")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "0").split(",")))

POINTS_FOR_50  = 100
POINTS_FOR_100 = 250
POINTS_FOR_200 = 600

CODE_PRICE_POINTS = 500
CODE_PRICE_STARS  = 50   # Telegram Stars

DB_PATH = os.getenv("DB_PATH", "bot_database.db")

TASK_CONFIG = {
    "task_50":  {"count": 50,  "reward": POINTS_FOR_50,  "label": "50 скриншотов"},
    "task_100": {"count": 100, "reward": POINTS_FOR_100, "label": "100 скриншотов"},
    "task_200": {"count": 200, "reward": POINTS_FOR_200, "label": "200 скриншотов"},
}


# ─────────────────────────────────────────────
#  БАЗА ДАННЫХ
# ─────────────────────────────────────────────

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                points INTEGER DEFAULT 0,
                tiktok_username TEXT DEFAULT NULL,
                screenshots_submitted INTEGER DEFAULT 0,
                codes_generated INTEGER DEFAULT 0,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                code TEXT UNIQUE,
                used INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS screenshot_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                task_type TEXT,
                status TEXT DEFAULT 'pending',
                submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                type TEXT,
                amount INTEGER,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        await db.commit()
    logger.info("✅ Database initialized")


async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cur:
            return await cur.fetchone()


async def create_user(user_id: int, username: str, full_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)",
            (user_id, username, full_name),
        )
        await db.commit()


async def update_tiktok(user_id: int, tiktok_username: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET tiktok_username = ? WHERE user_id = ?",
            (tiktok_username, user_id),
        )
        await db.commit()


async def add_points(user_id: int, points: int, description: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET points = points + ? WHERE user_id = ?",
            (points, user_id),
        )
        await db.execute(
            "INSERT INTO transactions (user_id, type, amount, description) VALUES (?, 'earn', ?, ?)",
            (user_id, points, description),
        )
        await db.commit()


async def spend_points(user_id: int, points: int, description: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT points FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            if not row or row["points"] < points:
                return False
        await db.execute(
            "UPDATE users SET points = points - ? WHERE user_id = ?",
            (points, user_id),
        )
        await db.execute(
            "INSERT INTO transactions (user_id, type, amount, description) VALUES (?, 'spend', ?, ?)",
            (user_id, points, description),
        )
        await db.commit()
        return True


async def save_code(user_id: int, code: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO codes (user_id, code) VALUES (?, ?)",
            (user_id, code),
        )
        await db.execute(
            "UPDATE users SET codes_generated = codes_generated + 1 WHERE user_id = ?",
            (user_id,),
        )
        await db.commit()


async def add_screenshots(user_id: int, count: int, task_type: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET screenshots_submitted = screenshots_submitted + ? WHERE user_id = ?",
            (count, user_id),
        )
        await db.execute(
            "INSERT INTO screenshot_tasks (user_id, task_type, status) VALUES (?, ?, 'approved')",
            (user_id, task_type),
        )
        await db.commit()


async def get_all_users():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users ORDER BY points DESC") as cur:
            return await cur.fetchall()


async def get_user_codes(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM codes WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
        ) as cur:
            return await cur.fetchall()


# ─────────────────────────────────────────────
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ─────────────────────────────────────────────

def generate_code(length: int = 20) -> str:
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=length))


def format_code(code: str) -> str:
    return "-".join([code[i : i + 4] for i in range(0, len(code), 4)])


def validate_tiktok_username(username: str):
    username = username.strip().lstrip("@")
    if re.match(r"^[a-zA-Z0-9_.]{2,24}$", username):
        return username
    return None


def points_to_emoji(points: int) -> str:
    if points >= 5000: return "👑"
    if points >= 2000: return "💎"
    if points >= 1000: return "🥇"
    if points >= 500:  return "🥈"
    if points >= 100:  return "🥉"
    return "🌱"


def get_tier_name(points: int) -> str:
    if points >= 5000: return "Легенда"
    if points >= 2000: return "Бриллиант"
    if points >= 1000: return "Золото"
    if points >= 500:  return "Серебро"
    if points >= 100:  return "Бронза"
    return "Новичок"


def get_progress_bar(points: int) -> str:
    for tier in [100, 500, 1000, 2000, 5000]:
        if points < tier:
            p = int((points / tier) * 10)
            return f"{'🟩' * p}{'⬜' * (10 - p)} {points}/{tier}"
    return "🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩 MAX"


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS and ADMIN_IDS != [0]


# ─────────────────────────────────────────────
#  КЛАВИАТУРЫ
# ─────────────────────────────────────────────

def main_menu_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👤 Мой профиль",           callback_data="profile"),
            InlineKeyboardButton(text="📊 Задания",               callback_data="tasks"),
        ],
        [
            InlineKeyboardButton(text="🛒 Магазин",               callback_data="shop"),
            InlineKeyboardButton(text="📸 Отправить скриншоты",   callback_data="screenshots"),
        ],
        [
            InlineKeyboardButton(text="🎵 Подключить TikTok",     callback_data="connect_tiktok"),
        ],
        [
            InlineKeyboardButton(text="❓ Помощь",                callback_data="help"),
        ],
    ])


def back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu")]
    ])


def tasks_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📸 50 скринов → 100 💎",  callback_data="task_50")],
        [InlineKeyboardButton(text="📸 100 скринов → 250 💎", callback_data="task_100")],
        [InlineKeyboardButton(text="📸 200 скринов → 600 💎", callback_data="task_200")],
        [InlineKeyboardButton(text="🔙 Назад",                callback_data="main_menu")],
    ])


def shop_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Купить код за 500 💎",     callback_data="buy_code_points")],
        [InlineKeyboardButton(text="⭐ Купить код за 50 звёзд",   callback_data="buy_code_stars")],
        [InlineKeyboardButton(text="📋 Мои коды",                 callback_data="my_codes")],
        [InlineKeyboardButton(text="🔙 Назад",                    callback_data="main_menu")],
    ])


# ─────────────────────────────────────────────
#  FSM СОСТОЯНИЯ
# ─────────────────────────────────────────────

class ConnectTikTok(StatesGroup):
    waiting_for_username = State()


class ScreenshotUpload(StatesGroup):
    waiting_for_screenshots = State()
    confirming = State()


# ─────────────────────────────────────────────
#  РОУТЕР
# ─────────────────────────────────────────────

router = Router()


# ══════════════════════════════════════════════
#  /start  —  ПРИВЕТСТВИЕ
# ══════════════════════════════════════════════

WELCOME_TEXT = """
🌟 *Добро пожаловать в TikTok Rewards!* 🌟

Привет, {name}! Ты попал в место, где твоя активность в TikTok превращается в реальные награды!

━━━━━━━━━━━━━━━━━━━
🎯 *Как это работает:*
━━━━━━━━━━━━━━━━━━━

1️⃣ *Подключи TikTok аккаунт* — привяжи свой профиль для старта

2️⃣ *Выполняй задания* — отправляй скриншоты своей активности в TikTok и получай 💎 баллы

3️⃣ *Трать баллы* — обменивай накопленные баллы на уникальные коды

4️⃣ *Получи награду* — отправь код оператору и забери свой приз!

━━━━━━━━━━━━━━━━━━━
💡 *Структура заданий:*

📸 50 скриншотов  → 100 💎 баллов
📸 100 скриншотов → 250 💎 баллов
📸 200 скриншотов → 600 💎 баллов

━━━━━━━━━━━━━━━━━━━

Готов начать? Нажми кнопку ниже! 👇
"""

RETURNING_TEXT = """
👋 *С возвращением, {name}!*

Твой баланс: *{points} 💎*
TikTok: {tiktok}

Выбери действие 👇
"""


@router.message(CommandStart())
async def cmd_start(message: Message):
    user = await get_user(message.from_user.id)
    if not user:
        await create_user(
            message.from_user.id,
            message.from_user.username or "",
            message.from_user.full_name,
        )
        await message.answer(
            WELCOME_TEXT.format(name=message.from_user.first_name),
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )
    else:
        tiktok = f"@{user['tiktok_username']}" if user["tiktok_username"] else "❌ Не подключён"
        await message.answer(
            RETURNING_TEXT.format(
                name=message.from_user.first_name,
                points=user["points"],
                tiktok=tiktok,
            ),
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )


@router.callback_query(F.data == "main_menu")
async def back_to_main(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    tiktok = f"@{user['tiktok_username']}" if user["tiktok_username"] else "❌ Не подключён"
    await callback.message.edit_text(
        RETURNING_TEXT.format(
            name=callback.from_user.first_name,
            points=user["points"],
            tiktok=tiktok,
        ),
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "help")
async def help_handler(callback: CallbackQuery):
    text = f"""
❓ *Помощь*

━━━━━━━━━━━━━━━━━━━
📌 *Частые вопросы:*
━━━━━━━━━━━━━━━━━━━

*Как подключить TikTok?*
Нажми «🎵 Подключить TikTok» и отправь свой никнейм

*Какие скриншоты нужны?*
Скриншоты из приложения TikTok — публикации, просмотры, активность на своём аккаунте

*Как получить приз?*
1. Накопи 500+ 💎 баллов
2. Купи уникальный код в магазине
3. Отправь код оператору {OPERATOR_USERNAME}

*Можно ли купить код за деньги?*
Да! Купи напрямую за ⭐ Telegram Stars в магазине

━━━━━━━━━━━━━━━━━━━
💬 *Поддержка:* {OPERATOR_USERNAME}
    """
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=back_keyboard())
    await callback.answer()


# ══════════════════════════════════════════════
#  ПРОФИЛЬ
# ══════════════════════════════════════════════

@router.callback_query(F.data == "profile")
async def profile_handler(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer("Профиль не найден. Напиши /start", show_alert=True)
        return

    tiktok = f"✅ @{user['tiktok_username']}" if user["tiktok_username"] else "❌ Не подключён"
    text = f"""
👤 *Мой профиль*
━━━━━━━━━━━━━━━━━━━

🏷 *Имя:* {user['full_name']}
🆔 *ID:* `{user['user_id']}`
🎵 *TikTok:* {tiktok}

━━━━━━━━━━━━━━━━━━━
{points_to_emoji(user['points'])} *Статус:* {get_tier_name(user['points'])}
💎 *Баллы:* {user['points']}
📸 *Скриншотов сдано:* {user['screenshots_submitted']}
🎁 *Кодов получено:* {user['codes_generated']}
━━━━━━━━━━━━━━━━━━━

📈 *До следующего уровня:*
{get_progress_bar(user['points'])}
    """
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=back_keyboard())
    await callback.answer()


# ══════════════════════════════════════════════
#  ЗАДАНИЯ
# ══════════════════════════════════════════════

@router.callback_query(F.data == "tasks")
async def tasks_handler(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer("Сначала напиши /start", show_alert=True)
        return

    if not user["tiktok_username"]:
        await callback.message.edit_text(
            "⚠️ *Сначала подключи TikTok аккаунт!*\n\n"
            "Нажми «🎵 Подключить TikTok» в главном меню.",
            parse_mode="Markdown",
            reply_markup=back_keyboard(),
        )
        await callback.answer()
        return

    text = f"""
📊 *Задания*
━━━━━━━━━━━━━━━━━━━

Твой TikTok: @{user['tiktok_username']}
Твои баллы: {user['points']} 💎

━━━━━━━━━━━━━━━━━━━
📋 *Доступные задания:*

📸 *50 скриншотов*  → 100 💎
📸 *100 скриншотов* → 250 💎
📸 *200 скриншотов* → 600 💎

━━━━━━━━━━━━━━━━━━━
💡 *Что нужно скринить?*
• Свои видео в TikTok (просмотры, лайки)
• Уведомления активности
• Статистику профиля
• Комментарии и взаимодействия

Выбери задание 👇
    """
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=tasks_keyboard())
    await callback.answer()


@router.callback_query(F.data.in_({"task_50", "task_100", "task_200"}))
async def task_selected(callback: CallbackQuery):
    task_type = callback.data
    cfg = TASK_CONFIG[task_type]

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📸 Перейти к загрузке", callback_data=f"start_upload_{task_type}")],
        [InlineKeyboardButton(text="🔙 Назад",              callback_data="tasks")],
    ])
    text = f"""
📸 *Задание: {cfg['label']}*
━━━━━━━━━━━━━━━━━━━

🎯 *Награда:* {cfg['reward']} 💎 баллов

━━━━━━━━━━━━━━━━━━━
📌 *Инструкция:*

1. Сделай {cfg['count']} скриншотов своей TikTok активности
2. Отправляй скриншоты боту (по 1 или альбомами до 10 штук)
3. После отправки всех фото нажми «✅ Готово»

⚠️ *Важно:*
• Скриншоты должны быть из TikTok
• На каждом должен быть виден интерфейс TikTok
• Дубликаты не засчитываются

Нажми кнопку ниже, чтобы начать 👇
    """
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)
    await callback.answer()


# ══════════════════════════════════════════════
#  СКРИНШОТЫ (FSM)
# ══════════════════════════════════════════════

@router.callback_query(F.data == "screenshots")
async def screenshots_menu(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user or not user["tiktok_username"]:
        await callback.message.edit_text(
            "⚠️ *Сначала подключи TikTok!*\n\nВернись в главное меню и нажми «🎵 Подключить TikTok»",
            parse_mode="Markdown",
            reply_markup=back_keyboard(),
        )
        await callback.answer()
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📸 50 скринов  (+100 💎)",  callback_data="start_upload_task_50")],
        [InlineKeyboardButton(text="📸 100 скринов (+250 💎)",  callback_data="start_upload_task_100")],
        [InlineKeyboardButton(text="📸 200 скринов (+600 💎)",  callback_data="start_upload_task_200")],
        [InlineKeyboardButton(text="🔙 Назад",                  callback_data="main_menu")],
    ])
    text = """
📸 *Отправить скриншоты*
━━━━━━━━━━━━━━━━━━━

Выбери задание и отправь скриншоты из TikTok.

*Как отправить много скриншотов:*
• В Telegram можно отправить до 10 фото за раз (альбом)
• Отправляй несколько альбомов подряд
• После отправки всех — нажми «Готово»

Выбери задание 👇
    """
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("start_upload_"))
async def start_upload(callback: CallbackQuery, state: FSMContext):
    task_type = callback.data.replace("start_upload_", "")
    if task_type not in TASK_CONFIG:
        await callback.answer("Неверное задание", show_alert=True)
        return

    cfg = TASK_CONFIG[task_type]
    await state.set_state(ScreenshotUpload.waiting_for_screenshots)
    await state.update_data(task_type=task_type, received_count=0)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Готово, проверить!", callback_data="check_screenshots")],
        [InlineKeyboardButton(text="❌ Отмена",             callback_data="cancel_upload")],
    ])
    text = f"""
📸 *Загрузка скриншотов*
━━━━━━━━━━━━━━━━━━━

🎯 Задание: *{cfg['label']}*
💎 Награда: *{cfg['reward']} баллов*

━━━━━━━━━━━━━━━━━━━
👇 *Начни отправлять скриншоты прямо сейчас!*

Нужно: {cfg['count']} скриншотов из TikTok.
Можно отправлять альбомами (до 10 за раз).

После отправки всех фото нажми «✅ Готово»
    """
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)
    await callback.answer()


@router.message(ScreenshotUpload.waiting_for_screenshots, F.photo)
async def receive_screenshot(message: Message, state: FSMContext):
    data = await state.get_data()
    new_count = data.get("received_count", 0) + 1
    await state.update_data(received_count=new_count)

    cfg = TASK_CONFIG[data.get("task_type", "task_50")]
    needed = cfg["count"]

    if new_count % 10 == 0 or new_count >= needed:
        remaining = needed - new_count
        status = "✅ Достаточно! Нажми «Готово»" if new_count >= needed else f"Продолжай, нужно ещё {remaining}"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Готово, проверить!", callback_data="check_screenshots")],
            [InlineKeyboardButton(text="❌ Отмена",             callback_data="cancel_upload")],
        ])
        await message.answer(
            f"📊 Получено: *{new_count}/{needed}* скриншотов\n{status}",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )


@router.callback_query(F.data == "check_screenshots", ScreenshotUpload.waiting_for_screenshots)
async def check_screenshots_cb(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    count  = data.get("received_count", 0)
    cfg    = TASK_CONFIG[data.get("task_type", "task_50")]
    needed = cfg["count"]

    if count < needed:
        await callback.answer(
            f"⚠️ Недостаточно скриншотов!\nПолучено: {count}, нужно: {needed}",
            show_alert=True,
        )
        return

    await state.set_state(ScreenshotUpload.confirming)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить и получить баллы", callback_data="confirm_screenshots")],
        [InlineKeyboardButton(text="❌ Отмена",                       callback_data="cancel_upload")],
    ])
    await callback.message.edit_text(
        f"🎉 *Отлично!*\n\n"
        f"Получено скриншотов: *{count}* ✅\n"
        f"Нужно было: *{needed}*\n\n"
        f"Награда: *{cfg['reward']} 💎 баллов*\n\n"
        f"Подтверди получение баллов 👇",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data == "confirm_screenshots", ScreenshotUpload.confirming)
async def confirm_screenshots_cb(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cfg   = TASK_CONFIG[data.get("task_type", "task_50")]
    count = data.get("received_count", 0)
    user  = await get_user(callback.from_user.id)

    await add_screenshots(callback.from_user.id, count, data.get("task_type"))
    await add_points(callback.from_user.id, cfg["reward"], f"Задание: {cfg['label']}")
    await state.clear()

    await callback.message.edit_text(
        f"🎊 *Баллы начислены!*\n\n"
        f"✅ Задание выполнено: {cfg['label']}\n"
        f"💎 Начислено: +{cfg['reward']} баллов\n"
        f"💰 Твой баланс: {user['points'] + cfg['reward']} 💎\n\n"
        f"Продолжай выполнять задания и зарабатывай больше!",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer("✅ Баллы начислены!", show_alert=True)


@router.callback_query(F.data == "cancel_upload")
async def cancel_upload(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "❌ Загрузка отменена.\n\nВернись в главное меню.",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


# ══════════════════════════════════════════════
#  ПОДКЛЮЧЕНИЕ TIKTOK (FSM)
# ══════════════════════════════════════════════

@router.callback_query(F.data == "connect_tiktok")
async def connect_tiktok_cb(callback: CallbackQuery, state: FSMContext):
    user    = await get_user(callback.from_user.id)
    current = (
        f"\n\n✅ *Текущий аккаунт:* @{user['tiktok_username']}"
        if user and user["tiktok_username"]
        else ""
    )
    await state.set_state(ConnectTikTok.waiting_for_username)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_tiktok")]
    ])
    await callback.message.edit_text(
        f"🎵 *Подключение TikTok*\n"
        f"━━━━━━━━━━━━━━━━━━━{current}\n\n"
        f"Отправь свой TikTok никнейм (без @)\n\n"
        f"Пример: `myawesomeprofile`",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.message(ConnectTikTok.waiting_for_username)
async def receive_tiktok_username(message: Message, state: FSMContext):
    username = validate_tiktok_username(message.text or "")
    if not username:
        await message.answer(
            "⚠️ *Неверный формат никнейма!*\n\n"
            "Используй только: буквы, цифры, точки и подчёркивания\n"
            "Длина: 2–24 символа\n\n"
            "Попробуй ещё раз:",
            parse_mode="Markdown",
        )
        return

    await update_tiktok(message.from_user.id, username)
    await state.clear()
    await message.answer(
        f"✅ *TikTok подключён!*\n\n"
        f"🎵 Аккаунт: @{username}\n\n"
        f"Теперь ты можешь выполнять задания и зарабатывать баллы!\n"
        f"Перейди в раздел «📊 Задания»",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )


@router.callback_query(F.data == "cancel_tiktok")
async def cancel_tiktok(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user   = await get_user(callback.from_user.id)
    tiktok = f"@{user['tiktok_username']}" if user and user["tiktok_username"] else "❌ Не подключён"
    await callback.message.edit_text(
        f"👤 *Главное меню*\n\nTikTok: {tiktok}",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


# ══════════════════════════════════════════════
#  МАГАЗИН
# ══════════════════════════════════════════════

@router.callback_query(F.data == "shop")
async def shop_handler(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer("Сначала /start", show_alert=True)
        return

    text = f"""
🛒 *Магазин*
━━━━━━━━━━━━━━━━━━━

💎 *Твои баллы:* {user['points']}

━━━━━━━━━━━━━━━━━━━
🎁 *Уникальный код* — 20 символов.
Отправь оператору {OPERATOR_USERNAME} и получи награду!

💎 За баллы: *{CODE_PRICE_POINTS} 💎*
⭐ За звёзды: *{CODE_PRICE_STARS} ⭐ Telegram Stars*

━━━━━━━━━━━━━━━━━━━
Выбери способ покупки 👇
    """
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=shop_keyboard())
    await callback.answer()


@router.callback_query(F.data == "buy_code_points")
async def buy_code_points_cb(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer("Ошибка", show_alert=True)
        return

    if user["points"] < CODE_PRICE_POINTS:
        await callback.answer(
            f"⚠️ Недостаточно баллов!\nНужно: {CODE_PRICE_POINTS} 💎\nУ тебя: {user['points']} 💎",
            show_alert=True,
        )
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Потратить {CODE_PRICE_POINTS} 💎", callback_data="confirm_buy_points")],
        [InlineKeyboardButton(text="❌ Отмена",                            callback_data="shop")],
    ])
    await callback.message.edit_text(
        f"🎁 *Подтверди покупку*\n\n"
        f"Стоимость: *{CODE_PRICE_POINTS} 💎 баллов*\n"
        f"Твой баланс: *{user['points']} 💎*\n"
        f"После покупки: *{user['points'] - CODE_PRICE_POINTS} 💎*\n\n"
        f"Ты получишь уникальный код из 20 символов.",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data == "confirm_buy_points")
async def confirm_buy_points_cb(callback: CallbackQuery):
    success = await spend_points(callback.from_user.id, CODE_PRICE_POINTS, "Покупка кода")
    if not success:
        await callback.answer("⚠️ Недостаточно баллов!", show_alert=True)
        return

    code      = generate_code()
    formatted = format_code(code)
    await save_code(callback.from_user.id, code)

    text = f"""
🎉 *Код успешно получен!*
━━━━━━━━━━━━━━━━━━━

🔑 *Твой уникальный код:*
`{formatted}`

━━━━━━━━━━━━━━━━━━━
📌 *Что делать дальше:*
1. Скопируй код выше
2. Напиши оператору: {OPERATOR_USERNAME}
3. Отправь ему этот код
4. Получи свою награду! 🎁

⚠️ Код одноразовый, не теряй его!
    """
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=back_keyboard())
    await callback.answer("✅ Код выдан!", show_alert=True)


@router.callback_query(F.data == "buy_code_stars")
async def buy_code_stars_cb(callback: CallbackQuery, bot: Bot):
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title="🎁 Уникальный код",
        description=f"Уникальный код из 20 символов для обмена у оператора {OPERATOR_USERNAME}",
        payload="buy_code_stars",
        currency="XTR",
        prices=[LabeledPrice(label="Уникальный код", amount=CODE_PRICE_STARS)],
    )
    await callback.answer()


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery, bot: Bot):
    await bot.answer_pre_checkout_query(query.id, ok=True)


@router.message(F.successful_payment)
async def successful_payment(message: Message):
    if message.successful_payment.invoice_payload == "buy_code_stars":
        code      = generate_code()
        formatted = format_code(code)
        await save_code(message.from_user.id, code)

        text = f"""
🎉 *Оплата прошла успешно!*
━━━━━━━━━━━━━━━━━━━

⭐ Спасибо за покупку!

🔑 *Твой уникальный код:*
`{formatted}`

━━━━━━━━━━━━━━━━━━━
📌 *Что делать дальше:*
1. Скопируй код выше
2. Напиши оператору: {OPERATOR_USERNAME}
3. Отправь ему этот код
4. Получи свою награду! 🎁

⚠️ Код одноразовый, не теряй его!
        """
        await message.answer(text, parse_mode="Markdown", reply_markup=main_menu_keyboard())


@router.callback_query(F.data == "my_codes")
async def my_codes_handler(callback: CallbackQuery):
    codes = await get_user_codes(callback.from_user.id)
    if not codes:
        await callback.message.edit_text(
            "📋 *Мои коды*\n\nУ тебя пока нет кодов.\n\nКупи код в магазине!",
            parse_mode="Markdown",
            reply_markup=back_keyboard(),
        )
        await callback.answer()
        return

    text = "📋 *Мои коды*\n━━━━━━━━━━━━━━━━━━━\n\n"
    for i, code in enumerate(codes[:10], 1):
        status = "✅ Активен" if not code["used"] else "❌ Использован"
        text += f"*{i}.* `{format_code(code['code'])}`\n{status}\n\n"
    text += f"\n━━━━━━━━━━━━━━━━━━━\nОтправь код оператору: {OPERATOR_USERNAME}"

    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=back_keyboard())
    await callback.answer()


# ══════════════════════════════════════════════
#  АДМИН-ПАНЕЛЬ
# ══════════════════════════════════════════════

@router.message(Command("admin"))
async def admin_panel(message: Message):
    if not is_admin(message.from_user.id):
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Все пользователи", callback_data="admin_users")],
        [InlineKeyboardButton(text="📊 Статистика",       callback_data="admin_stats")],
    ])
    await message.answer("🔧 *Панель администратора*", parse_mode="Markdown", reply_markup=keyboard)


@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return

    users = await get_all_users()
    text = f"""
📊 *Статистика бота*
━━━━━━━━━━━━━━━━━━━

👥 Всего пользователей: *{len(users)}*
🎵 С TikTok: *{sum(1 for u in users if u['tiktok_username'])}*
💎 Всего баллов выдано: *{sum(u['points'] for u in users)}*
📸 Скриншотов принято: *{sum(u['screenshots_submitted'] for u in users)}*
🎁 Кодов выдано: *{sum(u['codes_generated'] for u in users)}*
    """
    await callback.message.edit_text(
        text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
        ]),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return

    users = await get_all_users()
    text = "👥 *Топ пользователей*\n━━━━━━━━━━━━━━━━━━━\n\n"
    for i, u in enumerate(users[:20], 1):
        tiktok = f"@{u['tiktok_username']}" if u["tiktok_username"] else "—"
        text += f"*{i}.* {points_to_emoji(u['points'])} {u['full_name']} — {u['points']} 💎\n   TikTok: {tiktok}\n\n"

    await callback.message.edit_text(
        text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
        ]),
    )
    await callback.answer()


@router.message(Command("addpoints"))
async def add_points_command(message: Message):
    """Usage: /addpoints <user_id> <amount>"""
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Использование: `/addpoints <user_id> <amount>`", parse_mode="Markdown")
        return

    try:
        target_id = int(parts[1])
        amount    = int(parts[2])
    except ValueError:
        await message.answer("⚠️ ID и сумма должны быть числами.")
        return

    user = await get_user(target_id)
    if not user:
        await message.answer("⚠️ Пользователь не найден.")
        return

    await add_points(target_id, amount, "Начислено администратором")
    await message.answer(
        f"✅ Пользователю `{target_id}` начислено *{amount} 💎* баллов",
        parse_mode="Markdown",
    )


# ─────────────────────────────────────────────
#  ЗАПУСК
# ─────────────────────────────────────────────

async def main():
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    dp  = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    logger.info("🤖 Bot started!")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
