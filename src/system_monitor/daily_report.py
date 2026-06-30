"""
VAULTCUT - Daily Report
========================
Sends a comprehensive daily report on Telegram every morning at 09:00.
"""

import sqlite3
import os
import asyncio
from datetime import datetime, timedelta
from telegram import Bot
from src.telegram_bot.bot import get_token, get_chat_id
from src.logger import get_system_logger
from src.system_monitor.heartbeat import HEARTBEAT_FILE
import json

logger = get_system_logger()
DB_PATH = 'data/vaultcut.db'


def get_uptime():
    try:
        with open(HEARTBEAT_FILE, 'r') as f:
            hb = json.load(f)
        started = datetime.fromisoformat(hb.get('started_at', hb.get('timestamp')))
        delta = datetime.now() - started
        hours = int(delta.total_seconds() // 3600)
        minutes = int((delta.total_seconds() % 3600) // 60)
        return f"{hours}h {minutes}m"
    except Exception:
        return "Unknown"


def get_daily_stats():
    conn = sqlite3.connect(DB_PATH)
    stats = {}
    today = datetime.now().date().isoformat()
    yesterday = (datetime.now() - timedelta(days=1)).date().isoformat()

    try:
        # === DOWNLOADS ===
        stats['downloads_total'] = conn.execute(
            "SELECT COUNT(*) FROM downloaded_videos WHERE download_status='completed'"
        ).fetchone()[0]

        stats['downloads_today'] = conn.execute(
            "SELECT COUNT(*) FROM downloaded_videos WHERE download_status='completed' AND download_date LIKE ?",
            (f"{today}%",)
        ).fetchone()[0]

        stats['downloads_queued'] = conn.execute(
            "SELECT COUNT(*) FROM downloaded_videos WHERE download_status='queued'"
        ).fetchone()[0]

        stats['downloads_failed'] = conn.execute(
            "SELECT COUNT(*) FROM downloaded_videos WHERE download_status='failed'"
        ).fetchone()[0]

        stats['downloads_size_gb'] = conn.execute(
            "SELECT COUNT(*) FROM downloaded_videos WHERE download_status='completed' AND file_path IS NOT NULL"
        ).fetchone()[0]

        # === CLIPS ===
        stats['clips_total'] = conn.execute(
            "SELECT COUNT(*) FROM clips"
        ).fetchone()[0]

        stats['clips_cut_today'] = conn.execute(
            "SELECT COUNT(*) FROM clips WHERE status != 'pending_clip' AND created_date LIKE ?",
            (f"{today}%",)
        ).fetchone()[0]

        stats['clips_pending_cut'] = conn.execute(
            "SELECT COUNT(*) FROM clips WHERE status='pending_clip'"
        ).fetchone()[0]

        stats['clips_pending_approval'] = conn.execute(
            "SELECT COUNT(*) FROM clips WHERE status='pending_approval' AND telegram_sent=1"
        ).fetchone()[0]

        stats['clips_approved'] = conn.execute(
            "SELECT COUNT(*) FROM clips WHERE approval_status='approved'"
        ).fetchone()[0]

        stats['clips_rejected'] = conn.execute(
            "SELECT COUNT(*) FROM clips WHERE approval_status='rejected'"
        ).fetchone()[0]

        stats['clips_uploaded'] = conn.execute(
            "SELECT COUNT(*) FROM clips WHERE status='uploaded'"
        ).fetchone()[0]

        stats['clips_uploaded_today'] = conn.execute(
            "SELECT COUNT(*) FROM clips WHERE status='uploaded' AND uploaded_at LIKE ?",
            (f"{today}%",)
        ).fetchone()[0]

        # === CHANNELS ===
        stats['channels_total'] = conn.execute(
            "SELECT COUNT(*) FROM monitored_channels"
        ).fetchone()[0]

        stats['channels_active'] = conn.execute(
            "SELECT COUNT(*) FROM monitored_channels WHERE status='active'"
        ).fetchone()[0]

        # === TRENDS ===
        stats['trends_total'] = conn.execute(
            "SELECT COUNT(*) FROM trend_history"
        ).fetchone()[0]

        stats['trends_today'] = conn.execute(
            "SELECT COUNT(*) FROM trend_history WHERE check_date LIKE ?",
            (f"{today}%",)
        ).fetchone()[0]

        # === TOP CLIPS BY SCORE ===
        top_clips = conn.execute("""
            SELECT generated_title, virality_score, target_channel, status
            FROM clips
            WHERE virality_score IS NOT NULL AND generated_title IS NOT NULL
            ORDER BY virality_score DESC
            LIMIT 3
        """).fetchall()
        stats['top_clips'] = top_clips

        # === UPLOAD QUEUE ===
        stats['uploads_pending'] = conn.execute(
            "SELECT COUNT(*) FROM upload_schedule WHERE status='pending'"
        ).fetchone()[0]

        stats['uploads_done_today'] = conn.execute(
            "SELECT COUNT(*) FROM clips WHERE status='uploaded' AND uploaded_at LIKE ?",
            (f"{today}%",)
        ).fetchone()[0]

        # === DISK ===
        downloads_dir = 'data/downloads'
        clips_dir = 'data/clips'
        downloads_size = sum(
            os.path.getsize(os.path.join(downloads_dir, f))
            for f in os.listdir(downloads_dir)
            if os.path.isfile(os.path.join(downloads_dir, f))
        ) / (1024**3) if os.path.exists(downloads_dir) else 0

        clips_size = sum(
            os.path.getsize(os.path.join(clips_dir, f))
            for f in os.listdir(clips_dir)
            if os.path.isfile(os.path.join(clips_dir, f))
        ) / (1024**3) if os.path.exists(clips_dir) else 0

        stats['disk_downloads_gb'] = round(downloads_size, 2)
        stats['disk_clips_gb'] = round(clips_size, 2)

    except Exception as e:
        logger.error(f"Daily stats error: {e}")
    finally:
        conn.close()

    return stats


def format_daily_report(stats):
    now = datetime.now().strftime('%Y-%m-%d')
    uptime = get_uptime()

    lines = [
        f"📊 *VAULTCUT DAILY REPORT*",
        f"📅 {now} | ⏱ Uptime: {uptime}",
        f"{'─' * 32}",
        f"",
        f"📥 *DOWNLOADS*",
        f"  Total completed: {stats.get('downloads_total', 0)}",
        f"  Downloaded today: {stats.get('downloads_today', 0)}",
        f"  In queue: {stats.get('downloads_queued', 0)}",
        f"  Failed: {stats.get('downloads_failed', 0)}",
        f"",
        f"✂️ *CLIPS*",
        f"  Total in DB: {stats.get('clips_total', 0)}",
        f"  Cut today: {stats.get('clips_cut_today', 0)}",
        f"  Waiting to cut: {stats.get('clips_pending_cut', 0)}",
        f"  Waiting your approval: {stats.get('clips_pending_approval', 0)}",
        f"  Approved: {stats.get('clips_approved', 0)}",
        f"  Rejected: {stats.get('clips_rejected', 0)}",
        f"  Uploaded: {stats.get('clips_uploaded', 0)}",
        f"  Uploaded today: {stats.get('clips_uploaded_today', 0)}",
        f"",
        f"📤 *UPLOAD QUEUE*",
        f"  Pending: {stats.get('uploads_pending', 0)}",
        f"  Done today: {stats.get('uploads_done_today', 0)}",
        f"",
        f"📡 *CHANNELS & TRENDS*",
        f"  Channels monitored: {stats.get('channels_active', 0)}/{stats.get('channels_total', 0)}",
        f"  Trends tracked: {stats.get('trends_total', 0)}",
        f"  Trends today: {stats.get('trends_today', 0)}",
        f"",
        f"💾 *DISK USAGE*",
        f"  Downloads: {stats.get('disk_downloads_gb', 0):.2f} GB",
        f"  Clips: {stats.get('disk_clips_gb', 0):.2f} GB",
    ]

    top_clips = stats.get('top_clips', [])
    if top_clips:
        lines.append(f"")
        lines.append(f"⭐ *TOP CLIPS BY SCORE*")
        for clip in top_clips:
            title = (clip[0] or 'No title')[:35]
            score = clip[1] or 0
            channel = (clip[2] or 'Unknown')[:20]
            status = clip[3] or 'unknown'
            lines.append(f"  {score}/10 | {title} | {channel} | {status}")

    lines.append(f"")
    lines.append(f"{'─' * 32}")
    lines.append(f"_Generated automatically by VAULTCUT_")

    return '\n'.join(lines)


async def _send_report():
    stats = get_daily_stats()
    report = format_daily_report(stats)
    bot = Bot(token=get_token())
    async with bot:
        await bot.send_message(
            chat_id=get_chat_id(),
            text=report,
            parse_mode='Markdown'
        )
    logger.info("Daily report sent to Telegram")


def send_daily_report():
    try:
        asyncio.run(_send_report())
    except Exception as e:
        logger.error(f"Daily report failed: {e}")


if __name__ == '__main__':
    send_daily_report()
