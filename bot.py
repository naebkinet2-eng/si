
#  TikTok Rewards Bot — весь код в одном файле
#  Запуск: python bot.py
#  Хостинг: Render (render.yaml) — бесплатно
# ============================================================

import asyncio
import logging
import os
import random
import re
import string # ============================================================
#  TikTok Rewards Bot
#  Хранение данных в памяти, отчёты в Telegram-канале
#  Запуск: python bot.py
# ============================================================

import asyncio
import json
import logging
import os
import random
import re
import string
from typing import Optional

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
    InputMediaPhoto,
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

BOT_TOKEN         = os.getenv("BOT_TOKEN", "8206313463:AAFxbANmtioF9T0zo1glaUwUrcehayVGoIE")
OPERATOR_USERNAME = os.getenv("OPERATOR_USERNAME", "@OldSIWs")
ADMIN_IDS         = list(map(int, os.getenv("ADMIN_IDS", "0").split(",")))
DB_CHANNEL_ID     = int(os.getenv("DB_CHANNEL_ID", "-1003513114819"))

CODE_PRICE_POINTS = 500
CODE_PRICE_STARS  = 50

TASK_CONFIG = {
    "task_50":  {"count": 50,  "reward": 100, "label": "50 скриншотов"},
    "task_100": {"count": 100, "reward": 250, "label": "100 скриншотов"},
    "task_200": {"count": 200, "reward": 600, "label": "200 скриншотов"},
}

# Картинка для главного экрана — красивый TikTok/neon баннер
WELCOME_IMAGE_URL = "https://i.imgur.com/Q8Q7jYK.jpeg"

# ─────────────────────────────────────────────
#  ХРАНИЛИЩЕ В ПАМЯТИ
# ─────────────────────────────────────────────

USERS: dict[int, dict]        = {}
USER_MSG_IDS: dict[int, int]  = {}
USER_CODES: dict[int, list]   = {}
CODES_MSG_IDS: dict[int, int] = {}
INDEX_MSG_ID: Optional[int]   = None
_bot: Optional[Bot]           = None


def default_user(user_id: int, username: str, full_name: str) -> dict:
    return {
        "user_id": user_id,
        "username": username,
        "full_name": full_name,
        "points": 0,
        "tiktok_username": None,
        "screenshots_submitted": 0,
        "codes_generated": 0,
    }


def extract_json(text: str) -> Optional[str]:
    match = re.search(r"```json\s*([\s\S]+?)\s*```", text)
    return match.group(1) if match else None


# ─────────────────────────────────────────────
#  ОТЧЁТ В КАНАЛ ПОСЛЕ ЗАДАНИЯ
# ─────────────────────────────────────────────

async def send_task_report(user_id: int, task_type: str, photo_file_ids: list[str]):
    """Отправляет красивый отчёт в канал после выполнения задания."""
    user = USERS.get(user_id)
    if not user:
        return

    cfg        = TASK_CONFIG[task_type]
    tiktok_url = f"https://tiktok.com/@{user['tiktok_username']}" if user["tiktok_username"] else "не указан"
    tg_nick    = f"@{user['username']}" if user["username"] else "нет username"

    caption = (
        f"✅ *Задание выполнено!*\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 *Telegram:* {user['full_name']} ({tg_nick})\n"
        f"🆔 *ID:* `{user_id}`\n"
        f"🎵 *TikTok:* {tiktok_url}\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📸 *Задание:* {cfg['label']}\n"
        f"💎 *Начислено:* +{cfg['reward']} баллов\n"
        f"💰 *Баланс:* {user['points']} 💎\n"
        f"📊 *Всего скринов сдано:* {user['screenshots_submitted']}"
    )

    try:
        # Берём первые 10 скринов для отчёта
        report_photos = photo_file_ids[:10]

        if len(report_photos) == 1:
            await _bot.send_photo(
                chat_id=DB_CHANNEL_ID,
                photo=report_photos[0],
                caption=caption,
                parse_mode="Markdown",
            )
        elif len(report_photos) > 1:
            # Первое фото с подписью, остальные без
            media = [
                InputMediaPhoto(media=report_photos[0], caption=caption, parse_mode="Markdown"),
                *[InputMediaPhoto(media=fid) for fid in report_photos[1:]]
            ]
            await _bot.send_media_group(chat_id=DB_CHANNEL_ID, media=media)
        else:
            # Нет фото — просто текст
            await _bot.send_message(
                chat_id=DB_CHANNEL_ID,
                text=caption,
                parse_mode="Markdown",
            )

        logger.info(f"📨 Отчёт отправлен в канал для user {user_id}")

    except Exception as e:
        logger.error(f"Ошибка отправки отчёта: {e}")


# ─────────────────────────────────────────────
#  СОХРАНЕНИЕ ДАННЫХ В КАНАЛ (индекс)
# ─────────────────────────────────────────────

async def save_user_to_channel(user_id: int):
    data = USERS.get(user_id)
    if not data:
        return
    text = f"#user_{user_id}\n```json\n{json.dumps(data, ensure_ascii=False, indent=2)}\n```"
    try:
        if user_id in USER_MSG_IDS:
            await _bot.edit_message_text(
                chat_id=DB_CHANNEL_ID,
                message_id=USER_MSG_IDS[user_id],
                text=text,
            )
        else:
            msg = await _bot.send_message(chat_id=DB_CHANNEL_ID, text=text)
            USER_MSG_IDS[user_id] = msg.message_id
            await save_index()
    except Exception as e:
        logger.error(f"save_user {user_id}: {e}")


async def save_codes_to_channel(user_id: int):
    codes = USER_CODES.get(user_id, [])
    text  = f"#codes_{user_id}\n```json\n{json.dumps(codes, ensure_ascii=False, indent=2)}\n```"
    try:
        if user_id in CODES_MSG_IDS:
            await _bot.edit_message_text(
                chat_id=DB_CHANNEL_ID,
                message_id=CODES_MSG_IDS[user_id],
                text=text,
            )
        else:
            msg = await _bot.send_message(chat_id=DB_CHANNEL_ID, text=text)
            CODES_MSG_IDS[user_id] = msg.message_id
            await save_index()
    except Exception as e:
        logger.error(f"save_codes {user_id}: {e}")


async def save_index():
    global INDEX_MSG_ID
    index = {
        "users": {str(k): v for k, v in USER_MSG_IDS.items()},
        "codes": {str(k): v for k, v in CODES_MSG_IDS.items()},
    }
    text = f"#index\n```json\n{json.dumps(index, ensure_ascii=False, indent=2)}\n```"
    try:
        if INDEX_MSG_ID:
            await _bot.edit_message_text(
                chat_id=DB_CHANNEL_ID,
                message_id=INDEX_MSG_ID,
                text=text,
            )
        else:
            msg = await _bot.send_message(chat_id=DB_CHANNEL_ID, text=text)
            INDEX_MSG_ID = msg.message_id
            with open("index_id.txt", "w") as f:
                f.write(str(INDEX_MSG_ID))
    except Exception as e:
        logger.error(f"save_index: {e}")


# ─────────────────────────────────────────────
#  ЗАГРУЗКА ИЗ КАНАЛА ПРИ СТАРТЕ
# ─────────────────────────────────────────────

async def load_all_from_channel():
    global INDEX_MSG_ID
    logger.info("📥 Загружаю данные из канала...")

    if not os.path.exists("index_id.txt"):
        logger.info("📭 Первый запуск — данных нет")
        return

    try:
        with open("index_id.txt") as f:
            INDEX_MSG_ID = int(f.read().strip())
    except Exception:
        logger.info("📭 Не удалось прочитать index_id.txt")
        return

    try:
        fwd      = await _bot.forward_message(DB_CHANNEL_ID, DB_CHANNEL_ID, INDEX_MSG_ID)
        raw      = fwd.text or fwd.caption or ""
        json_str = extract_json(raw)
        await _bot.delete_message(DB_CHANNEL_ID, fwd.message_id)
        if not json_str:
            return
        index = json.loads(json_str)
    except Exception as e:
        logger.error(f"Ошибка загрузки индекса: {e}")
        return

    loaded = 0
    for user_id_str, msg_id in index.get("users", {}).items():
        uid = int(user_id_str)
        try:
            fwd      = await _bot.forward_message(DB_CHANNEL_ID, DB_CHANNEL_ID, msg_id)
            raw      = fwd.text or fwd.caption or ""
            json_str = extract_json(raw)
            await _bot.delete_message(DB_CHANNEL_ID, fwd.message_id)
            if json_str:
                USERS[uid]        = json.loads(json_str)
                USER_MSG_IDS[uid] = msg_id
                loaded += 1
        except Exception as e:
            logger.warning(f"Пользователь {uid}: {e}")

    for user_id_str, msg_id in index.get("codes", {}).items():
        uid = int(user_id_str)
        try:
            fwd      = await _bot.forward_message(DB_CHANNEL_ID, DB_CHANNEL_ID, msg_id)
            raw      = fwd.text or fwd.caption or ""
            json_str = extract_json(raw)
            await _bot.delete_message(DB_CHANNEL_ID, fwd.message_id)
            if json_str:
                USER_CODES[uid]    = json.loads(json_str)
                CODES_MSG_IDS[uid] = msg_id
        except Exception as e:
            logger.warning(f"Коды {uid}: {e}")

    logger.info(f"✅ Загружено {loaded} пользователей")


# ─────────────────────────────────────────────
#  API БАЗЫ ДАННЫХ
# ─────────────────────────────────────────────

async def get_user(user_id: int) -> Optional[dict]:
    return USERS.get(user_id)


async def create_user(user_id: int, username: str, full_name: str):
    if user_id not in USERS:
        USERS[user_id] = default_user(user_id, username, full_name)
        await save_user_to_channel(user_id)


async def update_tiktok(user_id: int, tiktok_username: str):
    if user_id in USERS:
        USERS[user_id]["tiktok_username"] = tiktok_username
        await save_user_to_channel(user_id)


async def add_points(user_id: int, points: int):
    if user_id in USERS:
        USERS[user_id]["points"] += points
        await save_user_to_channel(user_id)


async def spend_points(user_id: int, points: int) -> bool:
    if user_id not in USERS or USERS[user_id]["points"] < points:
        return False
    USERS[user_id]["points"] -= points
    await save_user_to_channel(user_id)
    return True


async def add_screenshots(user_id: int, count: int):
    if user_id in USERS:
        USERS[user_id]["screenshots_submitted"] += count
        await save_user_to_channel(user_id)


async def save_code(user_id: int, code: str):
    if user_id not in USER_CODES:
        USER_CODES[user_id] = []
    USER_CODES[user_id].append({"code": code, "used": False})
    if user_id in USERS:
        USERS[user_id]["codes_generated"] += 1
    await save_user_to_channel(user_id)
    await save_codes_to_channel(user_id)


async def get_user_codes(user_id: int) -> list:
    return USER_CODES.get(user_id, [])


async def get_all_users() -> list:
    return sorted(USERS.values(), key=lambda u: u["points"], reverse=True)


# ─────────────────────────────────────────────
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ─────────────────────────────────────────────

def generate_code(length: int = 20) -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


def format_code(code: str) -> str:
    return "-".join(code[i: i + 4] for i in range(0, len(code), 4))


def validate_tiktok_username(username: str) -> Optional[str]:
    username = username.strip().lstrip("@")
    return username if re.match(r"^[a-zA-Z0-9_.]{2,24}$", username) else None


def points_to_emoji(p: int) -> str:
    if p >= 5000: return "👑"
    if p >= 2000: return "💎"
    if p >= 1000: return "🥇"
    if p >= 500:  return "🥈"
    if p >= 100:  return "🥉"
    return "🌱"


def get_tier_name(p: int) -> str:
    if p >= 5000: return "Легенда"
    if p >= 2000: return "Бриллиант"
    if p >= 1000: return "Золото"
    if p >= 500:  return "Серебро"
    if p >= 100:  return "Бронза"
    return "Новичок"


def get_progress_bar(p: int) -> str:
    for tier in [100, 500, 1000, 2000, 5000]:
        if p < tier:
            n = int((p / tier) * 10)
            return f"{'🟩'*n}{'⬜'*(10-n)} {p}/{tier}"
    return "🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩 MAX"


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS and ADMIN_IDS != [0]


# ─────────────────────────────────────────────
#  КЛАВИАТУРЫ
# ─────────────────────────────────────────────

def main_menu_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👤 Мой профиль",       callback_data="profile"),
            InlineKeyboardButton(text="📊 Задания",           callback_data="tasks"),
        ],
        [
            InlineKeyboardButton(text="🛒 Магазин",           callback_data="shop"),
            InlineKeyboardButton(text="📸 Скриншоты",         callback_data="screenshots"),
        ],
        [
            InlineKeyboardButton(text="🎵 Подключить TikTok", callback_data="connect_tiktok"),
        ],
        [
            InlineKeyboardButton(text="❓ Помощь",            callback_data="help"),
        ],
    ])


def back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu")]
    ])


def tasks_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📸 50 скринов  → 100 💎",  callback_data="task_50")],
        [InlineKeyboardButton(text="📸 100 скринов → 250 💎",  callback_data="task_100")],
        [InlineKeyboardButton(text="📸 200 скринов → 600 💎",  callback_data="task_200")],
        [InlineKeyboardButton(text="🔙 Назад",                 callback_data="main_menu")],
    ])


def shop_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Купить код за 500 💎",   callback_data="buy_code_points")],
        [InlineKeyboardButton(text="⭐ Купить код за 50 звёзд", callback_data="buy_code_stars")],
        [InlineKeyboardButton(text="📋 Мои коды",               callback_data="my_codes")],
        [InlineKeyboardButton(text="🔙 Назад",                  callback_data="main_menu")],
    ])


# ─────────────────────────────────────────────
#  FSM
# ─────────────────────────────────────────────

class ConnectTikTok(StatesGroup):
    waiting_for_username = State()


class ScreenshotUpload(StatesGroup):
    waiting_for_screenshots = State()
    confirming              = State()


# ─────────────────────────────────────────────
#  РОУТЕР
# ─────────────────────────────────────────────

router = Router()


async def send_main_menu(target, user: dict, name: str, edit: bool = False):
    """Универсальная функция отправки главного меню с картинкой."""
    tiktok = f"@{user['tiktok_username']}" if user["tiktok_username"] else "❌ Не подключён"
    caption = (
        f"👋 *С возвращением, {name}!*\n\n"
        f"Баланс: *{user['points']} 💎*\n"
        f"TikTok: {tiktok}\n\n"
        f"Выбери действие 👇"
    )
    if edit and hasattr(target, "message"):
        # CallbackQuery — редактируем
        try:
            await target.message.edit_media(
                media=InputMediaPhoto(
                    media=WELCOME_IMAGE_URL,
                    caption=caption,
                    parse_mode="Markdown",
                ),
                reply_markup=main_menu_keyboard(),
            )
        except Exception:
            # Если текущее сообщение не фото — удаляем и шлём новое
            await target.message.delete()
            await target.bot.send_photo(
                chat_id=target.from_user.id,
                photo=WELCOME_IMAGE_URL,
                caption=caption,
                parse_mode="Markdown",
                reply_markup=main_menu_keyboard(),
            )
    else:
        # Message — новое сообщение
        chat_id = target.chat.id if hasattr(target, "chat") else target.from_user.id
        bot     = target.bot if hasattr(target, "bot") else _bot
        await bot.send_photo(
            chat_id=chat_id,
            photo=WELCOME_IMAGE_URL,
            caption=caption,
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )


# ══════════════════════════════════════════════
#  /start
# ══════════════════════════════════════════════

@router.message(CommandStart())
async def cmd_start(message: Message):
    user = await get_user(message.from_user.id)

    if not user:
        await create_user(
            message.from_user.id,
            message.from_user.username or "",
            message.from_user.full_name,
        )
        # Новый пользователь — показываем приветствие с картинкой
        welcome_caption = (
            f"🌟 *Добро пожаловать в TikTok Rewards!*\n\n"
            f"Привет, {message.from_user.first_name}! "
            f"Ты попал туда, где активность в TikTok превращается в реальные награды!\n\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 *Как это работает:*\n\n"
            f"1️⃣ Подключи свой TikTok аккаунт\n"
            f"2️⃣ Выполняй задания — отправляй скриншоты\n"
            f"3️⃣ Копи 💎 баллы\n"
            f"4️⃣ Купи уникальный код в магазине\n"
            f"5️⃣ Отправь код оператору {OPERATOR_USERNAME} и получи приз!\n\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"💡 *Задания:*\n"
            f"📸 50 скринов  → 100 💎\n"
            f"📸 100 скринов → 250 💎\n"
            f"📸 200 скринов → 600 💎\n\n"
            f"Начнём? 👇"
        )
        await message.answer_photo(
            photo=WELCOME_IMAGE_URL,
            caption=welcome_caption,
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )
    else:
        user = await get_user(message.from_user.id)
        await send_main_menu(message, user, message.from_user.first_name)


@router.callback_query(F.data == "main_menu")
async def back_to_main(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer("Напиши /start", show_alert=True)
        return
    await send_main_menu(callback, user, callback.from_user.first_name, edit=True)
    await callback.answer()


@router.callback_query(F.data == "help")
async def help_handler(callback: CallbackQuery):
    try:
        await callback.message.edit_caption(
            caption=(
                f"❓ *Помощь*\n\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"*Как подключить TikTok?*\n"
                f"Нажми «🎵 Подключить TikTok» и отправь никнейм\n\n"
                f"*Какие скриншоты нужны?*\n"
                f"Скриншоты из TikTok: видео, лайки, просмотры, статистика профиля\n\n"
                f"*Как получить приз?*\n"
                f"1. Накопи 500+ 💎\n"
                f"2. Купи код в магазине\n"
                f"3. Отправь код оператору {OPERATOR_USERNAME}\n\n"
                f"*Можно купить без баллов?*\n"
                f"Да, за ⭐ Telegram Stars в магазине\n\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"💬 Поддержка: {OPERATOR_USERNAME}"
            ),
            parse_mode="Markdown",
            reply_markup=back_keyboard(),
        )
    except Exception:
        await callback.message.edit_text(
            f"❓ *Помощь*\n\n"
            f"*Как подключить TikTok?*\nНажми «🎵 Подключить TikTok» и отправь никнейм\n\n"
            f"*Как получить приз?*\n1. Накопи 500+ 💎\n2. Купи код в магазине\n3. Отправь оператору {OPERATOR_USERNAME}\n\n"
            f"💬 Поддержка: {OPERATOR_USERNAME}",
            parse_mode="Markdown",
            reply_markup=back_keyboard(),
        )
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
    text = (
        f"👤 *Мой профиль*\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"🏷 *Имя:* {user['full_name']}\n"
        f"🆔 *ID:* `{user['user_id']}`\n"
        f"🎵 *TikTok:* {tiktok}\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{points_to_emoji(user['points'])} *Статус:* {get_tier_name(user['points'])}\n"
        f"💎 *Баллы:* {user['points']}\n"
        f"📸 *Скриншотов:* {user['screenshots_submitted']}\n"
        f"🎁 *Кодов:* {user['codes_generated']}\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"📈 *Прогресс:*\n"
        f"{get_progress_bar(user['points'])}"
    )
    try:
        await callback.message.edit_caption(caption=text, parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception:
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
        text = "⚠️ *Сначала подключи TikTok!*\n\nНажми «🎵 Подключить TikTok» в главном меню."
        try:
            await callback.message.edit_caption(caption=text, parse_mode="Markdown", reply_markup=back_keyboard())
        except Exception:
            await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=back_keyboard())
        await callback.answer()
        return
    text = (
        f"📊 *Задания*\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"TikTok: @{user['tiktok_username']}\n"
        f"Баллы: {user['points']} 💎\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📸 *50 скриншотов*  → 100 💎\n"
        f"📸 *100 скриншотов* → 250 💎\n"
        f"📸 *200 скриншотов* → 600 💎\n\n"
        f"Выбери задание 👇"
    )
    try:
        await callback.message.edit_caption(caption=text, parse_mode="Markdown", reply_markup=tasks_keyboard())
    except Exception:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=tasks_keyboard())
    await callback.answer()


@router.callback_query(F.data.in_({"task_50", "task_100", "task_200"}))
async def task_selected(callback: CallbackQuery):
    cfg = TASK_CONFIG[callback.data]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📸 Начать загрузку", callback_data=f"start_upload_{callback.data}")],
        [InlineKeyboardButton(text="🔙 Назад",           callback_data="tasks")],
    ])
    text = (
        f"📸 *{cfg['label']}*\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎯 Награда: *{cfg['reward']} 💎*\n\n"
        f"📌 Инструкция:\n"
        f"1. Сделай {cfg['count']} скриншотов из TikTok\n"
        f"2. Отправляй боту (можно альбомами по 10)\n"
        f"3. Нажми «✅ Готово»\n\n"
        f"⚠️ Скриншоты должны быть из TikTok"
    )
    try:
        await callback.message.edit_caption(caption=text, parse_mode="Markdown", reply_markup=keyboard)
    except Exception:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)
    await callback.answer()


# ══════════════════════════════════════════════
#  СКРИНШОТЫ (FSM)
# ══════════════════════════════════════════════

@router.callback_query(F.data == "screenshots")
async def screenshots_menu(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user or not user["tiktok_username"]:
        text = "⚠️ *Сначала подключи TikTok!*\n\nГлавное меню → «🎵 Подключить TikTok»"
        try:
            await callback.message.edit_caption(caption=text, parse_mode="Markdown", reply_markup=back_keyboard())
        except Exception:
            await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=back_keyboard())
        await callback.answer()
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📸 50 скринов  (+100 💎)",  callback_data="start_upload_task_50")],
        [InlineKeyboardButton(text="📸 100 скринов (+250 💎)",  callback_data="start_upload_task_100")],
        [InlineKeyboardButton(text="📸 200 скринов (+600 💎)",  callback_data="start_upload_task_200")],
        [InlineKeyboardButton(text="🔙 Назад",                  callback_data="main_menu")],
    ])
    try:
        await callback.message.edit_caption(caption="📸 *Выбери задание:*", parse_mode="Markdown", reply_markup=keyboard)
    except Exception:
        await callback.message.edit_text("📸 *Выбери задание:*", parse_mode="Markdown", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("start_upload_"))
async def start_upload(callback: CallbackQuery, state: FSMContext):
    task_type = callback.data.replace("start_upload_", "")
    if task_type not in TASK_CONFIG:
        await callback.answer("Неверное задание", show_alert=True)
        return
    cfg = TASK_CONFIG[task_type]
    await state.set_state(ScreenshotUpload.waiting_for_screenshots)
    await state.update_data(task_type=task_type, received_count=0, photo_file_ids=[])

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Готово, проверить!", callback_data="check_screenshots")],
        [InlineKeyboardButton(text="❌ Отмена",             callback_data="cancel_upload")],
    ])
    text = (
        f"📸 *Загрузка: {cfg['label']}*\n\n"
        f"Нужно: *{cfg['count']} скриншотов*\n"
        f"Награда: *{cfg['reward']} 💎*\n\n"
        f"Отправляй скриншоты из TikTok прямо сейчас.\n"
        f"Можно по одному или альбомами (до 10 за раз).\n\n"
        f"После отправки всех фото нажми «✅ Готово»"
    )
    try:
        await callback.message.edit_caption(caption=text, parse_mode="Markdown", reply_markup=keyboard)
    except Exception:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)
    await callback.answer()


@router.message(ScreenshotUpload.waiting_for_screenshots, F.photo)
async def receive_screenshot(message: Message, state: FSMContext):
    data      = await state.get_data()
    new_count = data.get("received_count", 0) + 1
    photo_ids = data.get("photo_file_ids", [])

    # Сохраняем file_id первых 10 фото для отчёта в канал
    if len(photo_ids) < 10:
        photo_ids.append(message.photo[-1].file_id)

    await state.update_data(received_count=new_count, photo_file_ids=photo_ids)

    cfg    = TASK_CONFIG[data.get("task_type", "task_50")]
    needed = cfg["count"]

    if new_count % 10 == 0 or new_count >= needed:
        status   = "✅ Достаточно! Нажми «Готово»" if new_count >= needed else f"Ещё {needed - new_count}"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Готово, проверить!", callback_data="check_screenshots")],
            [InlineKeyboardButton(text="❌ Отмена",             callback_data="cancel_upload")],
        ])
        await message.answer(
            f"📊 Получено: *{new_count}/{needed}*\n{status}",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )


@router.callback_query(F.data == "check_screenshots", ScreenshotUpload.waiting_for_screenshots)
async def check_screenshots_cb(callback: CallbackQuery, state: FSMContext):
    data   = await state.get_data()
    count  = data.get("received_count", 0)
    cfg    = TASK_CONFIG[data.get("task_type", "task_50")]
    needed = cfg["count"]

    if count < needed:
        await callback.answer(
            f"⚠️ Мало скриншотов!\nПолучено: {count}, нужно: {needed}",
            show_alert=True,
        )
        return

    await state.set_state(ScreenshotUpload.confirming)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить и получить баллы", callback_data="confirm_screenshots")],
        [InlineKeyboardButton(text="❌ Отмена",                       callback_data="cancel_upload")],
    ])
    await callback.message.answer(
        f"🎉 *Отлично!*\n\n"
        f"Скриншотов: *{count}* ✅\n"
        f"Награда: *{cfg['reward']} 💎*\n\n"
        f"Нажми «Подтвердить» 👇",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data == "confirm_screenshots", ScreenshotUpload.confirming)
async def confirm_screenshots_cb(callback: CallbackQuery, state: FSMContext):
    data      = await state.get_data()
    cfg       = TASK_CONFIG[data.get("task_type", "task_50")]
    count     = data.get("received_count", 0)
    photo_ids = data.get("photo_file_ids", [])
    user      = await get_user(callback.from_user.id)

    await add_screenshots(callback.from_user.id, count)
    await add_points(callback.from_user.id, cfg["reward"])
    await state.clear()

    new_balance = (user["points"] if user else 0) + cfg["reward"]

    # Отправляем красивый отчёт в канал со скриншотами
    await send_task_report(callback.from_user.id, data.get("task_type", "task_50"), photo_ids)

    await callback.message.answer(
        f"🎊 *Баллы начислены!*\n\n"
        f"✅ {cfg['label']}\n"
        f"💎 +{cfg['reward']} баллов\n"
        f"💰 Баланс: {new_balance} 💎\n\n"
        f"Продолжай зарабатывать!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Главное меню", callback_data="go_main_after_task")]
        ]),
    )
    await callback.answer("✅ Баллы начислены!", show_alert=True)


@router.callback_query(F.data == "go_main_after_task")
async def go_main_after_task(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer()
        return
    await send_main_menu(callback, user, callback.from_user.first_name, edit=False)
    await callback.message.delete()
    await callback.answer()


@router.callback_query(F.data == "cancel_upload")
async def cancel_upload(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("❌ Отменено.", reply_markup=main_menu_keyboard())
    await callback.answer()


# ══════════════════════════════════════════════
#  TIKTOK (FSM)
# ══════════════════════════════════════════════

@router.callback_query(F.data == "connect_tiktok")
async def connect_tiktok_cb(callback: CallbackQuery, state: FSMContext):
    user    = await get_user(callback.from_user.id)
    current = f"\n\n✅ Текущий: @{user['tiktok_username']}" if user and user["tiktok_username"] else ""
    await state.set_state(ConnectTikTok.waiting_for_username)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_tiktok")]
    ])
    text = (
        f"🎵 *Подключение TikTok*{current}\n\n"
        f"Отправь свой TikTok никнейм (без @)\n\n"
        f"Пример: `myawesomeprofile`"
    )
    try:
        await callback.message.edit_caption(caption=text, parse_mode="Markdown", reply_markup=keyboard)
    except Exception:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)
    await callback.answer()


@router.message(ConnectTikTok.waiting_for_username)
async def receive_tiktok_username(message: Message, state: FSMContext):
    username = validate_tiktok_username(message.text or "")
    if not username:
        await message.answer(
            "⚠️ *Неверный формат!*\n\n"
            "Только буквы, цифры, точки и подчёркивания. Длина: 2–24 символа.\n\n"
            "Попробуй ещё раз:",
            parse_mode="Markdown",
        )
        return
    await update_tiktok(message.from_user.id, username)
    await state.clear()
    user = await get_user(message.from_user.id)
    await message.answer(
        f"✅ *TikTok подключён!*\n\n🎵 @{username}\n\nТеперь выполняй задания!",
        parse_mode="Markdown",
    )
    await send_main_menu(message, user, message.from_user.first_name)


@router.callback_query(F.data == "cancel_tiktok")
async def cancel_tiktok(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer()
        return
    await send_main_menu(callback, user, callback.from_user.first_name, edit=True)
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
    text = (
        f"🛒 *Магазин*\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"💎 Твои баллы: *{user['points']}*\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🎁 *Уникальный код* — 20 символов.\n"
        f"Отправь оператору {OPERATOR_USERNAME} → получи награду!\n\n"
        f"💎 За баллы: *{CODE_PRICE_POINTS} 💎*\n"
        f"⭐ За звёзды: *{CODE_PRICE_STARS} Stars*\n"
        f"━━━━━━━━━━━━━━━━━━━"
    )
    try:
        await callback.message.edit_caption(caption=text, parse_mode="Markdown", reply_markup=shop_keyboard())
    except Exception:
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
            f"⚠️ Нужно: {CODE_PRICE_POINTS} 💎\nУ тебя: {user['points']} 💎",
            show_alert=True,
        )
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Потратить {CODE_PRICE_POINTS} 💎", callback_data="confirm_buy_points")],
        [InlineKeyboardButton(text="❌ Отмена",                            callback_data="shop")],
    ])
    text = (
        f"🎁 *Подтверди покупку*\n\n"
        f"Стоимость: *{CODE_PRICE_POINTS} 💎*\n"
        f"Баланс: *{user['points']} 💎*\n"
        f"После покупки: *{user['points'] - CODE_PRICE_POINTS} 💎*"
    )
    try:
        await callback.message.edit_caption(caption=text, parse_mode="Markdown", reply_markup=keyboard)
    except Exception:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "confirm_buy_points")
async def confirm_buy_points_cb(callback: CallbackQuery):
    success = await spend_points(callback.from_user.id, CODE_PRICE_POINTS)
    if not success:
        await callback.answer("⚠️ Недостаточно баллов!", show_alert=True)
        return
    code      = generate_code()
    formatted = format_code(code)
    await save_code(callback.from_user.id, code)
    text = (
        f"🎉 *Код получен!*\n\n"
        f"🔑 *Твой код:*\n`{formatted}`\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"1. Скопируй код выше\n"
        f"2. Напиши: {OPERATOR_USERNAME}\n"
        f"3. Отправь код → получи награду 🎁\n\n"
        f"⚠️ Код одноразовый!"
    )
    try:
        await callback.message.edit_caption(caption=text, parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=back_keyboard())
    await callback.answer("✅ Код выдан!", show_alert=True)


@router.callback_query(F.data == "buy_code_stars")
async def buy_code_stars_cb(callback: CallbackQuery, bot: Bot):
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title="🎁 Уникальный код",
        description=f"Код из 20 символов для обмена у оператора {OPERATOR_USERNAME}",
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
        await message.answer(
            f"🎉 *Оплата прошла!*\n\n"
            f"🔑 *Твой код:*\n`{formatted}`\n\n"
            f"Напиши {OPERATOR_USERNAME} и отправь код 🎁\n\n"
            f"⚠️ Код одноразовый!",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )


@router.callback_query(F.data == "my_codes")
async def my_codes_handler(callback: CallbackQuery):
    codes = await get_user_codes(callback.from_user.id)
    if not codes:
        text = "📋 *Мои коды*\n\nПока нет кодов. Купи в магазине!"
        try:
            await callback.message.edit_caption(caption=text, parse_mode="Markdown", reply_markup=back_keyboard())
        except Exception:
            await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=back_keyboard())
        await callback.answer()
        return
    text = "📋 *Мои коды*\n━━━━━━━━━━━━━━━━━━━\n\n"
    for i, c in enumerate(codes[-10:], 1):
        status = "✅ Активен" if not c["used"] else "❌ Использован"
        text += f"*{i}.* `{format_code(c['code'])}`\n{status}\n\n"
    text += f"Отправь оператору: {OPERATOR_USERNAME}"
    try:
        await callback.message.edit_caption(caption=text, parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=back_keyboard())
    await callback.answer()


# ══════════════════════════════════════════════
#  АДМИН
# ══════════════════════════════════════════════

@router.message(Command("admin"))
async def admin_panel(message: Message):
    if not is_admin(message.from_user.id):
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")],
        [InlineKeyboardButton(text="📊 Статистика",   callback_data="admin_stats")],
    ])
    await message.answer("🔧 *Панель администратора*", parse_mode="Markdown", reply_markup=keyboard)


@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    users = await get_all_users()
    await callback.message.edit_text(
        f"📊 *Статистика*\n━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 Пользователей: *{len(users)}*\n"
        f"🎵 С TikTok: *{sum(1 for u in users if u['tiktok_username'])}*\n"
        f"💎 Баллов выдано: *{sum(u['points'] for u in users)}*\n"
        f"📸 Скриншотов: *{sum(u['screenshots_submitted'] for u in users)}*\n"
        f"🎁 Кодов: *{sum(u['codes_generated'] for u in users)}*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
        ]),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_users")
async def admin_users_cb(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    users = await get_all_users()
    text  = "👥 *Топ пользователей*\n━━━━━━━━━━━━━━━━━━━\n\n"
    for i, u in enumerate(users[:20], 1):
        tiktok = f"@{u['tiktok_username']}" if u["tiktok_username"] else "—"
        text += f"*{i}.* {points_to_emoji(u['points'])} {u['full_name']} — {u['points']} 💎\n   {tiktok}\n\n"
    await callback.message.edit_text(
        text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
        ]),
    )
    await callback.answer()


@router.message(Command("addpoints"))
async def add_points_cmd(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Использование: `/addpoints <user_id> <amount>`", parse_mode="Markdown")
        return
    try:
        target_id, amount = int(parts[1]), int(parts[2])
    except ValueError:
        await message.answer("⚠️ ID и сумма должны быть числами.")
        return
    if not await get_user(target_id):
        await message.answer("⚠️ Пользователь не найден.")
        return
    await add_points(target_id, amount)
    await message.answer(f"✅ Пользователю `{target_id}` начислено *{amount} 💎*", parse_mode="Markdown")


# ─────────────────────────────────────────────
#  ЗАПУСК
# ─────────────────────────────────────────────

async def main():
    global _bot
    _bot = Bot(token=BOT_TOKEN)
    await load_all_from_channel()
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    logger.info("🤖 Bot started!")
    await dp.start_polling(_bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())


import aiosqlite# ============================================================
#  TikTok Rewards Bot — хранение данных в Telegram-канале
#  Запуск: python bot.py
# ============================================================

import asyncio
import json
import logging
import os
import random
import re
import string
from typing import Optional

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

BOT_TOKEN         = os.getenv("BOT_TOKEN", "8206313463:AAFxbANmtioF9T0zo1glaUwUrcehayVGoIE")
OPERATOR_USERNAME = os.getenv("OPERATOR_USERNAME", "@OldSIWs")
ADMIN_IDS         = list(map(int, os.getenv("ADMIN_IDS", "0").split(",")))
DB_CHANNEL_ID     = int(os.getenv("DB_CHANNEL_ID", "-1003513114819"))

CODE_PRICE_POINTS = 500
CODE_PRICE_STARS  = 50

TASK_CONFIG = {
    "task_50":  {"count": 50,  "reward": 100, "label": "50 скриншотов"},
    "task_100": {"count": 100, "reward": 250, "label": "100 скриншотов"},
    "task_200": {"count": 200, "reward": 600, "label": "200 скриншотов"},
}

# ─────────────────────────────────────────────
#  ХРАНИЛИЩЕ В ПАМЯТИ (синкается с каналом)
# ─────────────────────────────────────────────

USERS: dict[int, dict]       = {}   # user_id -> данные
USER_MSG_IDS: dict[int, int] = {}   # user_id -> message_id в канале
USER_CODES: dict[int, list]  = {}   # user_id -> список кодов
CODES_MSG_IDS: dict[int, int]= {}   # user_id -> message_id кодов в канале
INDEX_MSG_ID: Optional[int]  = None
_bot: Optional[Bot]          = None


def default_user(user_id: int, username: str, full_name: str) -> dict:
    return {
        "user_id": user_id,
        "username": username,
        "full_name": full_name,
        "points": 0,
        "tiktok_username": None,
        "screenshots_submitted": 0,
        "codes_generated": 0,
    }


def extract_json(text: str) -> Optional[str]:
    match = re.search(r"```json\s*([\s\S]+?)\s*```", text)
    return match.group(1) if match else None


# ─────────────────────────────────────────────
#  СОХРАНЕНИЕ В КАНАЛ
# ─────────────────────────────────────────────

async def save_user_to_channel(user_id: int):
    data = USERS.get(user_id)
    if not data:
        return
    text = f"#user_{user_id}\n```json\n{json.dumps(data, ensure_ascii=False, indent=2)}\n```"
    try:
        if user_id in USER_MSG_IDS:
            await _bot.edit_message_text(
                chat_id=DB_CHANNEL_ID,
                message_id=USER_MSG_IDS[user_id],
                text=text,
            )
        else:
            msg = await _bot.send_message(chat_id=DB_CHANNEL_ID, text=text)
            USER_MSG_IDS[user_id] = msg.message_id
            await save_index()
    except Exception as e:
        logger.error(f"save_user {user_id}: {e}")


async def save_codes_to_channel(user_id: int):
    codes = USER_CODES.get(user_id, [])
    text  = f"#codes_{user_id}\n```json\n{json.dumps(codes, ensure_ascii=False, indent=2)}\n```"
    try:
        if user_id in CODES_MSG_IDS:
            await _bot.edit_message_text(
                chat_id=DB_CHANNEL_ID,
                message_id=CODES_MSG_IDS[user_id],
                text=text,
            )
        else:
            msg = await _bot.send_message(chat_id=DB_CHANNEL_ID, text=text)
            CODES_MSG_IDS[user_id] = msg.message_id
            await save_index()
    except Exception as e:
        logger.error(f"save_codes {user_id}: {e}")


async def save_index():
    global INDEX_MSG_ID
    index = {
        "users": {str(k): v for k, v in USER_MSG_IDS.items()},
        "codes": {str(k): v for k, v in CODES_MSG_IDS.items()},
    }
    text = f"#index\n```json\n{json.dumps(index, ensure_ascii=False, indent=2)}\n```"
    try:
        if INDEX_MSG_ID:
            await _bot.edit_message_text(
                chat_id=DB_CHANNEL_ID,
                message_id=INDEX_MSG_ID,
                text=text,
            )
        else:
            msg = await _bot.send_message(chat_id=DB_CHANNEL_ID, text=text)
            INDEX_MSG_ID = msg.message_id
            # Сохраняем ID индекса локально (маленький файл, не БД)
            with open("index_id.txt", "w") as f:
                f.write(str(INDEX_MSG_ID))
    except Exception as e:
        logger.error(f"save_index: {e}")


# ─────────────────────────────────────────────
#  ЗАГРУЗКА ИЗ КАНАЛА ПРИ СТАРТЕ
# ─────────────────────────────────────────────

async def load_all_from_channel():
    global INDEX_MSG_ID
    logger.info("📥 Загружаю данные из канала...")

    # Читаем ID индекса из файла
    if not os.path.exists("index_id.txt"):
        logger.info("📭 Первый запуск — данных нет")
        return

    try:
        with open("index_id.txt") as f:
            INDEX_MSG_ID = int(f.read().strip())
    except Exception:
        logger.info("📭 Не удалось прочитать index_id.txt")
        return

    # Получаем индекс через forwardMessage
    try:
        fwd = await _bot.forward_message(
            chat_id=DB_CHANNEL_ID,
            from_chat_id=DB_CHANNEL_ID,
            message_id=INDEX_MSG_ID,
        )
        raw      = fwd.text or fwd.caption or ""
        json_str = extract_json(raw)
        await _bot.delete_message(DB_CHANNEL_ID, fwd.message_id)

        if not json_str:
            logger.warning("Индекс пустой")
            return

        index = json.loads(json_str)
    except Exception as e:
        logger.error(f"Ошибка загрузки индекса: {e}")
        return

    loaded = 0

    # Загружаем пользователей
    for user_id_str, msg_id in index.get("users", {}).items():
        uid = int(user_id_str)
        try:
            fwd      = await _bot.forward_message(DB_CHANNEL_ID, DB_CHANNEL_ID, msg_id)
            raw      = fwd.text or fwd.caption or ""
            json_str = extract_json(raw)
            await _bot.delete_message(DB_CHANNEL_ID, fwd.message_id)
            if json_str:
                USERS[uid]       = json.loads(json_str)
                USER_MSG_IDS[uid] = msg_id
                loaded += 1
        except Exception as e:
            logger.warning(f"Пользователь {uid}: {e}")

    # Загружаем коды
    for user_id_str, msg_id in index.get("codes", {}).items():
        uid = int(user_id_str)
        try:
            fwd      = await _bot.forward_message(DB_CHANNEL_ID, DB_CHANNEL_ID, msg_id)
            raw      = fwd.text or fwd.caption or ""
            json_str = extract_json(raw)
            await _bot.delete_message(DB_CHANNEL_ID, fwd.message_id)
            if json_str:
                USER_CODES[uid]    = json.loads(json_str)
                CODES_MSG_IDS[uid] = msg_id
        except Exception as e:
            logger.warning(f"Коды {uid}: {e}")

    logger.info(f"✅ Загружено {loaded} пользователей")


# ─────────────────────────────────────────────
#  API БАЗЫ ДАННЫХ
# ─────────────────────────────────────────────

async def get_user(user_id: int) -> Optional[dict]:
    return USERS.get(user_id)


async def create_user(user_id: int, username: str, full_name: str):
    if user_id not in USERS:
        USERS[user_id] = default_user(user_id, username, full_name)
        await save_user_to_channel(user_id)


async def update_tiktok(user_id: int, tiktok_username: str):
    if user_id in USERS:
        USERS[user_id]["tiktok_username"] = tiktok_username
        await save_user_to_channel(user_id)


async def add_points(user_id: int, points: int):
    if user_id in USERS:
        USERS[user_id]["points"] += points
        await save_user_to_channel(user_id)


async def spend_points(user_id: int, points: int) -> bool:
    if user_id not in USERS or USERS[user_id]["points"] < points:
        return False
    USERS[user_id]["points"] -= points
    await save_user_to_channel(user_id)
    return True


async def add_screenshots(user_id: int, count: int):
    if user_id in USERS:
        USERS[user_id]["screenshots_submitted"] += count
        await save_user_to_channel(user_id)


async def save_code(user_id: int, code: str):
    if user_id not in USER_CODES:
        USER_CODES[user_id] = []
    USER_CODES[user_id].append({"code": code, "used": False})
    if user_id in USERS:
        USERS[user_id]["codes_generated"] += 1
    await save_user_to_channel(user_id)
    await save_codes_to_channel(user_id)


async def get_user_codes(user_id: int) -> list:
    return USER_CODES.get(user_id, [])


async def get_all_users() -> list:
    return sorted(USERS.values(), key=lambda u: u["points"], reverse=True)


# ─────────────────────────────────────────────
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ─────────────────────────────────────────────

def generate_code(length: int = 20) -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


def format_code(code: str) -> str:
    return "-".join(code[i: i + 4] for i in range(0, len(code), 4))


def validate_tiktok_username(username: str) -> Optional[str]:
    username = username.strip().lstrip("@")
    return username if re.match(r"^[a-zA-Z0-9_.]{2,24}$", username) else None


def points_to_emoji(p: int) -> str:
    if p >= 5000: return "👑"
    if p >= 2000: return "💎"
    if p >= 1000: return "🥇"
    if p >= 500:  return "🥈"
    if p >= 100:  return "🥉"
    return "🌱"


def get_tier_name(p: int) -> str:
    if p >= 5000: return "Легенда"
    if p >= 2000: return "Бриллиант"
    if p >= 1000: return "Золото"
    if p >= 500:  return "Серебро"
    if p >= 100:  return "Бронза"
    return "Новичок"


def get_progress_bar(p: int) -> str:
    for tier in [100, 500, 1000, 2000, 5000]:
        if p < tier:
            n = int((p / tier) * 10)
            return f"{'🟩'*n}{'⬜'*(10-n)} {p}/{tier}"
    return "🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩 MAX"


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS and ADMIN_IDS != [0]


# ─────────────────────────────────────────────
#  КЛАВИАТУРЫ
# ─────────────────────────────────────────────

def main_menu_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👤 Мой профиль",       callback_data="profile"),
            InlineKeyboardButton(text="📊 Задания",           callback_data="tasks"),
        ],
        [
            InlineKeyboardButton(text="🛒 Магазин",           callback_data="shop"),
            InlineKeyboardButton(text="📸 Скриншоты",         callback_data="screenshots"),
        ],
        [
            InlineKeyboardButton(text="🎵 Подключить TikTok", callback_data="connect_tiktok"),
        ],
        [
            InlineKeyboardButton(text="❓ Помощь",            callback_data="help"),
        ],
    ])


def back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu")]
    ])


def tasks_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📸 50 скринов  → 100 💎",  callback_data="task_50")],
        [InlineKeyboardButton(text="📸 100 скринов → 250 💎",  callback_data="task_100")],
        [InlineKeyboardButton(text="📸 200 скринов → 600 💎",  callback_data="task_200")],
        [InlineKeyboardButton(text="🔙 Назад",                 callback_data="main_menu")],
    ])


def shop_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Купить код за 500 💎",   callback_data="buy_code_points")],
        [InlineKeyboardButton(text="⭐ Купить код за 50 звёзд", callback_data="buy_code_stars")],
        [InlineKeyboardButton(text="📋 Мои коды",               callback_data="my_codes")],
        [InlineKeyboardButton(text="🔙 Назад",                  callback_data="main_menu")],
    ])


# ─────────────────────────────────────────────
#  FSM
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
#  /start
# ══════════════════════════════════════════════

@router.message(CommandStart())
async def cmd_start(message: Message):
    user = await get_user(message.from_user.id)
    if not user:
        await create_user(
            message.from_user.id,
            message.from_user.username or "",
            message.from_user.full_name,
        )
        text = (
            f"🌟 *Добро пожаловать в TikTok Rewards!*\n\n"
            f"Привет, {message.from_user.first_name}! Ты попал в место, где активность в TikTok превращается в реальные награды!\n\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 *Как это работает:*\n\n"
            f"1️⃣ Подключи TikTok аккаунт\n"
            f"2️⃣ Выполняй задания — отправляй скриншоты\n"
            f"3️⃣ Копи 💎 баллы\n"
            f"4️⃣ Купи уникальный код и отправь оператору\n\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"💡 *Задания:*\n"
            f"📸 50 скринов  → 100 💎\n"
            f"📸 100 скринов → 250 💎\n"
            f"📸 200 скринов → 600 💎\n\n"
            f"Нажми кнопку ниже 👇"
        )
        await message.answer(text, parse_mode="Markdown", reply_markup=main_menu_keyboard())
    else:
        tiktok = f"@{user['tiktok_username']}" if user["tiktok_username"] else "❌ Не подключён"
        await message.answer(
            f"👋 *С возвращением, {message.from_user.first_name}!*\n\n"
            f"Баланс: *{user['points']} 💎*\n"
            f"TikTok: {tiktok}\n\n"
            f"Выбери действие 👇",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )


@router.callback_query(F.data == "main_menu")
async def back_to_main(callback: CallbackQuery):
    user   = await get_user(callback.from_user.id)
    tiktok = f"@{user['tiktok_username']}" if user and user["tiktok_username"] else "❌ Не подключён"
    points = user["points"] if user else 0
    await callback.message.edit_text(
        f"👋 *С возвращением, {callback.from_user.first_name}!*\n\n"
        f"Баланс: *{points} 💎*\n"
        f"TikTok: {tiktok}\n\n"
        f"Выбери действие 👇",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "help")
async def help_handler(callback: CallbackQuery):
    await callback.message.edit_text(
        f"❓ *Помощь*\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"*Как подключить TikTok?*\n"
        f"Нажми «🎵 Подключить TikTok» и отправь никнейм\n\n"
        f"*Какие скриншоты нужны?*\n"
        f"Скриншоты из TikTok: видео, лайки, просмотры, статистика\n\n"
        f"*Как получить приз?*\n"
        f"1. Накопи 500+ 💎\n"
        f"2. Купи код в магазине\n"
        f"3. Отправь код оператору {OPERATOR_USERNAME}\n\n"
        f"*Можно купить без баллов?*\n"
        f"Да, за ⭐ Telegram Stars\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💬 Поддержка: {OPERATOR_USERNAME}",
        parse_mode="Markdown",
        reply_markup=back_keyboard(),
    )
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
    await callback.message.edit_text(
        f"👤 *Мой профиль*\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"🏷 *Имя:* {user['full_name']}\n"
        f"🆔 *ID:* `{user['user_id']}`\n"
        f"🎵 *TikTok:* {tiktok}\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{points_to_emoji(user['points'])} *Статус:* {get_tier_name(user['points'])}\n"
        f"💎 *Баллы:* {user['points']}\n"
        f"📸 *Скриншотов:* {user['screenshots_submitted']}\n"
        f"🎁 *Кодов:* {user['codes_generated']}\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"📈 *Прогресс:*\n"
        f"{get_progress_bar(user['points'])}",
        parse_mode="Markdown",
        reply_markup=back_keyboard(),
    )
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
            "⚠️ *Сначала подключи TikTok!*\n\nНажми «🎵 Подключить TikTok» в главном меню.",
            parse_mode="Markdown",
            reply_markup=back_keyboard(),
        )
        await callback.answer()
        return
    await callback.message.edit_text(
        f"📊 *Задания*\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"TikTok: @{user['tiktok_username']}\n"
        f"Баллы: {user['points']} 💎\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📸 *50 скриншотов*  → 100 💎\n"
        f"📸 *100 скриншотов* → 250 💎\n"
        f"📸 *200 скриншотов* → 600 💎\n\n"
        f"Выбери задание 👇",
        parse_mode="Markdown",
        reply_markup=tasks_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.in_({"task_50", "task_100", "task_200"}))
async def task_selected(callback: CallbackQuery):
    cfg = TASK_CONFIG[callback.data]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📸 Начать загрузку", callback_data=f"start_upload_{callback.data}")],
        [InlineKeyboardButton(text="🔙 Назад",           callback_data="tasks")],
    ])
    await callback.message.edit_text(
        f"📸 *{cfg['label']}*\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎯 Награда: *{cfg['reward']} 💎*\n\n"
        f"📌 Инструкция:\n"
        f"1. Сделай {cfg['count']} скриншотов из TikTok\n"
        f"2. Отправляй боту (можно альбомами по 10)\n"
        f"3. Нажми «✅ Готово»\n\n"
        f"⚠️ Скриншоты должны быть из TikTok",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    await callback.answer()


# ══════════════════════════════════════════════
#  СКРИНШОТЫ (FSM)
# ══════════════════════════════════════════════

@router.callback_query(F.data == "screenshots")
async def screenshots_menu(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user or not user["tiktok_username"]:
        await callback.message.edit_text(
            "⚠️ *Сначала подключи TikTok!*\n\nГлавное меню → «🎵 Подключить TikTok»",
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
    await callback.message.edit_text(
        "📸 *Выбери задание:*",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
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
    await callback.message.edit_text(
        f"📸 *Загрузка: {cfg['label']}*\n\n"
        f"Нужно: *{cfg['count']} скриншотов*\n"
        f"Награда: *{cfg['reward']} 💎*\n\n"
        f"Отправляй скриншоты из TikTok прямо сейчас.\n"
        f"Можно по одному или альбомами (до 10 за раз).\n\n"
        f"После отправки всех фото нажми «✅ Готово»",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.message(ScreenshotUpload.waiting_for_screenshots, F.photo)
async def receive_screenshot(message: Message, state: FSMContext):
    data      = await state.get_data()
    new_count = data.get("received_count", 0) + 1
    await state.update_data(received_count=new_count)
    cfg    = TASK_CONFIG[data.get("task_type", "task_50")]
    needed = cfg["count"]
    if new_count % 10 == 0 or new_count >= needed:
        status   = "✅ Достаточно! Нажми «Готово»" if new_count >= needed else f"Ещё {needed - new_count}"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Готово, проверить!", callback_data="check_screenshots")],
            [InlineKeyboardButton(text="❌ Отмена",             callback_data="cancel_upload")],
        ])
        await message.answer(
            f"📊 Получено: *{new_count}/{needed}*\n{status}",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )


@router.callback_query(F.data == "check_screenshots", ScreenshotUpload.waiting_for_screenshots)
async def check_screenshots_cb(callback: CallbackQuery, state: FSMContext):
    data   = await state.get_data()
    count  = data.get("received_count", 0)
    cfg    = TASK_CONFIG[data.get("task_type", "task_50")]
    needed = cfg["count"]
    if count < needed:
        await callback.answer(
            f"⚠️ Мало скриншотов!\nПолучено: {count}, нужно: {needed}",
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
        f"Скриншотов: *{count}* ✅\n"
        f"Награда: *{cfg['reward']} 💎*\n\n"
        f"Нажми «Подтвердить» 👇",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data == "confirm_screenshots", ScreenshotUpload.confirming)
async def confirm_screenshots_cb(callback: CallbackQuery, state: FSMContext):
    data  = await state.get_data()
    cfg   = TASK_CONFIG[data.get("task_type", "task_50")]
    count = data.get("received_count", 0)
    user  = await get_user(callback.from_user.id)
    await add_screenshots(callback.from_user.id, count)
    await add_points(callback.from_user.id, cfg["reward"])
    await state.clear()
    new_balance = (user["points"] if user else 0) + cfg["reward"]
    await callback.message.edit_text(
        f"🎊 *Баллы начислены!*\n\n"
        f"✅ {cfg['label']}\n"
        f"💎 +{cfg['reward']} баллов\n"
        f"💰 Баланс: {new_balance} 💎",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer("✅ Баллы начислены!", show_alert=True)


@router.callback_query(F.data == "cancel_upload")
async def cancel_upload(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "❌ Отменено.",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


# ══════════════════════════════════════════════
#  TIKTOK (FSM)
# ══════════════════════════════════════════════

@router.callback_query(F.data == "connect_tiktok")
async def connect_tiktok_cb(callback: CallbackQuery, state: FSMContext):
    user    = await get_user(callback.from_user.id)
    current = f"\n\n✅ Текущий: @{user['tiktok_username']}" if user and user["tiktok_username"] else ""
    await state.set_state(ConnectTikTok.waiting_for_username)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_tiktok")]
    ])
    await callback.message.edit_text(
        f"🎵 *Подключение TikTok*{current}\n\n"
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
            "⚠️ *Неверный формат!*\n\n"
            "Только буквы, цифры, точки и подчёркивания. Длина: 2–24 символа.\n\n"
            "Попробуй ещё раз:",
            parse_mode="Markdown",
        )
        return
    await update_tiktok(message.from_user.id, username)
    await state.clear()
    await message.answer(
        f"✅ *TikTok подключён!*\n\n🎵 @{username}\n\nТеперь выполняй задания!",
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
    await callback.message.edit_text(
        f"🛒 *Магазин*\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"💎 Твои баллы: *{user['points']}*\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🎁 *Уникальный код* — 20 символов.\n"
        f"Отправь оператору {OPERATOR_USERNAME} → получи награду!\n\n"
        f"💎 За баллы: *{CODE_PRICE_POINTS} 💎*\n"
        f"⭐ За звёзды: *{CODE_PRICE_STARS} Stars*\n"
        f"━━━━━━━━━━━━━━━━━━━",
        parse_mode="Markdown",
        reply_markup=shop_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "buy_code_points")
async def buy_code_points_cb(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer("Ошибка", show_alert=True)
        return
    if user["points"] < CODE_PRICE_POINTS:
        await callback.answer(
            f"⚠️ Нужно: {CODE_PRICE_POINTS} 💎\nУ тебя: {user['points']} 💎",
            show_alert=True,
        )
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Потратить {CODE_PRICE_POINTS} 💎", callback_data="confirm_buy_points")],
        [InlineKeyboardButton(text="❌ Отмена",                            callback_data="shop")],
    ])
    await callback.message.edit_text(
        f"🎁 *Подтверди покупку*\n\n"
        f"Стоимость: *{CODE_PRICE_POINTS} 💎*\n"
        f"Баланс сейчас: *{user['points']} 💎*\n"
        f"После покупки: *{user['points'] - CODE_PRICE_POINTS} 💎*",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data == "confirm_buy_points")
async def confirm_buy_points_cb(callback: CallbackQuery):
    success = await spend_points(callback.from_user.id, CODE_PRICE_POINTS)
    if not success:
        await callback.answer("⚠️ Недостаточно баллов!", show_alert=True)
        return
    code      = generate_code()
    formatted = format_code(code)
    await save_code(callback.from_user.id, code)
    await callback.message.edit_text(
        f"🎉 *Код получен!*\n\n"
        f"🔑 *Твой код:*\n`{formatted}`\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"1. Скопируй код\n"
        f"2. Напиши: {OPERATOR_USERNAME}\n"
        f"3. Отправь код → получи награду 🎁\n\n"
        f"⚠️ Код одноразовый!",
        parse_mode="Markdown",
        reply_markup=back_keyboard(),
    )
    await callback.answer("✅ Код выдан!", show_alert=True)


@router.callback_query(F.data == "buy_code_stars")
async def buy_code_stars_cb(callback: CallbackQuery, bot: Bot):
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title="🎁 Уникальный код",
        description=f"Код из 20 символов для обмена у оператора {OPERATOR_USERNAME}",
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
        await message.answer(
            f"🎉 *Оплата прошла!*\n\n"
            f"🔑 *Твой код:*\n`{formatted}`\n\n"
            f"Напиши {OPERATOR_USERNAME} и отправь код для получения награды 🎁\n\n"
            f"⚠️ Код одноразовый!",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )


@router.callback_query(F.data == "my_codes")
async def my_codes_handler(callback: CallbackQuery):
    codes = await get_user_codes(callback.from_user.id)
    if not codes:
        await callback.message.edit_text(
            "📋 *Мои коды*\n\nПока нет кодов. Купи в магазине!",
            parse_mode="Markdown",
            reply_markup=back_keyboard(),
        )
        await callback.answer()
        return
    text = "📋 *Мои коды*\n━━━━━━━━━━━━━━━━━━━\n\n"
    for i, c in enumerate(codes[-10:], 1):
        status = "✅ Активен" if not c["used"] else "❌ Использован"
        text += f"*{i}.* `{format_code(c['code'])}`\n{status}\n\n"
    text += f"Отправь оператору: {OPERATOR_USERNAME}"
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=back_keyboard())
    await callback.answer()


# ══════════════════════════════════════════════
#  АДМИН
# ══════════════════════════════════════════════

@router.message(Command("admin"))
async def admin_panel(message: Message):
    if not is_admin(message.from_user.id):
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")],
        [InlineKeyboardButton(text="📊 Статистика",   callback_data="admin_stats")],
    ])
    await message.answer("🔧 *Панель администратора*", parse_mode="Markdown", reply_markup=keyboard)


@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    users = await get_all_users()
    await callback.message.edit_text(
        f"📊 *Статистика*\n━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 Пользователей: *{len(users)}*\n"
        f"🎵 С TikTok: *{sum(1 for u in users if u['tiktok_username'])}*\n"
        f"💎 Баллов выдано: *{sum(u['points'] for u in users)}*\n"
        f"📸 Скриншотов: *{sum(u['screenshots_submitted'] for u in users)}*\n"
        f"🎁 Кодов: *{sum(u['codes_generated'] for u in users)}*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
        ]),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_users")
async def admin_users_cb(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    users = await get_all_users()
    text  = "👥 *Топ пользователей*\n━━━━━━━━━━━━━━━━━━━\n\n"
    for i, u in enumerate(users[:20], 1):
        tiktok = f"@{u['tiktok_username']}" if u["tiktok_username"] else "—"
        text += f"*{i}.* {points_to_emoji(u['points'])} {u['full_name']} — {u['points']} 💎\n   {tiktok}\n\n"
    await callback.message.edit_text(
        text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
        ]),
    )
    await callback.answer()


@router.message(Command("addpoints"))
async def add_points_cmd(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Использование: `/addpoints <user_id> <amount>`", parse_mode="Markdown")
        return
    try:
        target_id, amount = int(parts[1]), int(parts[2])
    except ValueError:
        await message.answer("⚠️ ID и сумма должны быть числами.")
        return
    if not await get_user(target_id):
        await message.answer("⚠️ Пользователь не найден.")
        return
    await add_points(target_id, amount)
    await message.answer(f"✅ Пользователю `{target_id}` начислено *{amount} 💎*", parse_mode="Markdown")


# ─────────────────────────────────────────────
#  ЗАПУСК
# ─────────────────────────────────────────────

async def main():
    global _bot
    _bot = Bot(token=BOT_TOKEN)
    await load_all_from_channel()
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    logger.info("🤖 Bot started!")
    await dp.start_polling(_bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())

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
