"""
VAULTCUT - Duplicate Clip Detector
====================================
Prevents cutting the same scene twice.
Checks for overlapping timestamps from the same source video.
"""

import sqlite3
from src.logger import get_system_logger

logger = get_system_logger()
DB_PATH = 'data/vaultcut.db'


def is_duplicate(video_id, start_time, end_time, threshold_seconds=10):
    """
    Returns True if a clip with overlapping timestamps already exists
    for the same source video.

    Args:
        video_id: The source video DB id
        start_time: Clip start in seconds
        end_time: Clip end in seconds
        threshold_seconds: Overlap tolerance in seconds

    Returns:
        (is_dup: bool, existing_clip_id: int or None)
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute("""
            SELECT id, start_time, end_time, status
            FROM clips
            WHERE video_id = ?
            AND status NOT IN ('rejected', 'failed', 'invalid_timestamps')
        """, (video_id,)).fetchall()

        for row in rows:
            existing_id = row[0]
            ex_start = float(row[1] or 0)
            ex_end = float(row[2] or 0)

            # Check overlap with tolerance
            overlap_start = max(start_time, ex_start)
            overlap_end = min(end_time, ex_end)
            overlap = overlap_end - overlap_start

            if overlap > threshold_seconds:
                logger.info(
                    f"Duplicate detected: new [{start_time:.1f}s-{end_time:.1f}s] "
                    f"overlaps existing clip {existing_id} "
                    f"[{ex_start:.1f}s-{ex_end:.1f}s] by {overlap:.1f}s"
                )
                return True, existing_id

        return False, None

    except Exception as e:
        logger.error(f"Duplicate check error: {e}")
        return False, None
    finally:
        conn.close()


def remove_duplicate_clips():
    """
    Scans all pending_clip clips and marks duplicates as rejected.
    Run once to clean up existing duplicates in the DB.

    Returns:
        Number of duplicates found and rejected.
    """
    conn = sqlite3.connect(DB_PATH)
    duplicates = 0

    try:
        rows = conn.execute("""
            SELECT id, video_id, start_time, end_time
            FROM clips
            WHERE status = 'pending_clip'
            ORDER BY id ASC
        """).fetchall()

        seen = []

        for row in rows:
            clip_id = row[0]
            video_id = row[1]
            start = float(row[2] or 0)
            end = float(row[3] or 0)

            is_dup = False
            for s_vid, s_start, s_end, s_id in seen:
                if s_vid != video_id:
                    continue
                overlap = min(end, s_end) - max(start, s_start)
                if overlap > 10:
                    logger.info(
                        f"Removing duplicate clip {clip_id} "
                        f"(overlaps clip {s_id} by {overlap:.1f}s)"
                    )
                    conn.execute(
                        "UPDATE clips SET status='rejected', approval_status='rejected' WHERE id=?",
                        (clip_id,)
                    )
                    duplicates += 1
                    is_dup = True
                    break

            if not is_dup:
                seen.append((video_id, start, end, clip_id))

        conn.commit()
        logger.info(f"Duplicate scan complete: {duplicates} duplicates removed")

    except Exception as e:
        logger.error(f"Remove duplicates error: {e}")
    finally:
        conn.close()

    return duplicates


if __name__ == '__main__':
    print("Scanning for duplicate clips...")
    n = remove_duplicate_clips()
    print(f"Done: {n} duplicates removed")
