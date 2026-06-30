import sqlite3
import time
from datetime import datetime, timedelta
from src.logger import get_system_logger
from src.telegram_bot.notification_manager import send_clip_for_approval

logger = get_system_logger()
DB_PATH = 'data/vaultcut.db'


def get_unsent_clips():
    """Get clips ready for Telegram approval.
    
    Flow: pending_clip -> ready_to_upload -> metadata -> pending_approval -> telegram
    Also catches ready_to_upload clips that skipped metadata.
    """
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT id FROM clips
        WHERE status IN ('pending_approval', 'ready_to_upload')
        AND file_path IS NOT NULL
        AND (telegram_sent=0 OR telegram_sent IS NULL)
        ORDER BY virality_score DESC
    """).fetchall()
    conn.close()
    return [r[0] for r in rows]


def auto_approve_expired():
    """Auto-approve clips older than 30 days without response."""
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT id FROM clips
        WHERE status='pending_approval'
        AND created_date < ?
    """, (cutoff,)).fetchall()
    now = datetime.now().isoformat()
    for (clip_id,) in rows:
        conn.execute("""
            UPDATE clips SET
                status='approved',
                approval_status='approved',
                approved_at=?
            WHERE id=?
        """, (now, clip_id))
        logger.info(f"Auto-approved clip {clip_id} after 30d timeout")
    conn.commit()
    conn.close()


def run_telegram_queue():
    auto_approve_expired()
    pending = get_unsent_clips()
    if not pending:
        logger.info("No clips pending Telegram notification")
        return
    logger.info(f"Sending {len(pending)} clips to Telegram")
    for i, clip_id in enumerate(pending):
        logger.info(f"Sending clip {clip_id}")
        success = send_clip_for_approval(clip_id)
        if success:
            # Mark as pending_approval after sending
            conn = sqlite3.connect(DB_PATH)
            conn.execute("""
                UPDATE clips SET status='pending_approval'
                WHERE id=? AND status IN ('ready_to_upload', 'pending_approval')
            """, (clip_id,))
            conn.commit()
            conn.close()
            logger.info(f"Clip {clip_id} sent and marked pending_approval")
        else:
            logger.error(f"Failed to send clip {clip_id}")
        if i < len(pending) - 1:
            time.sleep(5)


if __name__ == '__main__':
    import os
    import sys
    sys.path.insert(0, os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', '..')
    ))
    os.chdir(os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', '..')
    ))
    run_telegram_queue()
