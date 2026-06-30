"""
VAULTCUT - Status Tracker
==========================
Sends Telegram messages on system online/offline/crash events.
"""

import asyncio
from datetime import datetime
from telegram import Bot
from src.telegram_bot.bot import get_token, get_chat_id
from src.system_monitor.heartbeat import detect_downtime, detect_crash, get_last_heartbeat
from src.logger import get_system_logger

logger = get_system_logger()


async def _send_telegram(text):
    """Send text message to admin chat."""
    try:
        bot = Bot(token=get_token())
        async with bot:
            await bot.send_message(
                chat_id=get_chat_id(),
                text=text,
                parse_mode='Markdown'
            )
        return True
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


def send_telegram_sync(text):
    """Sync wrapper for sending Telegram."""
    try:
        asyncio.run(_send_telegram(text))
    except Exception as e:
        logger.error(f"Telegram sync send failed: {e}")


def notify_startup():
    """Send Telegram on system startup. Detect crash/downtime."""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Check for crash (previous instance died)
    if detect_crash():
        last = get_last_heartbeat()
        crash_time = last.strftime('%H:%M:%S') if last else 'unknown'
        msg = (
            f"?? *VAULTCUT crashed unexpectedly*\n\n"
            f"Last heartbeat: {crash_time}\n"
            f"Restarted at: {now}\n"
            f"Status: *RECOVERING*"
        )
        send_telegram_sync(msg)
        return

    # Check for downtime
    downtime = detect_downtime()
    if downtime:
        msg = (
            f"? *VAULTCUT is back ONLINE*\n\n"
            f"Offline for: *{downtime['duration_str']}*\n"
            f"From: {downtime['offline_from'].strftime('%H:%M:%S')}\n"
            f"To: {downtime['online_at'].strftime('%H:%M:%S')}\n\n"
            f"All systems resuming."
        )
        send_telegram_sync(msg)
        logger.info(f"Downtime detected: {downtime['duration_str']}")
        return

    # Normal startup
    msg = (
        f"? *VAULTCUT is now ONLINE*\n\n"
        f"Started at: {now}\n"
        f"All 9 phases active."
    )
    send_telegram_sync(msg)
    logger.info("Startup notification sent")


def notify_shutdown():
    """Send Telegram on graceful shutdown."""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    msg = (
        f"?? *VAULTCUT is now OFFLINE*\n\n"
        f"Stopped at: {now}\n"
        f"Reason: graceful shutdown"
    )
    send_telegram_sync(msg)
    logger.info("Shutdown notification sent")
