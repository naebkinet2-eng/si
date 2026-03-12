import asyncio
import logging
import os

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "8206313463:AAFxbANmtioF9T0zo1glaUwUrcehayVGoIE")
WELCOME_IMAGE_URL = "https://i.postimg.cc/RZf9T864/photo-2026-03-09-22-19-32.jpg"

router = Router()


def welcome_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="ПРАЙС🔥", url="https://t.me/OldSiWs")]
    ])


@router.message(CommandStart())
async def cmd_start(message: Message):
    caption = (
        f"🌟 *Рады приветствовать вас в нашем магазине!*\n\n"
        f"Добрый день 👋 {message.from_user.first_name}! "
        f"Ты попал туда, Куда надо!\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"*ОТЗЫВЫ: 👨‍💻Сайт: Trava.ct.ws*\n\n"
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


async def main():
    from aiohttp import web
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

    bot = Bot(token=BOT_TOKEN)
    dp  = Dispatcher()
    dp.include_router(router)

    RENDER_URL   = os.getenv("RENDER_EXTERNAL_URL", "https://si-wdhs.onrender.com")
    WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
    WEBHOOK_URL  = f"{RENDER_URL}{WEBHOOK_PATH}"

    await bot.set_webhook(
        url=WEBHOOK_URL,
        allowed_updates=["message"],
        drop_pending_updates=True,
    )
    logger.info(f"✅ Webhook: {WEBHOOK_URL}")

    app = web.Application()

    async def health(request):
        return web.Response(text="OK")

    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    port   = int(os.getenv("PORT", 10000))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    logger.info(f"🤖 Bot started on port {port}")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
