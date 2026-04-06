import asyncio
import logging
import os
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

LOG_BOT_TOKEN = "8614712117:AAFfzBuqtHo5_0HjZRmzY53NqYEmq0VAQn0"
LOG_CHANNEL_ID = -1003513114819
WELCOME_IMAGE_URL = "https://i.postimg.cc/RZf9T864/photo-2026-03-09-22-19-32.jpg"
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "https://si-wdhs.onrender.com")

BOTS = [
    ("8614712117:AAFfzBuqtHo5_0HjZRmzY53NqYEmq0VAQn0", "@Ken_Si_Bot"),
    ("8672105339:AAHyfFykmcUppAVLBRh7qauLMvCHXGTWcSY", "@oldsi11bot"),
    ("8320791549:AAER3VNYgeEClEV4p-41pCCX_PVyk0-M1Nk", "@NF_Si_Bot"),
    ("8718226706:AAF0nFlopKzI0_V-_GzideqzbTDA-3MBL2c", "@SL_SI_BOT"),
    ("8768303694:AAFEDa4lOHFHX439A7vcfh2qGltZciQBYXE", "@sistore11bot"),
]


def welcome_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="ПРАЙС🔥", url="https://t.me/OldSiWs")]
    ])


async def log_user(user, bot_name: str):
    try:
        log_bot = Bot(token=LOG_BOT_TOKEN)
        now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        username = f"@{user.username}" if user.username else "нет"
        profile_link = f"tg://user?id={user.id}"
        text = (
            f"Новый пользователь зашёл в бот\n"
            f"Бот: {bot_name}\n"
            f"Профиль: {profile_link}\n"
            f"Имя: {user.first_name} {user.last_name or ''} ({username})\n"
            f"Время: {now}"
        )
        await log_bot.send_message(chat_id=LOG_CHANNEL_ID, text=text)
        await log_bot.session.close()
    except Exception as e:
        logger.error(f"Logging error: {e}")


def make_router(bot_name: str) -> Router:
    router = Router()

    @router.message(CommandStart())
    async def cmd_start(message: Message):
        await log_user(message.from_user, bot_name)

        caption = (
            f"🌟 *Рады приветствовать вас в нашем магазине!*\n\n"
            f"Добрый день 👋 {message.from_user.first_name}! "
            f"Ты попал туда, куда надо!\n\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"*📩ОТЗЫВЫ: Trava.ct.ws 👨\u200d💻Сайт: Trava.ct.ws*\n\n"
            f"ДЛЯ ЗАКАЗА ПИСАТЬ ОПЕРАТОРУ: @OldSiWs\n"
            f"ПРАЙС, НАЛИЧИЕ, КОНСУЛЬТАЦИЯ: @OldSiWs"
        )
        try:
            await message.answer_photo(
                photo=WELCOME_IMAGE_URL,
                caption=caption,
                parse_mode="Markdown",
                reply_markup=welcome_keyboard(),
            )
        except Exception:
            await message.answer(
                caption,
                parse_mode="Markdown",
                reply_markup=welcome_keyboard(),
            )

    return router


async def main():
    app = web.Application()

    async def health(request):
        return web.Response(text="OK")

    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    for token, bot_name in BOTS:
        bot = Bot(token=token)
        dp = Dispatcher()
        dp.include_router(make_router(bot_name))

        webhook_path = f"/webhook/{token}"
        webhook_url = f"{RENDER_URL}{webhook_path}"

        await bot.set_webhook(
            url=webhook_url,
            allowed_updates=["message"],
            drop_pending_updates=True,
        )
        logger.info(f"{bot_name} webhook: {webhook_url}")

        SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=webhook_path)
        setup_application(app, dp, bot=bot)

    port = int(os.getenv("PORT", 10000))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"All bots started on port {port}")

    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
