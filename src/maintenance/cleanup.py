"""
VAULTCUT - Storage Cleanup
===========================
Deletes old downloaded videos and clips after they've been processed.
Keeps disk space under control.

Rules:
  - Source MP4: deleted after ALL its clips have been uploaded to YouTube
  - Clip MP4:   deleted N days after upload (default 7 days, configurable)
  - Transcripts: NEVER deleted (small JSON, useful archive)
  - Failed/pending items: NEVER deleted

Run manually:
    python src\maintenance\cleanup.py

Run via scheduler (every 6 hours):
    Added to main.py automatically
"""

import os
import sys
import sqlite3
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from src.logger import get_system_logger

logger = get_system_logger()

DB_PATH = 'data/vaultcut.db'
DOWNLOADS_DIR = 'data/downloads'
CLIPS_DIR = 'data/clips'

# How many days to keep clip MP4 files after upload (0 = delete immediately)
KEEP_CLIPS_DAYS = 7


def cleanup_source_videos():
    """
    Deletes downloaded source MP4 files where ALL clips from that video
    have been successfully uploaded to YouTube.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Find videos where all their clips are uploaded (or no pending clips exist)
    rows = conn.execute("""
        SELECT dv.id, dv.video_id, dv.file_path, dv.title
        FROM downloaded_videos dv
        WHERE dv.download_status = 'completed'
        AND dv.file_path IS NOT NULL
        AND EXISTS (
            SELECT 1 FROM clips c WHERE c.video_id = dv.id
        )
        AND NOT EXISTS (
            SELECT 1 FROM clips c
            WHERE c.video_id = dv.id
            AND c.status NOT IN ('uploaded', 'rejected', 'failed')
        )
    """).fetchall()

    conn.close()

    deleted_count = 0
    deleted_size_mb = 0.0

    for row in rows:
        file_path = row['file_path']
        if not file_path or not os.path.exists(file_path):
            continue

        try:
            size_mb = os.path.getsize(file_path) / (1024 * 1024)
            os.remove(file_path)
            logger.info(f"Deleted source: {row['title'][:50]} ({size_mb:.1f}MB)")

            # Mark in DB that file was deleted
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                "UPDATE downloaded_videos SET file_path=NULL WHERE id=?",
                (row['id'],)
            )
            conn.commit()
            conn.close()

            deleted_count += 1
            deleted_size_mb += size_mb
        except Exception as e:
            logger.error(f"Failed to delete {file_path}: {e}")

    if deleted_count > 0:
        logger.info(f"Source cleanup: {deleted_count} files deleted, {deleted_size_mb:.1f}MB freed")
    return deleted_count, deleted_size_mb


def cleanup_old_clips():
    """
    Deletes clip MP4 files older than KEEP_CLIPS_DAYS that have been uploaded.
    """
    if KEEP_CLIPS_DAYS <= 0:
        cutoff = datetime.now()
    else:
        cutoff = datetime.now() - timedelta(days=KEEP_CLIPS_DAYS)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT id, file_path, title, uploaded_at
        FROM clips
        WHERE status = 'uploaded'
        AND file_path IS NOT NULL
        AND uploaded_at < ?
    """, (cutoff.isoformat(),)).fetchall()

    conn.close()

    deleted_count = 0
    deleted_size_mb = 0.0

    for row in rows:
        file_path = row['file_path']
        if not file_path or not os.path.exists(file_path):
            continue

        try:
            size_mb = os.path.getsize(file_path) / (1024 * 1024)
            os.remove(file_path)
            logger.info(f"Deleted clip: {row['title'][:50]} ({size_mb:.1f}MB)")

            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                "UPDATE clips SET file_path=NULL WHERE id=?",
                (row['id'],)
            )
            conn.commit()
            conn.close()

            deleted_count += 1
            deleted_size_mb += size_mb
        except Exception as e:
            logger.error(f"Failed to delete {file_path}: {e}")

    if deleted_count > 0:
        logger.info(f"Clip cleanup: {deleted_count} files deleted, {deleted_size_mb:.1f}MB freed")
    return deleted_count, deleted_size_mb


def cleanup_orphan_files():
    """
    Deletes files in data/downloads/ that don't appear in the database.
    These are leftover from crashed downloads.
    """
    if not os.path.exists(DOWNLOADS_DIR):
        return 0, 0.0

    conn = sqlite3.connect(DB_PATH)
    db_files = set()
    rows = conn.execute("SELECT file_path FROM downloaded_videos WHERE file_path IS NOT NULL").fetchall()
    for row in rows:
        if row[0]:
            db_files.add(os.path.basename(row[0]))
    conn.close()

    deleted_count = 0
    deleted_size_mb = 0.0

    for filename in os.listdir(DOWNLOADS_DIR):
        if not filename.endswith('.mp4'):
            continue
        if filename in db_files:
            continue

        file_path = os.path.join(DOWNLOADS_DIR, filename)
        try:
            # Skip files modified in last 30 minutes (might be in-progress download)
            mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
            if datetime.now() - mtime < timedelta(minutes=30):
                continue

            size_mb = os.path.getsize(file_path) / (1024 * 1024)
            os.remove(file_path)
            logger.info(f"Deleted orphan: {filename} ({size_mb:.1f}MB)")
            deleted_count += 1
            deleted_size_mb += size_mb
        except Exception as e:
            logger.error(f"Failed to delete orphan {filename}: {e}")

    if deleted_count > 0:
        logger.info(f"Orphan cleanup: {deleted_count} files deleted, {deleted_size_mb:.1f}MB freed")
    return deleted_count, deleted_size_mb


def get_disk_usage():
    """Returns (downloads_mb, clips_mb, free_mb)."""
    import shutil

    def folder_size_mb(path):
        if not os.path.exists(path):
            return 0
        total = 0
        for f in os.listdir(path):
            full = os.path.join(path, f)
            if os.path.isfile(full):
                total += os.path.getsize(full)
        return total / (1024 * 1024)

    downloads_mb = folder_size_mb(DOWNLOADS_DIR)
    clips_mb = folder_size_mb(CLIPS_DIR)
    _, _, free_bytes = shutil.disk_usage(PROJECT_ROOT)
    free_mb = free_bytes / (1024 * 1024)
    return downloads_mb, clips_mb, free_mb


def run_cleanup():
    """Main entry point called by scheduler."""
    logger.info("=" * 55)
    logger.info("STORAGE CLEANUP: Starting run")
    logger.info("=" * 55)

    downloads_mb, clips_mb, free_mb = get_disk_usage()
    logger.info(f"Before: downloads={downloads_mb:.1f}MB | clips={clips_mb:.1f}MB | free={free_mb/1024:.1f}GB")

    src_count, src_freed = cleanup_source_videos()
    clip_count, clip_freed = cleanup_old_clips()
    orphan_count, orphan_freed = cleanup_orphan_files()

    total_freed = src_freed + clip_freed + orphan_freed

    downloads_mb, clips_mb, free_mb = get_disk_usage()
    logger.info(f"After:  downloads={downloads_mb:.1f}MB | clips={clips_mb:.1f}MB | free={free_mb/1024:.1f}GB")
    logger.info(f"Total freed this run: {total_freed:.1f}MB")
    logger.info("=" * 55)

    return {
        'source_videos_deleted': src_count,
        'clips_deleted': clip_count,
        'orphans_deleted': orphan_count,
        'total_mb_freed': total_freed,
    }


if __name__ == '__main__':
    run_cleanup()
