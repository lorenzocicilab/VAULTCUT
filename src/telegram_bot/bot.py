import json
import asyncio
from telegram import Bot
from telegram.ext import ApplicationBuilder


def load_settings():
    with open('config/settings.json', 'r', encoding='utf-8') as f:
        return json.load(f)


def get_token():
    return load_settings()['telegram']['bot_token']


def get_chat_id():
    return str(load_settings()['telegram']['your_chat_id'])


def get_application():
    from src.telegram_bot.handlers import register_handlers
    app = ApplicationBuilder().token(get_token()).build()
    register_handlers(app)
    return app


async def _send_text(text):
    bot = Bot(token=get_token())
    async with bot:
        await bot.send_message(chat_id=get_chat_id(), text=text)


def send_text_sync(text):
    asyncio.run(_send_text(text))


async def _send_video(path, caption=''):
    bot = Bot(token=get_token())
    async with bot:
        with open(path, 'rb') as f:
            await bot.send_video(
                chat_id=get_chat_id(),
                video=f,
                caption=caption,
                supports_streaming=True
            )


def send_video_sync(path, caption=''):
    asyncio.run(_send_video(path, caption))
