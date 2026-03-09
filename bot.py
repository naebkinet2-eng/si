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

# Единственное задание
TASK_CONFIG = {
    "task_150": {"count": 150, "reward": 150, "label": "150 скриншотов"},
}

WELCOME_IMAGE_URL = "https://i.ibb.co/TMcrTM8W/2026-03-09-221324358.png"
WELCOME_FILE_ID: Optional[str] = None  # кешируется после первой отправки

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

async def send_task_report(user_id: int, task_type: str, photo_file_ids: list):
    user = USERS.get(user_id)
    if not user:
        return
    cfg        = TASK_CONFIG.get(task_type, TASK_CONFIG["task_150"])
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
        f"📊 *Всего скринов:* {user['screenshots_submitted']}"
    )
    try:
        report_photos = photo_file_ids[:10]
        if len(report_photos) == 1:
            await _bot.send_photo(chat_id=DB_CHANNEL_ID, photo=report_photos[0],
                                  caption=caption, parse_mode="Markdown")
        elif len(report_photos) > 1:
            media = [
                InputMediaPhoto(media=report_photos[0], caption=caption, parse_mode="Markdown"),
                *[InputMediaPhoto(media=fid) for fid in report_photos[1:]]
            ]
            await _bot.send_media_group(chat_id=DB_CHANNEL_ID, media=media)
        else:
            await _bot.send_message(chat_id=DB_CHANNEL_ID, text=caption, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Ошибка отправки отчёта: {e}")


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
            await _bot.edit_message_text(chat_id=DB_CHANNEL_ID,
                                         message_id=USER_MSG_IDS[user_id], text=text)
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
            await _bot.edit_message_text(chat_id=DB_CHANNEL_ID,
                                         message_id=CODES_MSG_IDS[user_id], text=text)
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
            await _bot.edit_message_text(chat_id=DB_CHANNEL_ID,
                                         message_id=INDEX_MSG_ID, text=text)
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
            InlineKeyboardButton(text="📊 Задание",           callback_data="tasks"),
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
        [InlineKeyboardButton(text="📸 150 скринов → 150 💎", callback_data="task_150")],
        [InlineKeyboardButton(text="🔙 Назад",                callback_data="main_menu")],
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


async def send_welcome_photo(chat_id: int, caption: str, keyboard, bot_obj: Bot):
    """Отправляет фото, кешируя file_id для стабильности."""
    global WELCOME_FILE_ID
    try:
        if WELCOME_FILE_ID:
            msg = await bot_obj.send_photo(chat_id=chat_id, photo=WELCOME_FILE_ID,
                                           caption=caption, parse_mode="Markdown",
                                           reply_markup=keyboard)
        else:
            msg = await bot_obj.send_photo(chat_id=chat_id, photo=WELCOME_IMAGE_URL,
                                           caption=caption, parse_mode="Markdown",
                                           reply_markup=keyboard)
            WELCOME_FILE_ID = msg.photo[-1].file_id
            logger.info(f"📸 file_id закеширован")
    except Exception as e:
        logger.error(f"Ошибка фото: {e}")
        await bot_obj.send_message(chat_id=chat_id, text=caption,
                                   parse_mode="Markdown", reply_markup=keyboard)


async def send_main_menu(target, user: dict, name: str, edit: bool = False):
    tiktok  = f"@{user['tiktok_username']}" if user["tiktok_username"] else "❌ Не подключён"
    caption = (
        f"👋 *С возвращением, {name}!*\n\n"
        f"Баланс: *{user['points']} 💎*\n"
        f"TikTok: {tiktok}\n\n"
        f"Выбери действие 👇"
    )
    if hasattr(target, "message"):
        chat_id = target.from_user.id
        bot_obj = target.bot
        msg     = target.message
    else:
        chat_id = target.chat.id
        bot_obj = target.bot if hasattr(target, "bot") else _bot
        msg     = None

    if edit and msg:
        try:
            await msg.edit_media(
                media=InputMediaPhoto(
                    media=WELCOME_FILE_ID or WELCOME_IMAGE_URL,
                    caption=caption, parse_mode="Markdown",
                ),
                reply_markup=main_menu_keyboard(),
            )
            return
        except Exception:
            pass
        try:
            await msg.delete()
        except Exception:
            pass

    await send_welcome_photo(chat_id, caption, main_menu_keyboard(), bot_obj)


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
        welcome_caption = (
            f"🌟 *Рады приветствовать вас в нашем магазине!*\n\n"
            f"Добрый день 👋 {message.from_user.first_name}! "
            f"Ты попал туда, где активность в TikTok превращается в реальные награды!\n\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"*👨‍💻Сайт: Trava.ct.ws*\n\n"
            f"1️⃣ Подключи свой TikTok аккаунт\n"
            f"2️⃣ Выполняй задание — отправляй скриншоты\n"
            f"3️⃣ Копи 💎 баллы\n"
            f"4️⃣ Купи уникальный код в магазине\n"
            f"5️⃣ Отправь код оператору {OPERATOR_USERNAME} и получи пробы нашего товара!\n\n"
            f"✍️Чтобы начать выполнять простое задание напишите город оператору, "
            f"дабы предоставить наличие проб в вашем городе! @OldSiWS\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"💡 *Задание:*\n"
            f"📸 150 скринов → 150 💎\n"
            f"Начнём? 👇"
        )
        await send_welcome_photo(message.chat.id, welcome_caption,
                                 main_menu_keyboard(), message.bot or _bot)
    else:
        await send_main_menu(message, user, message.from_user.first_name)


@router.callback_query(F.data == "main_menu")
async def back_to_main(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer("Напиши /start", show_alert=True)
        return
    try:
        await callback.message.delete()
    except Exception:
        pass
    await send_main_menu(callback, user, callback.from_user.first_name)
    await callback.answer()


@router.callback_query(F.data == "help")
async def help_handler(callback: CallbackQuery):
    text = (
        f"❓ *Инструкции*\n\n"
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
        f"💬 Поддержка: {OPERATOR_USERNAME}"
    )
    try:
        await callback.message.edit_caption(caption=text, parse_mode="Markdown",
                                            reply_markup=back_keyboard())
    except Exception:
        await callback.message.edit_text(text, parse_mode="Markdown",
                                         reply_markup=back_keyboard())
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
        f"📈 *Прогресс:*\n{get_progress_bar(user['points'])}"
    )
    try:
        await callback.message.edit_caption(caption=text, parse_mode="Markdown",
                                            reply_markup=back_keyboard())
    except Exception:
        await callback.message.edit_text(text, parse_mode="Markdown",
                                         reply_markup=back_keyboard())
    await callback.answer()


# ══════════════════════════════════════════════
#  ЗАДАНИЕ
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
            await callback.message.edit_caption(caption=text, parse_mode="Markdown",
                                                reply_markup=back_keyboard())
        except Exception:
            await callback.message.edit_text(text, parse_mode="Markdown",
                                             reply_markup=back_keyboard())
        await callback.answer()
        return
    text = (
        f"📊 *Задание*\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"TikTok: @{user['tiktok_username']}\n"
        f"Баллы: {user['points']} 💎\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📸 *150 скриншотов* → 150 💎\n\n"
        f"Нажми чтобы начать 👇"
    )
    try:
        await callback.message.edit_caption(caption=text, parse_mode="Markdown",
                                            reply_markup=tasks_keyboard())
    except Exception:
        await callback.message.edit_text(text, parse_mode="Markdown",
                                         reply_markup=tasks_keyboard())
    await callback.answer()


@router.callback_query(F.data == "task_150")
async def task_selected(callback: CallbackQuery):
    cfg = TASK_CONFIG["task_150"]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📸 Начать загрузку",
                              callback_data="start_upload_task_150")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="tasks")],
    ])
    text = (
        f"📸 *{cfg['label']}*\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎯 Награда: *{cfg['reward']} 💎*\n\n"
        f"📌 Инструкция:\n"
        f"1. Сделай 150 скриншотов из TikTok\n"
        f"2. Отправляй боту (альбомами по 10)\n"
        f"3. Нажми «✅ Готово»\n\n"
        f"⚠️ Скриншоты должны быть из TikTok"
    )
    try:
        await callback.message.edit_caption(caption=text, parse_mode="Markdown",
                                            reply_markup=keyboard)
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
            await callback.message.edit_caption(caption=text, parse_mode="Markdown",
                                                reply_markup=back_keyboard())
        except Exception:
            await callback.message.edit_text(text, parse_mode="Markdown",
                                             reply_markup=back_keyboard())
        await callback.answer()
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📸 150 скринов (+150 💎)",
                              callback_data="start_upload_task_150")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")],
    ])
    try:
        await callback.message.edit_caption(caption="📸 *Выбери задание:*",
                                            parse_mode="Markdown", reply_markup=keyboard)
    except Exception:
        await callback.message.edit_text("📸 *Выбери задание:*",
                                         parse_mode="Markdown", reply_markup=keyboard)
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
        await callback.message.edit_caption(caption=text, parse_mode="Markdown",
                                            reply_markup=keyboard)
    except Exception:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)
    await callback.answer()


@router.message(ScreenshotUpload.waiting_for_screenshots, F.photo)
async def receive_screenshot(message: Message, state: FSMContext):
    data      = await state.get_data()
    new_count = data.get("received_count", 0) + 1
    photo_ids = data.get("photo_file_ids", [])
    if len(photo_ids) < 10:
        photo_ids.append(message.photo[-1].file_id)
    await state.update_data(received_count=new_count, photo_file_ids=photo_ids)
    cfg    = TASK_CONFIG[data.get("task_type", "task_150")]
    needed = cfg["count"]
    if new_count % 10 == 0 or new_count >= needed:
        status   = "✅ Достаточно! Нажми «Готово»" if new_count >= needed else f"Ещё {needed - new_count}"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Готово, проверить!", callback_data="check_screenshots")],
            [InlineKeyboardButton(text="❌ Отмена",             callback_data="cancel_upload")],
        ])
        await message.answer(f"📊 Получено: *{new_count}/{needed}*\n{status}",
                             parse_mode="Markdown", reply_markup=keyboard)


@router.callback_query(F.data == "check_screenshots", ScreenshotUpload.waiting_for_screenshots)
async def check_screenshots_cb(callback: CallbackQuery, state: FSMContext):
    data   = await state.get_data()
    count  = data.get("received_count", 0)
    cfg    = TASK_CONFIG[data.get("task_type", "task_150")]
    needed = cfg["count"]
    if count < needed:
        await callback.answer(f"⚠️ Мало!\nПолучено: {count}, нужно: {needed}", show_alert=True)
        return
    await state.set_state(ScreenshotUpload.confirming)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить и получить баллы",
                              callback_data="confirm_screenshots")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_upload")],
    ])
    await callback.message.answer(
        f"🎉 *Отлично!*\n\nСкриншотов: *{count}* ✅\nНаграда: *{cfg['reward']} 💎*\n\nНажми «Подтвердить» 👇",
        parse_mode="Markdown", reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data == "confirm_screenshots", ScreenshotUpload.confirming)
async def confirm_screenshots_cb(callback: CallbackQuery, state: FSMContext):
    data      = await state.get_data()
    cfg       = TASK_CONFIG[data.get("task_type", "task_150")]
    count     = data.get("received_count", 0)
    photo_ids = data.get("photo_file_ids", [])
    user      = await get_user(callback.from_user.id)
    await add_screenshots(callback.from_user.id, count)
    await add_points(callback.from_user.id, cfg["reward"])
    await state.clear()
    new_balance = (user["points"] if user else 0) + cfg["reward"]
    await send_task_report(callback.from_user.id, data.get("task_type", "task_150"), photo_ids)
    await callback.message.answer(
        f"🎊 *Баллы начислены!*\n\n✅ {cfg['label']}\n💎 +{cfg['reward']} баллов\n💰 Баланс: {new_balance} 💎\n\nПродолжай зарабатывать!",
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
    try:
        await callback.message.delete()
    except Exception:
        pass
    await send_main_menu(callback, user, callback.from_user.first_name)
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
    text = f"🎵 *Подключение TikTok*{current}\n\nОтправь свой TikTok никнейм (без @)\n\nПример: `myawesomeprofile`"
    try:
        await callback.message.edit_caption(caption=text, parse_mode="Markdown",
                                            reply_markup=keyboard)
    except Exception:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)
    await callback.answer()


@router.message(ConnectTikTok.waiting_for_username)
async def receive_tiktok_username(message: Message, state: FSMContext):
    username = validate_tiktok_username(message.text or "")
    if not username:
        await message.answer(
            "⚠️ *Неверный формат!*\n\nТолько буквы, цифры, точки и подчёркивания. Длина: 2–24 символа.\n\nПопробуй ещё раз:",
            parse_mode="Markdown",
        )
        return
    await update_tiktok(message.from_user.id, username)
    await state.clear()
    user = await get_user(message.from_user.id)
    await message.answer(f"✅ *TikTok подключён!*\n\n🎵 @{username}\n\nТеперь выполняй задание!",
                         parse_mode="Markdown")
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
        f"🛒 *Магазин*\n━━━━━━━━━━━━━━━━━━━\n\n"
        f"💎 Твои баллы: *{user['points']}*\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🎁 *Уникальный код* — 20 символов.\n"
        f"Отправь оператору {OPERATOR_USERNAME} → получи пробы!\n\n"
        f"💎 За баллы: *{CODE_PRICE_POINTS} 💎*\n"
        f"⭐ За звёзды: *{CODE_PRICE_STARS} Stars*\n"
        f"━━━━━━━━━━━━━━━━━━━"
    )
    try:
        await callback.message.edit_caption(caption=text, parse_mode="Markdown",
                                            reply_markup=shop_keyboard())
    except Exception:
        await callback.message.edit_text(text, parse_mode="Markdown",
                                         reply_markup=shop_keyboard())
    await callback.answer()


@router.callback_query(F.data == "buy_code_points")
async def buy_code_points_cb(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer("Ошибка", show_alert=True)
        return
    if user["points"] < CODE_PRICE_POINTS:
        await callback.answer(f"⚠️ Нужно: {CODE_PRICE_POINTS} 💎\nУ тебя: {user['points']} 💎",
                              show_alert=True)
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Потратить {CODE_PRICE_POINTS} 💎",
                              callback_data="confirm_buy_points")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="shop")],
    ])
    text = (
        f"🎁 *Подтверди покупку*\n\n"
        f"Стоимость: *{CODE_PRICE_POINTS} 💎*\n"
        f"Баланс: *{user['points']} 💎*\n"
        f"После: *{user['points'] - CODE_PRICE_POINTS} 💎*"
    )
    try:
        await callback.message.edit_caption(caption=text, parse_mode="Markdown",
                                            reply_markup=keyboard)
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
        f"🎉 *Код получен!*\n\n🔑 *Твой код:*\n`{formatted}`\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"1. Скопируй код\n2. Напиши: {OPERATOR_USERNAME}\n"
        f"3. Отправь код → получи пробы 🎁\n\n⚠️ Код одноразовый!"
    )
    try:
        await callback.message.edit_caption(caption=text, parse_mode="Markdown",
                                            reply_markup=back_keyboard())
    except Exception:
        await callback.message.edit_text(text, parse_mode="Markdown",
                                         reply_markup=back_keyboard())
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
            f"🎉 *Оплата прошла!*\n\n🔑 *Твой код:*\n`{formatted}`\n\n"
            f"Напиши {OPERATOR_USERNAME} и отправь код 🎁\n\n⚠️ Код одноразовый!",
            parse_mode="Markdown", reply_markup=main_menu_keyboard(),
        )


@router.callback_query(F.data == "my_codes")
async def my_codes_handler(callback: CallbackQuery):
    codes = await get_user_codes(callback.from_user.id)
    if not codes:
        text = "📋 *Мои коды*\n\nПока нет кодов. Купи в магазине!"
        try:
            await callback.message.edit_caption(caption=text, parse_mode="Markdown",
                                                reply_markup=back_keyboard())
        except Exception:
            await callback.message.edit_text(text, parse_mode="Markdown",
                                             reply_markup=back_keyboard())
        await callback.answer()
        return
    text = "📋 *Мои коды*\n━━━━━━━━━━━━━━━━━━━\n\n"
    for i, c in enumerate(codes[-10:], 1):
        status = "✅ Активен" if not c["used"] else "❌ Использован"
        text += f"*{i}.* `{format_code(c['code'])}`\n{status}\n\n"
    text += f"Отправь оператору: {OPERATOR_USERNAME}"
    try:
        await callback.message.edit_caption(caption=text, parse_mode="Markdown",
                                            reply_markup=back_keyboard())
    except Exception:
        await callback.message.edit_text(text, parse_mode="Markdown",
                                         reply_markup=back_keyboard())
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
        f"💎 Баллов: *{sum(u['points'] for u in users)}*\n"
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
    await message.answer(f"✅ Пользователю `{target_id}` начислено *{amount} 💎*",
                         parse_mode="Markdown")


# ─────────────────────────────────────────────
#  ЗАПУСК — WEBHOOK (конфликты невозможны)
# ─────────────────────────────────────────────

async def main():
    global _bot
    from aiohttp import web
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

    _bot = Bot(token=BOT_TOKEN)
    await load_all_from_channel()

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    RENDER_URL   = os.getenv("RENDER_EXTERNAL_URL", "https://si-wdhs.onrender.com")
    WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
    WEBHOOK_URL  = f"{RENDER_URL}{WEBHOOK_PATH}"

    await _bot.set_webhook(
        url=WEBHOOK_URL,
        allowed_updates=["message", "callback_query", "pre_checkout_query"],
        drop_pending_updates=True,
    )
    logger.info(f"✅ Webhook: {WEBHOOK_URL}")

    app = web.Application()

    async def health(request):
        return web.Response(text="OK")

    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    SimpleRequestHandler(dispatcher=dp, bot=_bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=_bot)

    port   = int(os.getenv("PORT", 10000))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    logger.info(f"🤖 Bot started on port {port}")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
