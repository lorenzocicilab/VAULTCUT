"""
VAULTCUT Database Manager — Phase 3 Update
============================================
This is the UPDATED version of init_db.py.

What changed from Phase 1/2:
  - get_db_path() checks data/vaultcut.db first (your setup)
  - monitored_channels gets 7 new columns (all optional with defaults)
  - downloaded_videos gets 6 new columns (all optional with defaults)
  - All additions use manual EXISTS check — safe to run many times
  - All existing rows and data are fully preserved

Run this to apply the schema updates:
    PowerShell: python src\database\init_db.py
"""

import sqlite3
import os
import sys
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)


def get_db_path() -> str:
    """
    Returns the full absolute path to vaultcut.db.
    Checks data/ subfolder first (your current setup),
    then falls back to the project root.
    """
    data_dir = os.path.join(PROJECT_ROOT, "data")
    if os.path.exists(data_dir):
        return os.path.join(data_dir, "vaultcut.db")
    return os.path.join(PROJECT_ROOT, "vaultcut.db")


def get_connection() -> sqlite3.Connection:
    """Opens a connection. Rows behave like dicts (access by column name)."""
    conn = sqlite3.connect(get_db_path())
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Returns True if the column already exists in the table."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in rows)


def _add_column_if_missing(conn, table: str, column: str, definition: str):
    """
    Adds a column to a table only if it is not already there.
    SQLite has no 'ADD COLUMN IF NOT EXISTS' syntax, so we check first.
    """
    if not _column_exists(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        print(f"  [DB] + Added column: {table}.{column}")
    else:
        print(f"  [DB]   Exists (skip): {table}.{column}")


def initialize_database():
    """
    Creates all tables if they don't exist, then adds any new
    Phase 3 columns to existing tables. Completely safe to re-run.
    """
    db_path = get_db_path()
    print(f"[DB] Path: {db_path}")
    conn = get_connection()
    cur = conn.cursor()

    # ── monitored_channels ────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS monitored_channels (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type           TEXT NOT NULL,
            channel_id            TEXT,
            username              TEXT,
            channel_name          TEXT NOT NULL,
            category              TEXT NOT NULL DEFAULT 'entertainment',
            active                INTEGER DEFAULT 1,
            added_by              TEXT DEFAULT 'manual',
            date_added            TEXT NOT NULL,
            last_checked          TEXT,
            total_clips_generated INTEGER DEFAULT 0,
            UNIQUE(source_type, channel_id),
            UNIQUE(source_type, username)
        )
    """)
    # Phase 3 additions
    _add_column_if_missing(conn, "monitored_channels", "discovery_source",  "TEXT DEFAULT 'manual'")
    _add_column_if_missing(conn, "monitored_channels", "priority",           "REAL DEFAULT 5.0")
    _add_column_if_missing(conn, "monitored_channels", "total_videos_downloaded", "INTEGER DEFAULT 0")
    _add_column_if_missing(conn, "monitored_channels", "subscriber_count",   "INTEGER DEFAULT 0")
    _add_column_if_missing(conn, "monitored_channels", "avg_views",          "REAL DEFAULT 0.0")
    _add_column_if_missing(conn, "monitored_channels", "channel_url",        "TEXT DEFAULT ''")
    _add_column_if_missing(conn, "monitored_channels", "description",        "TEXT DEFAULT ''")

    # ── downloaded_videos ─────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS downloaded_videos (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type          TEXT NOT NULL,
            video_id             TEXT NOT NULL,
            channel_id           INTEGER,
            title                TEXT,
            uploader             TEXT,
            source_url           TEXT NOT NULL,
            file_path            TEXT,
            duration_seconds     INTEGER,
            file_size_mb         REAL,
            download_date        TEXT NOT NULL,
            transcription_status TEXT DEFAULT 'pending',
            analysis_status      TEXT DEFAULT 'pending',
            deleted              INTEGER DEFAULT 0,
            UNIQUE(source_type, video_id),
            FOREIGN KEY (channel_id) REFERENCES monitored_channels(id)
        )
    """)
    # Phase 3 additions
    _add_column_if_missing(conn, "downloaded_videos", "queued_date",     "TEXT DEFAULT ''")
    _add_column_if_missing(conn, "downloaded_videos", "download_status", "TEXT DEFAULT 'queued'")
    _add_column_if_missing(conn, "downloaded_videos", "source_category", "TEXT DEFAULT 'entertainment'")
    _add_column_if_missing(conn, "downloaded_videos", "published_at",    "TEXT DEFAULT ''")
    _add_column_if_missing(conn, "downloaded_videos", "view_count",      "INTEGER DEFAULT 0")
    _add_column_if_missing(conn, "downloaded_videos", "like_count",      "INTEGER DEFAULT 0")
    # Phase 5 additions
    _add_column_if_missing(conn, "downloaded_videos", "transcript_path", "TEXT DEFAULT ''")
    _add_column_if_missing(conn, "downloaded_videos", "error_message",   "TEXT DEFAULT ''")

    # ── trend_history (unchanged) ──────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trend_history (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            check_date    TEXT NOT NULL,
            source        TEXT NOT NULL,
            trend_keyword TEXT NOT NULL,
            trend_score   REAL,
            category      TEXT,
            region        TEXT DEFAULT 'US'
        )
    """)

    # ── clips (unchanged) ─────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS clips (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id              INTEGER NOT NULL,
            clip_filename         TEXT,
            clip_path             TEXT,
            processed_path        TEXT,
            thumbnail_path        TEXT,
            start_time_seconds    REAL,
            end_time_seconds      REAL,
            duration_seconds      REAL,
            clip_score            REAL,
            trend_match_score     REAL,
            content_type          TEXT,
            subtitle_style        TEXT,
            target_channel        TEXT,
            generated_title       TEXT,
            generated_description TEXT,
            generated_hashtags    TEXT,
            approval_status       TEXT DEFAULT 'pending',
            approval_date         TEXT,
            upload_status         TEXT DEFAULT 'waiting',
            upload_date           TEXT,
            youtube_video_id      TEXT,
            scheduled_time        TEXT,
            created_date          TEXT NOT NULL,
            FOREIGN KEY (video_id) REFERENCES downloaded_videos(id)
        )
    """)

    # ── telegram_messages (unchanged) ─────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS telegram_messages (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            clip_id             INTEGER NOT NULL,
            telegram_message_id INTEGER,
            sent_date           TEXT NOT NULL,
            response            TEXT,
            response_date       TEXT,
            FOREIGN KEY (clip_id) REFERENCES clips(id)
        )
    """)

    # ── system_logs (unchanged) ────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS system_logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp  TEXT NOT NULL,
            level      TEXT NOT NULL,
            module     TEXT NOT NULL,
            message    TEXT NOT NULL,
            extra_data TEXT
        )
    """)

    # ── upload_schedule (unchanged) ────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS upload_schedule (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_key    TEXT NOT NULL,
            scheduled_time TEXT NOT NULL,
            clip_id        INTEGER,
            status         TEXT DEFAULT 'scheduled',
            FOREIGN KEY (clip_id) REFERENCES clips(id)
        )
    """)

    conn.commit()
    conn.close()
    print("[DB] All tables ready.")
    return True


def get_stats() -> dict:
    """Returns a {table_name: row_count} dict. Used by main.py at startup."""
    conn = get_connection()
    stats = {}
    tables = [
        "monitored_channels", "downloaded_videos", "clips",
        "trend_history", "telegram_messages", "system_logs", "upload_schedule"
    ]
    for table in tables:
        try:
            stats[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except Exception:
            stats[table] = "ERROR"
    conn.close()
    return stats


def log_to_db(module: str, level: str, message: str, extra_data: str = None):
    """Writes an entry to the system_logs table. Silently ignores errors."""
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO system_logs (timestamp, level, module, message, extra_data) VALUES (?,?,?,?,?)",
            (datetime.now().isoformat(), level, module, message, extra_data)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ============================================================
# Run directly to apply schema updates
# PowerShell: python src\database\init_db.py
# ============================================================
if __name__ == "__main__":
    print("=" * 55)
    print("VAULTCUT Database — Phase 3 Schema Update")
    print("=" * 55)
    initialize_database()
    print()
    print("[DB] Table row counts:")
    for table, count in get_stats().items():
        print(f"  {table:<32} {count} rows")
    print()
    print("[DB] Done. Your existing data is untouched.")
