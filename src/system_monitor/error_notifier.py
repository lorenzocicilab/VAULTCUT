"""
VAULTCUT - Error Notifier
==========================
Hooks into Python's logging system.
Whenever an ERROR or CRITICAL log is emitted, sends a Telegram alert.
Includes deduplication so the same error doesn't spam.
"""

import logging
import asyncio
import re
from datetime import datetime, timedelta
from collections import defaultdict
from telegram import Bot
from src.telegram_bot.bot import get_token, get_chat_id

# Track recent errors to avoid spam (same error within 5 min = ignored)
_recent_errors = defaultdict(lambda: datetime.min)
_DEDUP_WINDOW = timedelta(minutes=5)

# Suggested fixes for common errors
ERROR_FIXES = {
    'timeout': 'Network slow or service down. Check connection.',
    'unauthorized': 'API token invalid. Check settings.json credentials.',
    'quota': 'API quota exceeded. Wait 24h or request more quota.',
    'ffmpeg': 'ffmpeg may not be installed. Run: winget install ffmpeg',
    'permission': 'File permission denied. Check folder access rights.',
    'disk': 'Disk full. Run cleanup or free space manually.',
    'sqlite': 'Database locked. Close other DB connections.',
    'connection': 'Network connection lost. Check internet.',
    'rate limit': 'Rate limit hit. Slow down requests or wait.',
    'no such column': 'Database schema outdated. Run init_db.py',
    'no such file': 'Required file missing. Check installation.',
    'json': 'JSON parse error. Check API response format.',
}


def suggest_fix(error_msg):
    """Return suggested fix based on error keywords."""
    msg_lower = error_msg.lower()
    for keyword, fix in ERROR_FIXES.items():
        if keyword in msg_lower:
            return fix
    return None


async def _send_alert(text):
    """Send error alert via Telegram."""
    try:
        bot = Bot(token=get_token())
        async with bot:
            await bot.send_message(
                chat_id=get_chat_id(),
                text=text,
                parse_mode='Markdown'
            )
    except Exception:
        pass  # Never crash on notification failure


class TelegramErrorHandler(logging.Handler):
    """
    Custom logging handler that sends ERROR/CRITICAL logs to Telegram.
    Has deduplication to prevent spam.
    """

    def __init__(self):
        super().__init__(level=logging.ERROR)

    def emit(self, record):
        try:
            module = record.name.replace('vaultcut.', '')
            error_msg = record.getMessage()

            # Dedup key
            dedup_key = f"{module}:{error_msg[:80]}"
            now = datetime.now()
            if now - _recent_errors[dedup_key] < _DEDUP_WINDOW:
                return  # Skip - sent same error recently
            _recent_errors[dedup_key] = now

            # Build message
            timestamp = datetime.fromtimestamp(record.created).strftime('%H:%M:%S')
            level = record.levelname
            fix = suggest_fix(error_msg)

            text = (
                f"? *{level}: {module}*\n\n"
                f"Time: {timestamp}\n"
                f"Error:\n`{error_msg[:300]}`\n"
            )
            if fix:
                text += f"\n?? *Suggested fix:* {fix}"

            # Send asynchronously without blocking
            try:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(_send_alert(text))
                loop.close()
            except Exception:
                pass

        except Exception:
            pass  # Never crash on notification failure


def install_error_notifier():
    """
    Attach the Telegram handler to the root vaultcut logger.
    Call this once at startup.
    """
    handler = TelegramErrorHandler()
    handler.setLevel(logging.ERROR)
    formatter = logging.Formatter('%(message)s')
    handler.setFormatter(formatter)

    root_logger = logging.getLogger('vaultcut')
    root_logger.addHandler(handler)
