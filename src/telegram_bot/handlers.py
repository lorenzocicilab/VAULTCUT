import sqlite3
import json
import asyncio
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, CallbackQueryHandler, MessageHandler,
    CommandHandler, filters
)
from src.logger import get_system_logger

logger = get_system_logger()
DB_PATH = 'data/vaultcut.db'
HEARTBEAT_PATH = 'data/heartbeat.json'


def get_clip(clip_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_db_stats():
    conn = sqlite3.connect(DB_PATH)
    stats = {}
    try:
        stats['channels'] = conn.execute(
            "SELECT COUNT(*) FROM monitored_channels WHERE status='active'"
        ).fetchone()[0]
        stats['videos_queued'] = conn.execute(
            "SELECT COUNT(*) FROM downloaded_videos WHERE download_status='queued'"
        ).fetchone()[0]
        stats['videos_downloaded'] = conn.execute(
            "SELECT COUNT(*) FROM downloaded_videos WHERE download_status='completed'"
        ).fetchone()[0]
        stats['clips_total'] = conn.execute(
            "SELECT COUNT(*) FROM clips"
        ).fetchone()[0]
        stats['clips_pending'] = conn.execute(
            "SELECT COUNT(*) FROM clips WHERE approval_status IS NULL OR approval_status='pending'"
        ).fetchone()[0]
        stats['clips_approved'] = conn.execute(
            "SELECT COUNT(*) FROM clips WHERE approval_status='approved'"
        ).fetchone()[0]
        stats['clips_uploaded'] = conn.execute(
            "SELECT COUNT(*) FROM clips WHERE status='uploaded'"
        ).fetchone()[0]
        stats['uploads_pending'] = conn.execute(
            "SELECT COUNT(*) FROM upload_schedule WHERE status='pending'"
        ).fetchone()[0]
        stats['trends'] = conn.execute(
            "SELECT COUNT(*) FROM trend_history"
        ).fetchone()[0]
        last_clip = conn.execute(
            "SELECT created_date FROM clips ORDER BY id DESC LIMIT 1"
        ).fetchone()
        stats['last_clip'] = last_clip[0] if last_clip else 'N/A'
        last_upload = conn.execute(
            "SELECT uploaded_at FROM clips WHERE status='uploaded' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        stats['last_upload'] = last_upload[0] if last_upload else 'N/A'
    except Exception as e:
        logger.error(f"Stats error: {e}")
    finally:
        conn.close()
    return stats


def get_uptime():
    try:
        with open(HEARTBEAT_PATH, 'r') as f:
            hb = json.load(f)
        started = datetime.fromisoformat(hb.get('started_at', hb.get('timestamp')))
        delta = datetime.now() - started
        hours = int(delta.total_seconds() // 3600)
        minutes = int((delta.total_seconds() % 3600) // 60)
        return f"{hours}h {minutes}m"
    except Exception:
        return "Unknown"


def get_last_heartbeat():
    try:
        with open(HEARTBEAT_PATH, 'r') as f:
            hb = json.load(f)
        ts = datetime.fromisoformat(hb.get('timestamp'))
        delta = datetime.now() - ts
        minutes = int(delta.total_seconds() // 60)
        return f"{minutes}m ago"
    except Exception:
        return "Unknown"


async def edit_message(query, text):
    """Edit message whether it has text or caption (video)."""
    try:
        await query.edit_message_text(text)
    except Exception:
        try:
            await query.edit_message_caption(caption=text)
        except Exception as e:
            logger.error(f"Could not edit message: {e}")
            await query.answer(text[:200], show_alert=True)


async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = get_db_stats()
    uptime = get_uptime()
    heartbeat = get_last_heartbeat()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    msg = f"""📊 VAULTCUT STATUS
🕐 {now}

🟢 System
  Uptime: {uptime}
  Heartbeat: {heartbeat}

📡 Discovery
  Channels monitored: {stats.get('channels', 0)}
  Trends tracked: {stats.get('trends', 0)}

📥 Downloads
  Queued: {stats.get('videos_queued', 0)}
  Completed: {stats.get('videos_downloaded', 0)}

✂️ Clips
  Total: {stats.get('clips_total', 0)}
  Pending review: {stats.get('clips_pending', 0)}
  Approved: {stats.get('clips_approved', 0)}
  Uploaded: {stats.get('clips_uploaded', 0)}
  Last clip: {stats.get('last_clip', 'N/A')}

📤 Upload Queue
  Pending: {stats.get('uploads_pending', 0)}
  Last upload: {stats.get('last_upload', 'N/A')}"""

    await update.message.reply_text(msg)


async def handle_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    clip_id = int(query.data.split('_')[1])
    clip = get_clip(clip_id)
    if not clip:
        await edit_message(query, "Clip not found.")
        return
    now = datetime.now().isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        UPDATE clips SET
            approval_status='approved',
            status='approved',
            approved_at=?,
            telegram_sent=1
        WHERE id=?
    """, (now, clip_id))
    conn.execute("""
        INSERT INTO upload_schedule
            (clip_id, channel, platform, scheduled_time, status, upload_date)
        VALUES (?, ?, 'youtube', ?, 'pending', NULL)
    """, (
        clip_id,
        clip.get('channel', clip.get('target_channel', '')),
        clip.get('scheduled_upload_time', ''),
    ))
    conn.commit()
    conn.close()
    scheduled = clip.get('scheduled_upload_time', 'soon') or 'soon'
    await edit_message(query, f"✅ Approved! Clip #{clip_id} scheduled for {scheduled}")


async def handle_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    clip_id = int(query.data.split('_')[1])
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        UPDATE clips SET
            approval_status='rejected',
            status='rejected',
            telegram_sent=1
        WHERE id=?
    """, (clip_id,))
    conn.commit()
    conn.close()
    await edit_message(query, f"❌ Rejected. Clip #{clip_id} will not be uploaded.")


async def handle_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    clip_id = int(query.data.split('_')[1])
    context.user_data['editing_clip_id'] = clip_id
    await edit_message(query, "✏️ Send me the new title (max 60 characters):")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'editing_clip_id' not in context.user_data:
        return
    clip_id = context.user_data.pop('editing_clip_id')
    new_title = update.message.text.strip()[:60]
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE clips SET generated_title=? WHERE id=?",
        (new_title, clip_id)
    )
    conn.commit()
    conn.close()
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ APPROVE", callback_data=f"approve_{clip_id}"),
        InlineKeyboardButton("❌ REJECT", callback_data=f"reject_{clip_id}")
    ]])
    await update.message.reply_text(
        f"✏️ Title updated:\n{new_title}\n\nChoose:",
        reply_markup=keyboard
    )


def register_handlers(application):
    application.add_handler(CommandHandler("status", handle_status))
    application.add_handler(
        CallbackQueryHandler(handle_approve, pattern=r'^approve_\d+$')
    )
    application.add_handler(
        CallbackQueryHandler(handle_reject, pattern=r'^reject_\d+$')
    )
    application.add_handler(
        CallbackQueryHandler(handle_edit, pattern=r'^edit_\d+$')
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
    )
