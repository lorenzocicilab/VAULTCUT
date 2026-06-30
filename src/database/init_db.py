import sqlite3
import os
from src.logger import get_system_logger

logger = get_system_logger()

DB_PATH = 'data/vaultcut.db'


def get_connection():
    """Restituisce una connessione al database SQLite."""
    os.makedirs('data', exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _add_column(conn, table, column, definition):
    """Aggiunge una colonna se non esiste gia."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        conn.commit()
    except Exception:
        pass


def init_db():
    """Inizializza il database e crea tutte le tabelle."""
    os.makedirs('data', exist_ok=True)
    conn = sqlite3.connect(DB_PATH)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS trend_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            check_date TEXT,
            source TEXT,
            trend_keyword TEXT,
            trend_score REAL,
            category TEXT,
            region TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS monitored_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT,
            channel_name TEXT,
            platform TEXT,
            category TEXT,
            subscriber_count INTEGER,
            avg_views INTEGER,
            channel_url TEXT,
            description TEXT,
            added_date TEXT,
            date_added TEXT,
            last_checked TEXT,
            status TEXT,
            discovery_source TEXT,
            priority INTEGER,
            total_videos_downloaded INTEGER DEFAULT 0,
            total_clips_generated INTEGER DEFAULT 0,
            check_frequency_hours INTEGER DEFAULT 24,
            min_views INTEGER DEFAULT 0,
            max_duration_minutes INTEGER DEFAULT 60
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS downloaded_videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id TEXT,
            title TEXT,
            channel TEXT,
            channel_id TEXT,
            uploader TEXT,
            url TEXT,
            duration_seconds INTEGER,
            view_count INTEGER,
            like_count INTEGER,
            published_at TEXT,
            download_status TEXT DEFAULT 'queued',
            queued_date TEXT,
            download_date TEXT,
            source_type TEXT,
            source_category TEXT,
            status TEXT,
            file_path TEXT,
            transcript_path TEXT,
            analysis_path TEXT,
            transcription_status TEXT,
            analysis_status TEXT,
            viral_score REAL,
            trend_match_score REAL,
            error_message TEXT,
            deleted INTEGER DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS clips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id TEXT,
            start_time REAL,
            end_time REAL,
            duration REAL,
            virality_score REAL,
            title TEXT,
            description TEXT,
            file_path TEXT,
            created_date TEXT,
            status TEXT,
            clip_type TEXT,
            reason TEXT,
            content_type TEXT,
            target_channel TEXT,
            approval_status TEXT,
            upload_status TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS telegram_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_type TEXT,
            content TEXT,
            sent_date TEXT,
            status TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            level TEXT,
            module TEXT,
            message TEXT,
            timestamp TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS upload_schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            clip_id INTEGER,
            channel TEXT,
            scheduled_time TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS upload_quota (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            channel TEXT,
            units_used INTEGER DEFAULT 0,
            uploads_count INTEGER DEFAULT 0
        )
    """)

    conn.commit()

    # Phase 8 columns - clips
    _add_column(conn, 'clips', 'generated_title', 'TEXT')
    _add_column(conn, 'clips', 'generated_description', 'TEXT')
    _add_column(conn, 'clips', 'generated_hashtags', 'TEXT')
    _add_column(conn, 'clips', 'target_channel', 'TEXT')
    _add_column(conn, 'clips', 'scheduled_upload_time', 'TEXT')
    _add_column(conn, 'clips', 'approval_status', 'TEXT')
    _add_column(conn, 'clips', 'approved_at', 'TEXT')
    _add_column(conn, 'clips', 'telegram_sent', 'INTEGER DEFAULT 0')
    _add_column(conn, 'clips', 'telegram_message_id', 'INTEGER')

    # Phase 9 columns - clips
    _add_column(conn, 'clips', 'youtube_video_id', 'TEXT')
    _add_column(conn, 'clips', 'youtube_url', 'TEXT')
    _add_column(conn, 'clips', 'uploaded_at', 'TEXT')
    _add_column(conn, 'clips', 'upload_error', 'TEXT')
    _add_column(conn, 'clips', 'privacy_status', 'TEXT')

    # Phase 9 columns - upload_schedule
    _add_column(conn, 'upload_schedule', 'platform', 'TEXT')
    _add_column(conn, 'upload_schedule', 'attempts', 'INTEGER DEFAULT 0')
    _add_column(conn, 'upload_schedule', 'last_attempt', 'TEXT')
    _add_column(conn, 'upload_schedule', 'youtube_video_id', 'TEXT')
    _add_column(conn, 'upload_schedule', 'error_message', 'TEXT')

    conn.close()
    logger.info("Database initialized successfully")


def get_stats():
    """Restituisce statistiche sul database."""
    conn = sqlite3.connect(DB_PATH)
    stats = {}
    try:
        stats['trends'] = conn.execute(
            "SELECT COUNT(*) FROM trend_history"
        ).fetchone()[0]
        stats['channels'] = conn.execute(
            "SELECT COUNT(*) FROM monitored_channels"
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
        stats['clips_approved'] = conn.execute(
            "SELECT COUNT(*) FROM clips WHERE status='approved'"
        ).fetchone()[0]
        stats['clips_uploaded'] = conn.execute(
            "SELECT COUNT(*) FROM clips WHERE status='uploaded'"
        ).fetchone()[0]
    except Exception as e:
        logger.error(f"Stats error: {e}")
    finally:
        conn.close()
    return stats


if __name__ == "__main__":
    init_db()
    print("Database initialized.")
