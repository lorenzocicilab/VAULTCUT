import sqlite3
import time
from datetime import datetime
from src.logger import get_system_logger
from src.uploader.youtube_uploader import upload_video, check_quota_available
from src.uploader.youtube_auth import has_valid_credentials
from src.uploader.channel_config import normalize_channel_name

logger = get_system_logger()
DB_PATH = 'data/vaultcut.db'


def get_ready_uploads():
    """Get uploads that are scheduled and ready to go."""
    now = datetime.now().isoformat()
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT us.id as schedule_id, us.clip_id, us.channel,
                   us.scheduled_time, us.attempts,
                   c.target_channel, c.generated_title
            FROM upload_schedule us
            JOIN clips c ON c.id = us.clip_id
            WHERE us.status='pending'
            AND us.scheduled_time <= ?
            AND (us.attempts IS NULL OR us.attempts < 3)
            ORDER BY us.scheduled_time ASC
        """, (now,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Failed to get ready uploads: {e}")
        return []


def increment_attempt(schedule_id, error=None):
    """Increment upload attempt counter."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            UPDATE upload_schedule SET
                attempts=COALESCE(attempts, 0)+1,
                last_attempt=?,
                error_message=?
            WHERE id=?
        """, (datetime.now().isoformat(), error, schedule_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to increment attempt for schedule {schedule_id}: {e}")


def mark_failed(schedule_id, clip_id, error):
    """Mark an upload as permanently failed."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "UPDATE upload_schedule SET status='failed', error_message=? WHERE id=?",
            (error, schedule_id)
        )
        conn.execute(
            "UPDATE clips SET upload_error=? WHERE id=?",
            (error, clip_id)
        )
        conn.commit()
        conn.close()
        logger.error(f"Marked clip {clip_id} upload as failed: {error}")
    except Exception as e:
        logger.error(f"Failed to mark upload failed: {e}")


def process_upload_queue():
    """Process all uploads that are due."""
    uploads = get_ready_uploads()
    if not uploads:
        logger.info("No uploads ready at this time")
        return

    logger.info(f"Found {len(uploads)} upload(s) ready to process")

    for i, upload in enumerate(uploads):
        clip_id = upload['clip_id']
        channel_raw = upload.get('channel') or upload.get('target_channel')
        channel = normalize_channel_name(channel_raw)

        if not channel:
            logger.error(f"Unknown channel '{channel_raw}' for clip {clip_id}")
            mark_failed(upload['schedule_id'], clip_id, f"Unknown channel: {channel_raw}")
            continue

        if not has_valid_credentials(channel):
            logger.warning(f"No credentials for '{channel}' — skipping clip {clip_id}")
            logger.warning(f"Fix with: python manage_channels.py auth \"{channel}\"")
            increment_attempt(upload['schedule_id'], "No credentials")
            continue

        if not check_quota_available(channel):
            logger.warning(f"Daily quota exhausted for {channel}, skipping")
            continue

        increment_attempt(upload['schedule_id'])
        logger.info(f"Uploading clip {clip_id} → {channel}")

        success, result = upload_video(clip_id, channel)

        if success:
            logger.info(f"✅ Clip {clip_id} uploaded successfully: {result}")
            send_upload_notification(clip_id, result, channel)
        else:
            logger.error(f"❌ Clip {clip_id} upload failed: {result}")
            attempts = (upload.get('attempts') or 0) + 1
            if attempts >= 3:
                logger.error(f"Clip {clip_id} failed 3 times, marking permanently failed")
                mark_failed(upload['schedule_id'], clip_id, result)

        if i < len(uploads) - 1:
            logger.info("Waiting 5 minutes before next upload...")
            time.sleep(300)


def send_upload_notification(clip_id, video_id, channel):
    """Send Telegram notification when a clip is successfully uploaded."""
    try:
        from src.telegram_bot.bot import send_text_sync
        url = f"https://youtube.com/shorts/{video_id}"
        msg = f"""✅ CLIP UPLOADED TO YOUTUBE

📺 Channel: {channel}
🎬 Clip ID: {clip_id}
🔗 URL: {url}"""
        send_text_sync(msg)
    except Exception as e:
        logger.error(f"Failed to send upload notification: {e}")
