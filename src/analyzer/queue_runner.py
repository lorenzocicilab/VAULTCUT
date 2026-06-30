"""
VAULTCUT — Analysis Queue Runner
===================================
Orchestrates the full AI analysis pipeline for each transcribed video:

  1. Find next video: transcription_status='complete', analysis_status=pending
  2. Load transcript JSON
  3. Split into 30-segment chunks
  4. Send each chunk to Mistral (90s timeout per chunk)
  5. Collect all clip suggestions
  6. Validate (duration 30-59s, score >= 7.0, no overlaps)
  7. Save valid clips to the clips table
  8. Update analysis_status = 'complete'

Called every 20 minutes by APScheduler in main.py.
Can also be triggered manually:
    python manage_channels.py analyze now

Lock file prevents two analysis jobs running simultaneously.
Stale locks (> 3 hours old) are auto-removed.

This is the most CPU-intensive phase because Mistral runs locally.
Plan for 1-5 minutes per video depending on transcript length.
"""

import os
import sys
import time
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from src.logger import get_logger
from src.database.init_db import get_connection
from src.analyzer.transcript_reader  import TranscriptReader
from src.analyzer.mistral_analyzer   import MistralAnalyzer
from src.analyzer.clip_validator     import ClipValidator
from src.analyzer.analysis_storage   import AnalysisStorage

logger = get_logger("analyzer.queue")

LOCK_FILE = os.path.join(PROJECT_ROOT, "data", "temp_audio", ".analysis_lock")

# How many videos to analyze per scheduler run
# Mistral analysis takes 1-5 minutes per video on CPU.
# 20-minute scheduler window → 3 videos max is safe.
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
        if age > 10800:  # 3 hours
            logger.warning("Stale analysis lock (3h+ old). Removing.")
            _remove_lock()
            return False
    except OSError:
        return False
    return True


# ── Queue queries ─────────────────────────────────────────────────────────────

def get_pending_videos(limit: int = MAX_PER_RUN) -> list:
    """
    Returns the next N videos ready for AI analysis:
      - transcription_status = 'complete'  (Whisper has finished)
      - analysis_status IS NULL or 'pending' or 'failed' (not yet analyzed)

    Ordered by view_count DESC (most popular first — most likely to have viral moments).

    Args:
        limit: Maximum videos to return

    Returns:
        List of dicts with video metadata.
    """
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT
                id, video_id, title, source_url, source_type,
                file_path, duration_seconds, view_count,
                uploader, source_category, transcript_path
            FROM downloaded_videos
            WHERE transcription_status = 'complete'
              AND (
                  analysis_status IS NULL
                  OR analysis_status = 'pending'
                  OR analysis_status = 'failed'
              )
              AND deleted = 0
            ORDER BY view_count DESC, id ASC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Failed to get pending analysis videos: {e}")
        return []
    finally:
        conn.close()


def get_analysis_stats() -> dict:
    """Returns analysis status counts from the database."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT analysis_status, COUNT(*) as count
            FROM downloaded_videos
            WHERE transcription_status = 'complete'
            GROUP BY analysis_status
        """).fetchall()
        stats = {}
        for row in rows:
            status = row["analysis_status"] or "pending"
            stats[status] = row["count"]
        return stats
    except Exception as e:
        logger.error(f"Failed to get analysis stats: {e}")
        return {}
    finally:
        conn.close()


# ── Per-video pipeline ────────────────────────────────────────────────────────

def analyze_one_video(
    video:    dict,
    reader:   TranscriptReader,
    analyzer: MistralAnalyzer,
    storage:  AnalysisStorage,
) -> bool:
    """
    Runs the full analysis pipeline for one video.

    Steps:
      1. Load transcript
      2. Check video is long enough
      3. Split into chunks
      4. Analyze each chunk with Mistral
      5. Validate all suggestions
      6. Save to clips table
      7. Update analysis_status

    Args:
        video:    Video info dict from get_pending_videos()
        reader:   TranscriptReader (shared)
        analyzer: MistralAnalyzer (shared, Mistral connection reused)
        storage:  AnalysisStorage (shared)

    Returns:
        True on success, False on failure.
    """
    video_db_id = video["id"]
    video_id    = video.get("video_id", "") or str(video_db_id)
    title       = video.get("title", "") or f"video_{video_db_id}"
    category    = video.get("source_category", "entertainment") or "entertainment"
    duration    = float(video.get("duration_seconds", 0) or 0)

    logger.info("─" * 55)
    logger.info(f"Analyzing id={video_db_id}: {title[:50]}")
    logger.info(f"Duration: {int(duration//60)}m{int(duration%60)}s | Category: {category}")

    analysis_start = time.time()

    try:
        # ── Step 1: Mark as in_progress ───────────────────────
        storage.mark_in_progress(video_db_id)

        # ── Step 2: Load transcript ────────────────────────────
        transcript = reader.load_by_db_id(video_db_id)
        if not transcript:
            # Fallback: try by video_id directly
            transcript = reader.load_by_video_id(video_id)

        if not transcript:
            logger.error(f"Cannot load transcript for video id={video_db_id}")
            storage.mark_failed(video_db_id, "transcript not found")
            return False

        # Use duration from transcript if DB value is 0
        if duration == 0:
            duration = reader.get_duration(transcript)

        # ── Step 3: Check minimum length ──────────────────────
        if not reader.is_long_enough_for_clip(transcript):
            storage.mark_no_clips(video_db_id)
            return True  # Not a failure — just nothing to clip

        # ── Step 4: Split into chunks ──────────────────────────
        chunks = reader.split_into_chunks(transcript, chunk_size=30)

        if not chunks:
            logger.warning(f"No segments found in transcript for id={video_db_id}")
            storage.mark_no_clips(video_db_id)
            return True

        # ── Step 5: Analyze all chunks with Mistral ────────────
        logger.info(f"Sending to Mistral: {len(chunks)} chunk(s)...")
        raw_clips = analyzer.analyze_video(
            title=title,
            duration=duration,
            chunks=chunks,
        )

        # ── Step 6: Validate clip suggestions ─────────────────
        validator = ClipValidator(
            video_duration=duration,
            # min_score read from settings automatically
        )
        valid_clips = validator.validate_all(raw_clips)

        # ── Step 7: Save to database ───────────────────────────
        if valid_clips:
            saved_count = storage.save_clips(
                video_db_id=video_db_id,
                clips=valid_clips,
                title=title,
                category=category,
            )
            storage.mark_complete(video_db_id)

            elapsed = round(time.time() - analysis_start, 1)
            logger.info(f"")
            logger.info(f"✓ ANALYSIS COMPLETE: '{title[:45]}'")
            logger.info(f"  Mistral suggested: {len(raw_clips)} clips")
            logger.info(f"  After validation:  {len(valid_clips)} clips")
            logger.info(f"  Saved to database: {saved_count} clips")
            logger.info(f"  Time taken: {elapsed}s")
        else:
            logger.info(
                f"No clips passed validation for '{title[:45]}'. "
                f"Mistral suggested {len(raw_clips)}, all were filtered out."
            )
            storage.mark_no_clips(video_db_id)

        return True

    except Exception as e:
        elapsed = round(time.time() - analysis_start, 1)
        logger.error(f"Unexpected error analyzing video id={video_db_id} after {elapsed}s: {e}")
        storage.mark_failed(video_db_id, str(e)[:400])
        return False


# ── Main entry points ─────────────────────────────────────────────────────────

def run_analysis_queue(max_videos: int = MAX_PER_RUN):
    """
    Main scheduler entry point.
    Called every 20 minutes by APScheduler in main.py.

    Processes up to max_videos transcribed videos through Mistral analysis.
    Uses a lock file to prevent two runs overlapping.

    Args:
        max_videos: Maximum videos to analyze this run.
    """
    logger.info("=" * 55)
    logger.info("ANALYSIS QUEUE: Starting run")
    logger.info("=" * 55)

    if _is_locked():
        logger.info("Analysis already running (lock exists). Skipping this run.")
        return

    pending = get_pending_videos(limit=max_videos)
    stats   = get_analysis_stats()

    if not pending:
        total_clips = AnalysisStorage().get_pending_clip_count()
        logger.info("No videos pending analysis.")
        logger.info(f"Analysis stats: {stats}")
        logger.info(f"Clips ready for Phase 7 (cutting): {total_clips}")
        return

    total_pending = sum(
        v for k, v in stats.items()
        if k in ("pending", "failed", None)
    )
    logger.info(
        f"Analysis queue: {total_pending} pending | "
        f"Processing {len(pending)} this run | "
        f"Complete: {stats.get('complete', 0)}"
    )

    _create_lock()

    # Shared objects — Mistral connection is reused across all videos
    reader   = TranscriptReader()
    analyzer = MistralAnalyzer()
    storage  = AnalysisStorage()

    succeeded = 0
    failed    = 0

    try:
        for i, video in enumerate(pending):
            logger.info(f"\nVideo {i+1}/{len(pending)}")
            ok = analyze_one_video(video, reader, analyzer, storage)
            if ok:
                succeeded += 1
            else:
                failed += 1

    except Exception as e:
        logger.error(f"Unexpected error in analysis queue: {e}")
    finally:
        _remove_lock()

    final_stats  = get_analysis_stats()
    pending_cuts = AnalysisStorage().get_pending_clip_count()

    logger.info("")
    logger.info("=" * 55)
    logger.info(
        f"ANALYSIS RUN COMPLETE: "
        f"{succeeded} succeeded, {failed} failed"
    )
    logger.info(f"Total clips ready to cut (Phase 7): {pending_cuts}")
    logger.info("=" * 55)


def analyze_one_now() -> bool:
    """
    Analyzes exactly ONE video immediately.
    Used by: python manage_channels.py analyze now

    Returns:
        True if analysis succeeded (even if 0 clips found), False on error.
    """
    logger.info("MANUAL ANALYSIS: next pending video")

    pending = get_pending_videos(limit=1)
    if not pending:
        print("\n  No videos pending analysis.")
        print("  Make sure you have completed transcriptions:")
        print("    python manage_channels.py transcribe now")
        return False

    video = pending[0]
    title = video.get("title", "") or "(no title)"
    print(f"\n  Analyzing: {title[:60]}")
    print(f"  Sending transcript to Mistral (local AI)...")
    print(f"  This takes 1-5 minutes depending on video length.\n")

    reader   = TranscriptReader()
    analyzer = MistralAnalyzer()
    storage  = AnalysisStorage()

    return analyze_one_video(video, reader, analyzer, storage)


# ============================================================
# Self-test
# PowerShell: python src\analyzer\queue_runner.py
# ============================================================
if __name__ == "__main__":
    print("VAULTCUT Analysis Queue Runner — Status")
    print("=" * 55)

    stats = get_analysis_stats()
    print("\nAnalysis status (transcribed videos only):")
    if stats:
        for status, count in stats.items():
            print(f"  {status or 'pending':<30} {count}")
    else:
        print("  No transcribed videos yet.")

    storage = AnalysisStorage()
    full_stats = storage.get_stats()

    print(f"\nTotal clips in database: {full_stats.get('total_clips', 0)}")
    by_status = full_stats.get("clip_status", {})
    if by_status:
        for status, count in by_status.items():
            print(f"  {status:<25} {count}")

    pending = get_pending_videos(limit=3)
    if pending:
        print(f"\nNext {len(pending)} video(s) pending analysis:")
        for v in pending:
            dur = int((v.get("duration_seconds") or 0) // 60)
            print(
                f"  [id={v['id']:3d}] {dur:3d}min | "
                f"{(v.get('title') or '(no title)')[:55]}"
            )

    clips = storage.get_recent_clips(limit=5)
    if clips:
        print(f"\nMost recent clips saved:")
        for c in clips:
            dur = round(c.get("duration", 0), 0)
            print(
                f"  [clip_id={c['id']}] score={c['virality_score']} "
                f"| {dur:.0f}s | {c['clip_type']} | {c['title'][:45]}"
            )

    print()
    print("To analyze next video now:")
    print("  python manage_channels.py analyze now")
