import sqlite3
from datetime import datetime, timedelta
from src.logger import get_system_logger

logger = get_system_logger()

CHANNEL_SLOTS = {
    'VAULTCUT Gaming': [18, 20, 22],
    'VAULTCUT News': [7, 9],
    'VAULTCUT Sports': [18],
    'VAULTCUT Entertainment': [12, 20],
    'VAULTCUT Tech': [10, 19]
}


def is_slot_taken(channel, candidate_dt, db_path='data/vaultcut.db'):
    window_start = (candidate_dt - timedelta(hours=1)).isoformat()
    window_end = (candidate_dt + timedelta(hours=1)).isoformat()
    conn = sqlite3.connect(db_path)
    count = conn.execute(
        """SELECT COUNT(*) FROM upload_schedule
           WHERE channel=? AND scheduled_time BETWEEN ? AND ?
           AND status='pending'""",
        (channel, window_start, window_end)
    ).fetchone()[0]
    conn.close()
    return count > 0


def calculate_upload_time(channel_name, db_path='data/vaultcut.db'):
    slots = CHANNEL_SLOTS.get(channel_name, [12])
    now = datetime.now()
    for days_ahead in range(7):
        target_date = now + timedelta(days=days_ahead)
        for hour in slots:
            candidate = target_date.replace(
                hour=hour, minute=0, second=0, microsecond=0
            )
            if candidate > now + timedelta(minutes=30):
                if not is_slot_taken(channel_name, candidate, db_path):
                    logger.info(f"Scheduled for {candidate.isoformat()}")
                    return candidate.isoformat()
    fallback = now + timedelta(hours=2)
    return fallback.isoformat()
