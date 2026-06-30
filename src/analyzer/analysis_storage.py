"""
VAULTCUT — Analysis Storage
==============================
Saves validated clip suggestions to the 'clips' database table
and updates the analysis_status in downloaded_videos.

Each clip saved here has status='pending_clip', which tells Phase 7
(Clipper) that it's ready to be physically cut from the source video.

The clips table columns used here:
    video_id              → FK to downloaded_videos.id
    start_time            → clip start in seconds
    end_time              → clip end in seconds
    duration              → end - start in seconds
    virality_score        → Mistral's score (0-10)
    clip_type             → hook/reaction/highlight/funny/shocking
    reason                → Mistral's explanation (used as description)
    title                 → video title (Phase 7 will generate a proper one)
    description           → same as reason for now
    status                → 'pending_clip' (Phase 7 reads this)
    created_date          → ISO datetime
    content_type          → category: gaming/news/sports/entertainment/tech
    target_channel        → which VAULTCUT channel this belongs to

Usage:
    from src.analyzer.analysis_storage import AnalysisStorage
    storage = AnalysisStorage()
    count = storage.save_clips(video_db_id=1, clips=validated_list,
                               title="My Video", category="gaming")
    storage.mark_complete(video_db_id=1)
"""

import os
import sys
from datetime import datetime
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from src.logger import get_logger
from src.database.init_db import get_connection

logger = get_logger("analyzer.storage")

# Maps content category to the VAULTCUT channel key
CATEGORY_TO_CHANNEL = {
    "gaming":        "vaultcut_gaming",
    "news":          "vaultcut_news",
    "sports":        "vaultcut_sports",
    "entertainment": "vaultcut_entertainment",
    "tech":          "vaultcut_tech",
}


class AnalysisStorage:
    """
    Handles database writes for the analysis phase.
    All clip saves and status updates go through this class.
    """

    def save_clips(
        self,
        video_db_id: int,
        clips:       list,
        title:       str,
        category:    str = "entertainment",
    ) -> int:
        """
        Inserts validated clip suggestions into the clips table.

        For each clip, we insert a row with:
          - Timing info (start_time, end_time, duration)
          - Mistral's assessment (virality_score, clip_type, reason)
          - Routing info (target_channel based on category)
          - Status = 'pending_clip' so Phase 7 knows to process this

        Avoids duplicates: if a clip for the same video with the same
        start time already exists, it is skipped (not inserted twice).

        Args:
            video_db_id: The downloaded_videos.id for the source video
            clips:       List of validated clip dicts from ClipValidator
            title:       Video title (used as clip title placeholder)
            category:    Content category (gaming/news/etc.)

        Returns:
            Number of clips actually inserted.
        """
        if not clips:
            logger.info(f"No clips to save for video id={video_db_id}")
            return 0

        target_channel = CATEGORY_TO_CHANNEL.get(category, "vaultcut_entertainment")
        conn = get_connection()
        inserted = 0
        skipped  = 0

        try:
            for clip in clips:
                start    = float(clip["start"])
                end      = float(clip["end"])
                duration = round(end - start, 2)
                score    = float(clip["virality_score"])
                ctype    = clip.get("clip_type", "highlight")
                reason   = clip.get("reason", "")

                # Deduplication: check if this exact clip window already exists
                existing = conn.execute("""
                    SELECT id FROM clips
                    WHERE video_id = ?
                      AND ABS(start_time - ?) < 1.0
                """, (video_db_id, start)).fetchone()

                if existing:
                    skipped += 1
                    logger.debug(
                        f"  Skipped duplicate clip at {start:.1f}s "
                        f"(already saved as id={existing['id']})"
                    )
                    continue

                conn.execute("""
                    INSERT INTO clips (
                        video_id,
                        start_time,         end_time,
                        duration,           virality_score,
                        clip_type,          reason,
                        title,              description,
                        content_type,       target_channel,
                        status,             created_date,
                        approval_status,    upload_status
                    ) VALUES (
                        ?,
                        ?, ?,
                        ?, ?,
                        ?, ?,
                        ?, ?,
                        ?, ?,
                        ?, ?,
                        'pending',          'waiting'
                    )
                """, (
                    video_db_id,
                    start,      end,
                    duration,   score,
                    ctype,      reason,
                    title,      reason,     # description = reason for now
                    category,   target_channel,
                    "pending_clip",
                    datetime.now().isoformat(),
                ))
                inserted += 1

            conn.commit()
            logger.info(
                f"Clips saved: {inserted} new | {skipped} skipped (duplicates) "
                f"| channel={target_channel}"
            )

        except Exception as e:
            logger.error(f"Failed to save clips for video id={video_db_id}: {e}")
        finally:
            conn.close()

        return inserted

    def mark_complete(self, video_db_id: int):
        """
        Updates downloaded_videos to show analysis is done.
        Phase 7 reads analysis_status='complete' to know what to clip next.
        """
        conn = get_connection()
        try:
            conn.execute("""
                UPDATE downloaded_videos
                SET analysis_status = 'complete'
                WHERE id = ?
            """, (video_db_id,))
            conn.commit()
            logger.info(f"Analysis marked complete for video id={video_db_id}")
        except Exception as e:
            logger.error(f"Failed to mark analysis complete for id={video_db_id}: {e}")
        finally:
            conn.close()

    def mark_in_progress(self, video_db_id: int):
        """Sets analysis_status='in_progress' at the start of analysis."""
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE downloaded_videos SET analysis_status='in_progress' WHERE id=?",
                (video_db_id,)
            )
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to mark in_progress for id={video_db_id}: {e}")
        finally:
            conn.close()

    def mark_failed(self, video_db_id: int, reason: str):
        """Marks analysis as failed. Video can be retried later."""
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE downloaded_videos SET analysis_status='failed', error_message=? WHERE id=?",
                (str(reason)[:500], video_db_id)
            )
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to mark analysis failed for id={video_db_id}: {e}")
        finally:
            conn.close()

    def mark_no_clips(self, video_db_id: int):
        """
        Marks analysis as complete even though zero clips were found.
        We use 'complete_no_clips' so we don't re-analyze this video
        every 20 minutes forever.
        """
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE downloaded_videos SET analysis_status='complete_no_clips' WHERE id=?",
                (video_db_id,)
            )
            conn.commit()
            logger.info(f"Video id={video_db_id}: marked as analyzed but no viable clips found")
        except Exception as e:
            logger.error(f"Failed to mark no_clips for id={video_db_id}: {e}")
        finally:
            conn.close()

    def reset_in_progress(self):
        """
        Resets 'in_progress' analysis records back to 'pending' at startup.
        Handles crash recovery — a video stuck mid-analysis gets retried.
        """
        conn = get_connection()
        try:
            result = conn.execute("""
                UPDATE downloaded_videos
                SET analysis_status = 'pending'
                WHERE analysis_status = 'in_progress'
            """)
            conn.commit()
            if result.rowcount > 0:
                logger.warning(
                    f"Reset {result.rowcount} stuck 'in_progress' "
                    f"analysis records to 'pending'."
                )
        except Exception as e:
            logger.error(f"Failed to reset in-progress analysis: {e}")
        finally:
            conn.close()

    def get_stats(self) -> dict:
        """Returns clip counts by status for the terminal summary."""
        conn = get_connection()
        try:
            # Analysis status breakdown
            analysis_rows = conn.execute("""
                SELECT analysis_status, COUNT(*) as count
                FROM downloaded_videos
                WHERE transcription_status = 'complete'
                GROUP BY analysis_status
            """).fetchall()
            analysis = {(r["analysis_status"] or "pending"): r["count"] for r in analysis_rows}

            # Clips table breakdown
            clips_rows = conn.execute("""
                SELECT status, COUNT(*) as count
                FROM clips
                GROUP BY status
            """).fetchall()
            clips = {r["status"]: r["count"] for r in clips_rows}

            # Total clips saved
            total_clips = conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0]

            return {
                "analysis_status": analysis,
                "clip_status":     clips,
                "total_clips":     total_clips,
            }
        except Exception as e:
            logger.error(f"Failed to get analysis stats: {e}")
            return {}
        finally:
            conn.close()

    def get_pending_clip_count(self) -> int:
        """Returns how many clips are waiting to be cut by Phase 7."""
        conn = get_connection()
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM clips WHERE status='pending_clip'"
            ).fetchone()[0]
        except Exception:
            return 0
        finally:
            conn.close()

    def get_recent_clips(self, limit: int = 10) -> list:
        """Returns the most recently saved clips for display."""
        conn = get_connection()
        try:
            rows = conn.execute("""
                SELECT
                    c.id, c.title, c.start_time, c.end_time,
                    c.duration, c.virality_score, c.clip_type,
                    c.target_channel, c.status, c.created_date,
                    dv.source_url
                FROM clips c
                LEFT JOIN downloaded_videos dv ON c.video_id = dv.id
                ORDER BY c.created_date DESC
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Failed to get recent clips: {e}")
            return []
        finally:
            conn.close()
