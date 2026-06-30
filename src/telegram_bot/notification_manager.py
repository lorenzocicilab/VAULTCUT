import sqlite3
import os
import asyncio
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from src.telegram_bot.bot import get_token, get_chat_id
from src.logger import get_system_logger

logger = get_system_logger()
DB_PATH = 'data/vaultcut.db'


def get_clip(clip_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_video(video_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM downloaded_videos WHERE video_id=?", (video_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else {}


def build_keyboard(clip_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ APPROVE", callback_data=f"approve_{clip_id}"),
            InlineKeyboardButton("❌ REJECT", callback_data=f"reject_{clip_id}")
        ],
        [
            InlineKeyboardButton("✏️ EDIT TITLE", callback_data=f"edit_{clip_id}")
        ]
    ])


def format_caption(clip, video):
    duration = int(clip.get('duration', 0))
    score = clip.get('virality_score', 0)
    minutes = duration // 60
    seconds = duration % 60
    duration_str = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"

    title = clip.get('generated_title', 'No title') or 'No title'
    channel = clip.get('target_channel', 'Unknown') or 'Unknown'
    clip_type = clip.get('clip_type', 'unknown') or 'unknown'
    scheduled = clip.get('scheduled_upload_time', 'TBD') or 'TBD'
    url = video.get('url', 'N/A') or 'N/A'

    # Telegram caption max 1024 chars
    caption = (
        f"🎬 NEW CLIP #{clip.get('id')} - REVIEW\n"
        f"{'─' * 30}\n"
        f"📺 Channel: {channel}\n"
        f"⏱ Duration: {duration_str}\n"
        f"⭐ Score: {score}/10\n"
        f"🏷 Type: {clip_type}\n"
        f"🕐 Scheduled: {scheduled}\n"
        f"{'─' * 30}\n"
        f"📝 {title}\n"
        f"{'─' * 30}\n"
        f"🔗 {url}"
    )

    # Truncate if too long
    if len(caption) > 1024:
        caption = caption[:1020] + "..."

    return caption


async def _send_for_approval(clip_id):
    clip = get_clip(clip_id)
    if not clip:
        logger.error(f"Clip {clip_id} not found")
        return False

    video = get_video(clip.get('video_id', ''))
    token = get_token()
    chat_id = get_chat_id()
    bot = Bot(token=token)
    keyboard = build_keyboard(clip_id)
    caption = format_caption(clip, video)
    fp = clip.get('file_path', '')

    async with bot:
        if fp and os.path.exists(fp):
            size_mb = os.path.getsize(fp) / (1024 * 1024)

            if size_mb <= 50:
                # Send video with caption + buttons in ONE message (best mobile preview)
                with open(fp, 'rb') as f:
                    await bot.send_video(
                        chat_id=chat_id,
                        video=f,
                        caption=caption,
                        reply_markup=keyboard,
                        supports_streaming=True,
                        width=1080,
                        height=1920,
                        read_timeout=120,
                        write_timeout=120,
                        connect_timeout=30
                    )
                logger.info(f"Clip {clip_id} sent as video ({size_mb:.1f}MB)")

            else:
                # Too large: send text + buttons only
                await bot.send_message(
                    chat_id=chat_id,
                    text=caption + f"\n\n⚠️ Video too large for preview ({size_mb:.1f}MB)",
                    reply_markup=keyboard
                )
                logger.info(f"Clip {clip_id} sent as text only (too large: {size_mb:.1f}MB)")

        else:
            # No file: send text + buttons
            await bot.send_message(
                chat_id=chat_id,
                text=caption + "\n\n⚠️ Video file not available for preview",
                reply_markup=keyboard
            )
            logger.info(f"Clip {clip_id} sent as text only (no file)")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE clips SET telegram_sent=1 WHERE id=?", (clip_id,))
    conn.commit()
    conn.close()
    return True


def send_clip_for_approval(clip_id):
    return asyncio.run(_send_for_approval(clip_id))
