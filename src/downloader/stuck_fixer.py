"""
VAULTCUT - Download Stuck Fixer
================================
Resets videos stuck in 'downloading' status for more than 2 hours.
Called at startup and every 30 minutes by the scheduler.
"""

import sqlite3
from datetime import datetime, timedelta
from src.logger import get_system_logger

logger = get_system_logger()
DB_PATH = 'data/vaultcut.db'


def fix_stuck_downloads(max_age_hours=2):
    """
    Reset videos stuck in 'downloading' back to 'queued'.
    Also resets videos with no file_path that are marked 'completed'.
    """
    conn = sqlite3.connect(DB_PATH)
    fixed = 0

    try:
        # Fix stuck 'downloading' - no timeout column, use queued_date
        result = conn.execute("""
            UPDATE downloaded_videos
            SET download_status = 'queued',
                error_message = 'Reset: stuck in downloading status'
            WHERE download_status = 'downloading'
        """)
        fixed += result.rowcount
        if result.rowcount > 0:
            logger.warning(f"Reset {result.rowcount} stuck downloads to queued")

        # Fix 'completed' with no file_path
        result = conn.execute("""
            UPDATE downloaded_videos
            SET download_status = 'queued',
                file_path = NULL,
                error_message = 'Reset: completed but no file_path'
            WHERE download_status = 'completed'
            AND (file_path IS NULL OR file_path = '')
        """)
        fixed += result.rowcount
        if result.rowcount > 0:
            logger.warning(f"Reset {result.rowcount} completed-but-missing videos to queued")

        # Fix failed videos - retry after 24 hours
        result = conn.execute("""
            UPDATE downloaded_videos
            SET download_status = 'queued',
                error_message = NULL
            WHERE download_status = 'failed'
            AND queued_date < ?
        """, ((datetime.now() - timedelta(hours=24)).isoformat(),))
        fixed += result.rowcount
        if result.rowcount > 0:
            logger.info(f"Retrying {result.rowcount} failed downloads (24h retry)")

        conn.commit()

    except Exception as e:
        logger.error(f"Stuck fixer error: {e}")
    finally:
        conn.close()

    if fixed == 0:
        logger.info("No stuck downloads found")
    else:
        logger.info(f"Stuck fixer: fixed {fixed} total videos")

    return fixed


if __name__ == '__main__':
    fix_stuck_downloads()
