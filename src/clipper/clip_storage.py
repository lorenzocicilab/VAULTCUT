"""
VAULTCUT â€” Clip Storage
=========================
Handles all database reads and writes for the clipper phase.

Responsibilities:
  - Read pending clips from the clips table
  - Update clip status after processing (success or failure)
  - Look up source video file paths
  - Track processing statistics

Usage:
    from src.clipper.clip_storage import ClipStorage
    storage = ClipStorage()

    # Get clips to process
    pending = storage.get_pending_clips(limit=5)

    # After successful processing
    storage.update_clip_complete(clip_id=1, file_path="data/clips/1.mp4", file_size_mb=12.4)

    # After failure
    storage.update_clip_failed(clip_id=1, error_msg="FFmpeg error", status="encoding_failed")
"""

import os
import sys
from datetime import datetime
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from src.logger import get_logger
from src.database.init_db import get_connection

logger = get_logger("clipper.storage")

# Output folder for processed clips
CLIPS_DIR = os.path.join(PROJECT_ROOT, "data", "clips")


class ClipStorage:
    """
    Manages all database operations for the clipper phase.
    """

    def __init__(self):
        os.makedirs(CLIPS_DIR, exist_ok=True)

    # â”€â”€ Read â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def get_pending_clips(self, limit: int = 5) -> list:
        """
        Returns clips waiting to be cut, ordered by creation date (oldest first).

        We process oldest first so clips from earlier analysis runs
        don't get indefinitely delayed by newer ones.

        Args:
            limit: Maximum clips to return per batch

        Returns:
            List of dicts with all columns from the clips table,
            plus source_video info from downloaded_videos.
        """
        conn = get_connection()
        try:
            rows = conn.execute("""
                SELECT
                    c.id              AS clip_id,
                    c.video_id        AS source_db_id,
                    c.start_time,
                    c.end_time,
                    c.duration,
                    c.virality_score,
                    c.clip_type,
                    c.title,
                    c.description,
                    c.reason,
                    c.content_type,
                    c.target_channel,
                    c.created_date,
                    dv.video_id       AS source_video_id,
                    dv.file_path      AS source_file_path,
                    dv.url AS source_url,
                    dv.uploader,
                    dv.title          AS source_title
                FROM clips c
                JOIN downloaded_videos dv ON c.video_id = dv.id
                WHERE c.status = 'pending_clip'
                  AND dv.file_path IS NOT NULL
                  AND dv.file_path != ''
                ORDER BY c.created_date ASC
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get pending clips: {e}")
            return []
        finally:
            conn.close()

    def get_clip_by_id(self, clip_id: int) -> Optional[dict]:
        """Returns a single clip record by ID, with source video info."""
        conn = get_connection()
        try:
            row = conn.execute("""
                SELECT
                    c.*,
                    dv.video_id    AS source_video_id,
                    dv.file_path   AS source_file_path,
                    dv.url AS source_url,
                    dv.uploader
                FROM clips c
                JOIN downloaded_videos dv ON c.video_id = dv.id
                WHERE c.id = ?
            """, (clip_id,)).fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get clip id={clip_id}: {e}")
            return None
        finally:
            conn.close()

    def get_output_path(self, clip_id: int) -> str:
        """
        Returns the output file path for a clip.
        Format: data/clips/{clip_id}.mp4
        This is deterministic â€” the same clip_id always maps to the same filename.
        """
        return os.path.join(CLIPS_DIR, f"{clip_id}.mp4")

    # â”€â”€ Write â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def update_clip_complete(
        self,
        clip_id:      int,
        file_path:    str,
        file_size_mb: float = 0.0,
    ):
        """
        Marks a clip as successfully created.

        Updates:
          - status      = 'ready_to_upload'  (Phase 8 reads this)
          - file_path   = path to the output MP4
          - created_date = now (if not already set)

        Args:
            clip_id:      clips.id
            file_path:    Full path to the created MP4 file
            file_size_mb: Output file size in MB (for logging)
        """
        conn = get_connection()
        try:
            # Use relative path for portability
            # (so if the VAULTCUT folder moves, paths still work)
            rel_path = os.path.relpath(file_path, PROJECT_ROOT)

            conn.execute("""
                UPDATE clips
                SET
                    status       = 'ready_to_upload',
                    file_path    = ?,
                    created_date = COALESCE(created_date, ?)
                WHERE id = ?
            """, (rel_path, datetime.now().isoformat(), clip_id))
            conn.commit()

            logger.info(
                f"Clip id={clip_id} marked ready_to_upload | "
                f"path={rel_path} | "
                f"size={file_size_mb:.1f}MB"
            )
        except Exception as e:
            logger.error(f"Failed to update clip id={clip_id} complete: {e}")
        finally:
            conn.close()

    def update_clip_failed(
        self,
        clip_id:  int,
        error_msg: str,
        status:   str = "failed",
    ):
        """
        Marks a clip as failed with an error message.

        Valid failure statuses:
          'failed'              â€” generic failure
          'source_missing'      â€” source MP4 not found on disk
          'invalid_timestamps'  â€” start/end outside video duration
          'encoding_failed'     â€” FFmpeg/moviepy write error

        Args:
            clip_id:   clips.id
            error_msg: Error description (stored in description field)
            status:    One of the failure statuses above
        """
        conn = get_connection()
        try:
            conn.execute("""
                UPDATE clips
                SET
                    status      = ?,
                    description = ?
                WHERE id = ?
            """, (status, str(error_msg)[:500], clip_id))
            conn.commit()
            logger.warning(
                f"Clip id={clip_id} marked {status}: {error_msg[:100]}"
            )
        except Exception as e:
            logger.error(f"Failed to update clip id={clip_id} failed: {e}")
        finally:
            conn.close()

    def mark_in_progress(self, clip_id: int):
        """Sets status='cutting' to show this clip is being processed."""
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE clips SET status='cutting' WHERE id=?",
                (clip_id,)
            )
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to mark clip {clip_id} in progress: {e}")
        finally:
            conn.close()

    def reset_stuck_clips(self):
        """
        Resets clips stuck at 'cutting' back to 'pending_clip'.
        Called at startup to handle clips left mid-process after a crash.
        """
        conn = get_connection()
        try:
            result = conn.execute("""
                UPDATE clips SET status='pending_clip'
                WHERE status = 'cutting'
            """)
            conn.commit()
            if result.rowcount > 0:
                logger.warning(
                    f"Reset {result.rowcount} stuck 'cutting' clips to 'pending_clip'."
                )
        except Exception as e:
            logger.error(f"Failed to reset stuck clips: {e}")
        finally:
            conn.close()

    # â”€â”€ Stats â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def get_stats(self) -> dict:
        """Returns clip counts grouped by status."""
        conn = get_connection()
        try:
            rows = conn.execute("""
                SELECT status, COUNT(*) as count
                FROM clips
                GROUP BY status
            """).fetchall()
            return {row["status"]: row["count"] for row in rows}
        except Exception as e:
            logger.error(f"Failed to get clip stats: {e}")
            return {}
        finally:
            conn.close()

    def get_ready_clips(self, limit: int = 10) -> list:
        """Returns clips with status='ready_to_upload' (waiting for Phase 8)."""
        conn = get_connection()
        try:
            rows = conn.execute("""
                SELECT id, title, file_path, virality_score,
                       target_channel, content_type, duration
                FROM clips
                WHERE status = 'ready_to_upload'
                ORDER BY virality_score DESC
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get ready clips: {e}")
            return []
        finally:
            conn.close()
