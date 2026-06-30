"""
VAULTCUT — Transcription Queue Runner
========================================
Orchestrates the full transcription pipeline for each video:
  1. Find next downloaded video awaiting transcription
  2. Mark it as in_progress
  3. Extract audio (FFmpeg → WAV)
  4. Transcribe audio (Whisper → segments + full text)
  5. Save result (JSON file + update DB)
  6. Delete temp WAV
  7. Log stats

Called every 15 minutes by APScheduler in main.py.
Can also be triggered manually:
    python manage_channels.py transcribe now

Lock file prevents two transcription jobs running at once.
Stale locks are auto-removed after 3 hours.

Usage:
    from src.transcriber.queue_runner import run_transcription_queue
    run_transcription_queue()
"""

import os
import sys
import time
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from src.logger import get_logger
from src.database.init_db import get_connection
from src.transcriber.audio_extractor   import AudioExtractor
from src.transcriber.whisper_transcriber import WhisperTranscriber
from src.transcriber.transcript_storage  import TranscriptStorage

logger = get_logger("transcriber.queue")

LOCK_FILE = os.path.join(PROJECT_ROOT, "data", "temp_audio", ".transcribe_lock")

# How many videos to transcribe per scheduler run
# A 10-minute video takes ~3-4 minutes on base model with our CPU.
# 15-minute scheduler interval → process 3 videos max per run.
MAX_PER_RUN = 3


# ── Lock helpers ──────────────────────────────────────────────────────────────

def _create_lock():
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
    with open(LOCK_FILE, "w") as f:
        f.write(f"pid={os.getpid()}\nstarted={datetime.now().isoformat()}\n")


def _remove_lock():
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except OSError:
        pass


def _is_locked() -> bool:
    if not os.path.exists(LOCK_FILE):
        return False
    try:
        age = time.time() - os.path.getmtime(LOCK_FILE)
        if age > 10800:  # 3 hours — definitely stale
            logger.warning("Stale transcription lock (3h+ old). Removing.")
            _remove_lock()
            return False
    except OSError:
        return False
    return True


# ── Queue helpers ─────────────────────────────────────────────────────────────

def get_pending_videos(limit: int = MAX_PER_RUN) -> list:
    """
    Returns the next N videos that:
      - Have been fully downloaded (download_status = 'completed')
      - Have not yet been transcribed
        (transcription_status IS NULL OR 'pending' OR 'failed')

    Ordered by view_count DESC so the most-watched content gets processed first
    (higher views = more likely to contain viral moments worth clipping).

    Args:
        limit: Maximum rows to return

    Returns:
        List of dicts with video info
    """
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT
                id, video_id, title, source_url, source_type,
                file_path, duration_seconds, view_count,
                uploader, source_category
            FROM downloaded_videos
            WHERE download_status      = 'completed'
              AND (
                  transcription_status IS NULL
                  OR transcription_status = 'pending'
                  OR transcription_status = 'failed'
              )
              AND file_path IS NOT NULL
              AND file_path != ''
              AND deleted = 0
            ORDER BY view_count DESC, id ASC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Failed to get pending videos: {e}")
        return []
    finally:
        conn.close()


def reset_failed_downloads():
    """
    Resets videos stuck at download_status='failed' back to 'queued'
    so the downloader will retry them.

    Called once at startup (from main.py).
    Also useful when a batch of downloads fails due to a temporary error
    (e.g. internet outage, YouTube rate limit).
    """
    conn = get_connection()
    try:
        result = conn.execute("""
            UPDATE downloaded_videos
            SET download_status = 'queued',
                error_message   = NULL
            WHERE download_status = 'failed'
        """)
        conn.commit()
        if result.rowcount > 0:
            logger.info(
                f"Reset {result.rowcount} failed downloads back to 'queued' for retry."
            )
        return result.rowcount
    except Exception as e:
        logger.error(f"Failed to reset failed downloads: {e}")
        return 0
    finally:
        conn.close()


def get_transcription_stats() -> dict:
    """Returns a summary of transcription status across all downloaded videos."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT
                transcription_status,
                COUNT(*) as count
            FROM downloaded_videos
            WHERE download_status = 'completed'
            GROUP BY transcription_status
        """).fetchall()
        stats = {}
        for row in rows:
            status = row["transcription_status"] or "pending"
            stats[status] = row["count"]
        return stats
    except Exception as e:
        logger.error(f"Failed to get transcription stats: {e}")
        return {}
    finally:
        conn.close()


# ── Per-video pipeline ────────────────────────────────────────────────────────

def _mark_file_missing(video_db_id: int):
    """
    If the MP4 file is missing at transcription time,
    reset download_status to 'queued' so it gets re-downloaded.
    """
    conn = get_connection()
    try:
        conn.execute("""
            UPDATE downloaded_videos
            SET download_status      = 'queued',
                transcription_status = 'pending',
                file_path            = NULL
            WHERE id = ?
        """, (video_db_id,))
        conn.commit()
        logger.warning(
            f"MP4 file missing for id={video_db_id} — "
            f"reset to queued for re-download."
        )
    except Exception as e:
        logger.error(f"Failed to reset missing file for id={video_db_id}: {e}")
    finally:
        conn.close()


def transcribe_one_video(
    video: dict,
    extractor: AudioExtractor,
    transcriber: WhisperTranscriber,
    storage: TranscriptStorage,
) -> bool:
    """
    Runs the full transcription pipeline for one video.

    Args:
        video:       Dict with video info from get_pending_videos()
        extractor:   AudioExtractor instance (shared across calls)
        transcriber: WhisperTranscriber instance (model loaded once)
        storage:     TranscriptStorage instance (shared across calls)

    Returns:
        True if transcription succeeded, False if it failed.
    """
    video_db_id  = video["id"]
    video_id     = video.get("video_id", "") or str(video_db_id)
    title        = video.get("title",    "") or f"video_{video_db_id}"
    file_path    = video.get("file_path", "")
    duration     = float(video.get("duration_seconds", 0) or 0)
    view_count   = video.get("view_count", 0)
    source_url   = video.get("source_url", "")
    uploader     = video.get("uploader",  "")

    logger.info("─" * 55)
    logger.info(f"Transcribing id={video_db_id}: {title[:55]}")
    logger.info(f"Duration: {int(duration//60)}m{int(duration%60)}s | Views: {view_count:,}")

    # Estimate how long this will take
    estimated_s = transcriber.estimate_time(duration)
    logger.info(f"Estimated transcription time: ~{int(estimated_s//60)}m{int(estimated_s%60)}s")

    wav_path = None

    try:
        # ── Step 1: Verify MP4 exists ──────────────────────────
        if not file_path or not os.path.exists(file_path):
            logger.error(f"MP4 not found at: {file_path}")
            _mark_file_missing(video_db_id)
            return False

        # ── Step 2: Mark as in_progress ───────────────────────
        storage.mark_in_progress(video_db_id)

        # ── Step 3: Extract audio → WAV ───────────────────────
        logger.info("Step 1/3: Extracting audio...")
        wav_path = extractor.extract(file_path, video_id)

        if not wav_path:
            logger.error("Audio extraction failed")
            storage.mark_failed(video_db_id, "audio extraction failed")
            return False

        # ── Step 4: Transcribe ─────────────────────────────────
        logger.info("Step 2/3: Running Whisper transcription...")
        start_time = time.time()
        result = transcriber.transcribe(wav_path)

        if not result:
            logger.error("Whisper transcription returned no result")
            storage.mark_failed(video_db_id, "whisper transcription failed")
            return False

        # ── Step 5: Save transcript ────────────────────────────
        logger.info("Step 3/3: Saving transcript...")
        saved_path = storage.save(
            video_db_id=video_db_id,
            video_id=video_id,
            title=title,
            duration=duration,
            whisper_result=result,
            source_url=source_url,
            uploader=uploader,
        )

        if not saved_path:
            logger.error("Failed to save transcript")
            storage.mark_failed(video_db_id, "transcript save failed")
            return False

        # ── Log success ────────────────────────────────────────
        total_elapsed = round(time.time() - start_time, 1)
        logger.info(f"")
        logger.info(f"✓ TRANSCRIPTION COMPLETE: '{title[:45]}'")
        logger.info(f"  Segments:  {result['segment_count'] if 'segment_count' in result else len(result['segments'])}")
        logger.info(f"  Words:     {result['word_count']:,}")
        logger.info(f"  Language:  {result['language']}")
        logger.info(f"  Time:      {total_elapsed}s")
        logger.info(f"  Saved to:  {saved_path}")

        return True

    except Exception as e:
        logger.error(f"Unexpected error transcribing id={video_db_id}: {e}")
        storage.mark_failed(video_db_id, str(e)[:400])
        return False

    finally:
        # ── Always delete temp WAV ─────────────────────────────
        if wav_path:
            extractor.cleanup(wav_path)


# ── Main entry points ─────────────────────────────────────────────────────────

def run_transcription_queue(max_videos: int = MAX_PER_RUN):
    """
    Main scheduler entry point.
    Called every 15 minutes by APScheduler in main.py.

    Processes up to max_videos, one at a time.
    Uses a lock file to prevent overlapping runs.

    Args:
        max_videos: Maximum videos to transcribe this run
    """
    logger.info("=" * 55)
    logger.info("TRANSCRIPTION QUEUE: Starting run")
    logger.info("=" * 55)

    if _is_locked():
        logger.info("Transcription already running (lock exists). Skipping this run.")
        return

    pending = get_pending_videos(limit=max_videos)
    stats   = get_transcription_stats()

    if not pending:
        logger.info("No videos pending transcription.")
        logger.info(f"Transcription stats: {stats}")
        return

    total_pending = sum(
        v for k, v in stats.items()
        if k in ("pending", "failed", None)
    )
    logger.info(
        f"Transcription queue: {total_pending} pending | "
        f"Processing {len(pending)} this run | "
        f"Complete: {stats.get('complete', 0)}"
    )

    _create_lock()

    # Create shared objects — model loads ONCE here
    extractor   = AudioExtractor()
    transcriber = WhisperTranscriber()  # Model loads on first .transcribe() call
    storage     = TranscriptStorage()

    succeeded = 0
    failed    = 0

    try:
        for i, video in enumerate(pending):
            logger.info(f"\nVideo {i+1}/{len(pending)}")
            ok = transcribe_one_video(video, extractor, transcriber, storage)
            if ok:
                succeeded += 1
            else:
                failed += 1

    except Exception as e:
        logger.error(f"Unexpected error in transcription queue runner: {e}")
    finally:
        _remove_lock()

    final_stats = get_transcription_stats()
    logger.info("")
    logger.info("=" * 55)
    logger.info(
        f"TRANSCRIPTION RUN COMPLETE: "
        f"{succeeded} succeeded, {failed} failed"
    )
    logger.info(f"Total complete: {final_stats.get('complete', 0)}")
    logger.info("=" * 55)


def transcribe_one_now() -> bool:
    """
    Transcribes exactly ONE video immediately.
    Used by: python manage_channels.py transcribe now

    Ignores the lock file (manual override).

    Returns:
        True if transcription succeeded, False otherwise.
    """
    logger.info("MANUAL TRANSCRIPTION: next pending video")

    pending = get_pending_videos(limit=1)
    if not pending:
        print("\n  No videos pending transcription.")
        print("  Make sure you have completed downloads first:")
        print("    python manage_channels.py download now")
        return False

    video = pending[0]
    title = video.get("title", "") or "(no title)"
    print(f"\n  Transcribing: {title[:60]}")
    print(f"  Duration: {int((video.get('duration_seconds') or 0)//60)} minutes")
    print(f"  This may take 2-5 minutes. Watch the log output below.\n")

    extractor   = AudioExtractor()
    transcriber = WhisperTranscriber()
    storage     = TranscriptStorage()

    return transcribe_one_video(video, extractor, transcriber, storage)


# ============================================================
# Self-test
# PowerShell: python src\transcriber\queue_runner.py
# ============================================================
if __name__ == "__main__":
    print("VAULTCUT Transcription Queue Runner — Status")
    print("=" * 50)

    stats = get_transcription_stats()
    print("\nTranscription status (completed downloads only):")
    if stats:
        for status, count in stats.items():
            print(f"  {status or 'pending':<20} {count}")
    else:
        print("  No completed downloads yet.")

    pending = get_pending_videos(limit=5)
    if pending:
        print(f"\nNext {len(pending)} videos to transcribe:")
        for v in pending:
            dur = int((v.get("duration_seconds") or 0) // 60)
            views = v.get("view_count", 0)
            print(
                f"  [id={v['id']:3d}] {dur:3d}min | {views:>8,} views | "
                f"{(v.get('title') or '(no title)')[:50]}"
            )
    else:
        print("\nNo videos pending transcription.")

    print()
    print("To transcribe the next video now:")
    print("  python manage_channels.py transcribe now")
