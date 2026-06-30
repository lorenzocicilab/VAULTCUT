"""
VAULTCUT — Storage Manager
============================
Monitors disk space and keeps the downloads folder from growing too large.

Responsibilities:
  - Check free disk space before every download
  - Estimate how much space a video will need
  - Auto-delete old completed files when folder exceeds 50GB
  - Log storage statistics

This module is called by queue_runner.py before each download.
It never touches files that are still 'queued' or 'downloading'.

Usage:
    from src.downloader.storage_manager import StorageManager
    sm = StorageManager()
    if sm.is_space_available():
        proceed_with_download()
"""

import os
import sys
import shutil
import glob
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from src.logger import get_logger
from src.database.init_db import get_connection

logger = get_logger("downloader.storage")

# ── Configuration constants ────────────────────────────────────────────────────
# Stop downloading if free space drops below this
MIN_FREE_SPACE_GB = 5.0

# If the downloads folder exceeds this, trigger cleanup
MAX_DOWNLOADS_FOLDER_GB = 50.0

# Keep files from the last N days; delete anything older
KEEP_FILES_DAYS = 7

# Rough estimate: 1 minute of 1080p video ≈ 150MB
# Used to estimate whether we have enough space before starting
MB_PER_MINUTE_1080P = 150.0


class StorageManager:
    """
    Manages disk space for the VAULTCUT downloads folder.
    All size calculations are in bytes internally; GB for display.
    """

    def __init__(self):
        self.downloads_dir = os.path.join(PROJECT_ROOT, "data", "downloads")
        self.clips_dir     = os.path.join(PROJECT_ROOT, "data", "clips")
        self.processed_dir = os.path.join(PROJECT_ROOT, "data", "processed")

        # Make sure these folders exist
        for d in [self.downloads_dir, self.clips_dir, self.processed_dir]:
            os.makedirs(d, exist_ok=True)

    # ── Disk space checks ────────────────────────────────────────────────────

    def get_free_space_gb(self) -> float:
        """
        Returns the free disk space on the drive where downloads live.
        Uses shutil.disk_usage which works on Windows and Linux.

        Returns:
            Free space in gigabytes, rounded to 2 decimal places.
        """
        try:
            usage = shutil.disk_usage(self.downloads_dir)
            return round(usage.free / (1024 ** 3), 2)
        except Exception as e:
            logger.error(f"Could not check disk space: {e}")
            return 0.0

    def get_total_space_gb(self) -> float:
        """Returns total disk capacity in GB."""
        try:
            usage = shutil.disk_usage(self.downloads_dir)
            return round(usage.total / (1024 ** 3), 2)
        except Exception:
            return 0.0

    def is_space_available(self, required_gb: float = 0.0) -> bool:
        """
        Returns True if enough disk space is available to proceed.

        Args:
            required_gb: Extra space needed on top of the MIN_FREE_SPACE_GB buffer.
                         Pass 0 to just check the minimum threshold.

        Returns:
            True if safe to download, False if we should stop.
        """
        free = self.get_free_space_gb()
        needed = MIN_FREE_SPACE_GB + required_gb

        if free < needed:
            logger.warning(
                f"LOW DISK SPACE: {free:.2f}GB free, need {needed:.2f}GB "
                f"(minimum {MIN_FREE_SPACE_GB}GB safety buffer). "
                f"Download halted."
            )
            return False

        return True

    def estimate_download_size_gb(self, duration_seconds: int) -> float:
        """
        Estimates how much disk space a video will need.

        We use a conservative estimate of 150MB per minute at 1080p.
        The actual file will often be smaller, but this keeps us safe.

        Args:
            duration_seconds: Video duration in seconds

        Returns:
            Estimated file size in GB
        """
        if duration_seconds <= 0:
            return 0.5  # Default 500MB estimate if duration unknown

        minutes = duration_seconds / 60.0
        estimated_mb = minutes * MB_PER_MINUTE_1080P
        return round(estimated_mb / 1024, 3)

    # ── Folder size ──────────────────────────────────────────────────────────

    def get_folder_size_gb(self, folder_path: str) -> float:
        """
        Calculates the total size of all files in a folder.

        Args:
            folder_path: Full path to the folder

        Returns:
            Total size in GB
        """
        total_bytes = 0
        try:
            for dirpath, dirnames, filenames in os.walk(folder_path):
                for filename in filenames:
                    filepath = os.path.join(dirpath, filename)
                    try:
                        total_bytes += os.path.getsize(filepath)
                    except OSError:
                        pass  # File might have been deleted
        except Exception as e:
            logger.error(f"Could not calculate folder size for {folder_path}: {e}")

        return round(total_bytes / (1024 ** 3), 3)

    def get_downloads_size_gb(self) -> float:
        """Returns total size of the downloads/ folder in GB."""
        return self.get_folder_size_gb(self.downloads_dir)

    # ── Cleanup ──────────────────────────────────────────────────────────────

    def cleanup_old_downloads(self, days_to_keep: int = KEEP_FILES_DAYS, dry_run: bool = False) -> int:
        """
        Deletes video files from data/downloads/ that:
          1. Are older than `days_to_keep` days
          2. Have download_status = 'completed' in the database
             (we never delete queued or failed videos automatically)

        Updates the database to set deleted=1 for removed files.

        Args:
            days_to_keep: Files older than this many days will be deleted
            dry_run: If True, only log what would be deleted without deleting

        Returns:
            Number of files deleted (or that would be deleted in dry_run mode)
        """
        cutoff_date = datetime.now() - timedelta(days=days_to_keep)
        cutoff_iso  = cutoff_date.isoformat()

        conn = get_connection()
        deleted_count = 0
        freed_bytes   = 0

        try:
            # Find completed videos older than the cutoff
            rows = conn.execute("""
                SELECT id, file_path, title
                FROM downloaded_videos
                WHERE download_status = 'completed'
                  AND deleted = 0
                  AND download_date < ?
                  AND file_path IS NOT NULL
                  AND file_path != ''
            """, (cutoff_iso,)).fetchall()

            logger.info(f"Storage cleanup: found {len(rows)} old completed files to evaluate")

            for row in rows:
                file_path = row["file_path"]
                title     = row["title"] or "unknown"

                if not file_path or not os.path.exists(file_path):
                    # File already gone — just mark it in DB
                    if not dry_run:
                        conn.execute(
                            "UPDATE downloaded_videos SET deleted=1 WHERE id=?",
                            (row["id"],)
                        )
                    deleted_count += 1
                    continue

                file_size = os.path.getsize(file_path)

                if dry_run:
                    logger.info(f"  [DRY RUN] Would delete: {title[:50]} ({file_size/1024/1024:.1f}MB)")
                    deleted_count += 1
                    freed_bytes   += file_size
                    continue

                try:
                    os.remove(file_path)
                    conn.execute(
                        "UPDATE downloaded_videos SET deleted=1 WHERE id=?",
                        (row["id"],)
                    )
                    freed_bytes   += file_size
                    deleted_count += 1
                    logger.info(f"  Deleted: {title[:50]} ({file_size/1024/1024:.1f}MB)")
                except OSError as e:
                    logger.error(f"  Could not delete {file_path}: {e}")

            if not dry_run:
                conn.commit()

        except Exception as e:
            logger.error(f"Cleanup error: {e}")
        finally:
            conn.close()

        freed_gb = round(freed_bytes / (1024 ** 3), 3)
        action   = "Would free" if dry_run else "Freed"
        logger.info(f"Cleanup complete: {deleted_count} files. {action} {freed_gb}GB")
        return deleted_count

    def auto_cleanup_if_needed(self) -> bool:
        """
        Checks if the downloads folder is over the size limit and
        runs cleanup automatically if it is.

        Returns:
            True if cleanup was triggered, False if not needed.
        """
        current_size = self.get_downloads_size_gb()

        if current_size > MAX_DOWNLOADS_FOLDER_GB:
            logger.warning(
                f"Downloads folder is {current_size:.1f}GB "
                f"(limit: {MAX_DOWNLOADS_FOLDER_GB}GB). Running cleanup..."
            )
            self.cleanup_old_downloads()
            return True

        return False

    # ── Stats report ─────────────────────────────────────────────────────────

    def log_storage_stats(self):
        """Logs a human-readable storage summary. Called before each download batch."""
        free_gb      = self.get_free_space_gb()
        total_gb     = self.get_total_space_gb()
        downloads_gb = self.get_downloads_size_gb()
        used_pct     = round((1 - free_gb / total_gb) * 100, 1) if total_gb > 0 else 0

        logger.info(f"Storage: {free_gb:.1f}GB free / {total_gb:.0f}GB total ({used_pct}% used)")
        logger.info(f"Downloads folder: {downloads_gb:.2f}GB")

        if free_gb < MIN_FREE_SPACE_GB * 2:
            logger.warning(f"Disk space is getting low! Only {free_gb:.1f}GB free.")

    def get_stats_dict(self) -> dict:
        """Returns storage stats as a dict for programmatic use."""
        return {
            "free_gb":         self.get_free_space_gb(),
            "total_gb":        self.get_total_space_gb(),
            "downloads_gb":    self.get_downloads_size_gb(),
            "space_available": self.is_space_available(),
        }


# ============================================================
# Self-test
# PowerShell: python src\downloader\storage_manager.py
# ============================================================
if __name__ == "__main__":
    sm = StorageManager()
    print("VAULTCUT Storage Manager Test")
    print("=" * 45)
    stats = sm.get_stats_dict()
    print(f"Free space:      {stats['free_gb']:.2f} GB")
    print(f"Total space:     {stats['total_gb']:.0f} GB")
    print(f"Downloads size:  {stats['downloads_gb']:.3f} GB")
    print(f"Safe to download: {'YES' if stats['space_available'] else 'NO — too little space'}")
    print()

    # Test estimation
    for mins in [5, 15, 30, 60]:
        est = sm.estimate_download_size_gb(mins * 60)
        print(f"  Estimated size for {mins:3d}-min video: {est:.2f} GB")
    print()

    # Dry run cleanup
    print("Dry-run cleanup (shows what would be deleted):")
    sm.cleanup_old_downloads(dry_run=True)
