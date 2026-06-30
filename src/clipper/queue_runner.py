"""
VAULTCUT — Clip Queue Runner
================================
Processes all pending clips one at a time.

Called every 15 minutes by APScheduler in main.py.
Can also be triggered manually:
    python manage_channels.py cut now

For each clip in the queue:
  1. Look up source video path from downloaded_videos
  2. Mark clip as 'cutting'
  3. Call ClipProcessor.process_clip()
  4. Log result
  5. Move to next clip

Lock file prevents two clip jobs running simultaneously.
Clip creation is CPU-intensive (FFmpeg encoding), so we
absolutely never run two at once.
"""

import os
import sys
import time
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from src.logger import get_logger
from src.clipper.clip_storage   import ClipStorage
from src.clipper.clip_processor import ClipProcessor

logger = get_logger("clipper.queue")

LOCK_FILE    = os.path.join(PROJECT_ROOT, "data", "clips", ".clip_lock")
MAX_PER_RUN  = 5   # Max clips to process per scheduler run


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
        if age > 10800:   # 3 hours — stale
            logger.warning("Stale clip lock (3h+ old). Removing.")
            _remove_lock()
            return False
    except OSError:
        return False
    return True


# ── Main entry points ─────────────────────────────────────────────────────────

def process_clip_queue(max_clips: int = MAX_PER_RUN) -> dict:
    """
    Processes up to max_clips pending clips.
    Called every 15 minutes by APScheduler.

    Args:
        max_clips: Maximum clips to cut this run.

    Returns:
        Dict with processed_count, success_count, failed_count.
    """
    result = {"processed_count": 0, "success_count": 0, "failed_count": 0}

    logger.info("=" * 55)
    logger.info("CLIP QUEUE: Starting run")
    logger.info("=" * 55)

    if _is_locked():
        logger.info("Clip processing already running (lock exists). Skipping.")
        return result

    storage = ClipStorage()
    pending = storage.get_pending_clips(limit=max_clips)
    stats   = storage.get_stats()

    if not pending:
        ready = stats.get("ready_to_upload", 0)
        logger.info(f"No clips pending cutting.")
        logger.info(f"  Clips ready to upload: {ready}")
        logger.info(f"  Full stats: {stats}")
        return result

    logger.info(
        f"Clips to process: {len(pending)} | "
        f"Already ready: {stats.get('ready_to_upload', 0)} | "
        f"Failed: {stats.get('failed', 0)}"
    )

    _create_lock()
    processor = ClipProcessor()

    try:
        for i, clip_record in enumerate(pending):
            clip_id    = clip_record["clip_id"]
            title      = clip_record.get("title", "") or f"clip_{clip_id}"
            source_path = clip_record.get("source_file_path", "")

            logger.info(f"\nClip {i+1}/{len(pending)}: id={clip_id}")
            logger.info(f"  Title:  {title[:55]}")
            logger.info(f"  Source: {os.path.basename(source_path) if source_path else 'UNKNOWN'}")

            if not source_path:
                logger.error(f"  No source file path for clip id={clip_id}")
                storage.update_clip_failed(clip_id, "no source file path", "source_missing")
                result["processed_count"] += 1
                result["failed_count"]    += 1
                continue

            storage.mark_in_progress(clip_id)

            clip_result = processor.process_clip(clip_record, source_path)

            result["processed_count"] += 1
            if clip_result["success"]:
                result["success_count"] += 1
                logger.info(
                    f"  ✓ SUCCESS | "
                    f"{clip_result['file_size_mb']:.1f}MB | "
                    f"{clip_result['processing_time']:.0f}s"
                )
            else:
                result["failed_count"] += 1
                logger.warning(
                    f"  ✗ FAILED: {clip_result.get('error', 'unknown')[:80]}"
                )

    except Exception as e:
        logger.error(f"Unexpected error in clip queue: {e}")
    finally:
        _remove_lock()

    final_stats = storage.get_stats()
    logger.info("")
    logger.info("=" * 55)
    logger.info(
        f"CLIP QUEUE COMPLETE: "
        f"{result['success_count']} cut, {result['failed_count']} failed"
    )
    logger.info(f"Total ready to upload: {final_stats.get('ready_to_upload', 0)}")
    logger.info("=" * 55)

    return result


def cut_one_now() -> dict:
    """
    Cuts exactly ONE pending clip immediately.
    Used by: python manage_channels.py cut now
    Ignores the lock file (manual override).

    Returns:
        Result dict from process_clip_queue with max_clips=1
    """
    logger.info("MANUAL CUT: processing next pending clip")
    storage = ClipStorage()
    pending = storage.get_pending_clips(limit=1)

    if not pending:
        print("\n  No clips pending cutting.")
        print("  Make sure you have analyzed videos first:")
        print("    python manage_channels.py analyze now")
        return {"processed_count": 0, "success_count": 0, "failed_count": 0}

    clip   = pending[0]
    title  = clip.get("title", "") or f"clip_{clip['clip_id']}"
    source = clip.get("source_file_path", "")

    print(f"\n  Cutting clip: {title[:60]}")
    print(f"  Timestamps: {clip['start_time']:.1f}s → {clip['end_time']:.1f}s ({clip['duration']:.0f}s)")
    print(f"  Source: {os.path.basename(source) if source else 'UNKNOWN'}")
    print(f"  Output: data/clips/{clip['clip_id']}.mp4")
    print(f"\n  Processing... (this takes 30-120 seconds)")
    print()

    storage.mark_in_progress(clip["clip_id"])
    processor = ClipProcessor()
    result    = processor.process_clip(clip, source)

    return {
        "processed_count": 1,
        "success_count":   1 if result["success"] else 0,
        "failed_count":    0 if result["success"] else 1,
        "result":          result,
    }


# ============================================================
# Self-test
# PowerShell: python src\clipper\queue_runner.py
# ============================================================
if __name__ == "__main__":
    print("VAULTCUT Clip Queue Runner — Status")
    print("=" * 50)

    storage = ClipStorage()
    stats   = storage.get_stats()

    print("\nClip status breakdown:")
    if stats:
        for status, count in stats.items():
            print(f"  {status:<25} {count}")
    else:
        print("  No clips in database yet.")

    pending = storage.get_pending_clips(limit=5)
    if pending:
        print(f"\nNext {len(pending)} clip(s) pending cutting:")
        for c in pending:
            print(
                f"  [clip_id={c['clip_id']:3d}] "
                f"{c['start_time']:.1f}s→{c['end_time']:.1f}s | "
                f"score={c['virality_score']} | "
                f"{(c.get('title') or '')[:45]}"
            )

    ready = storage.get_ready_clips(limit=5)
    if ready:
        print(f"\nClips ready to upload ({len(ready)} shown):")
        for c in ready:
            print(
                f"  [clip_id={c['id']:3d}] "
                f"score={c['virality_score']} | "
                f"{c.get('target_channel', '?'):25s} | "
                f"{(c.get('title') or '')[:40]}"
            )

    print()
    print(f"Lock file present: {os.path.exists(LOCK_FILE)}")
    print()
    print("To cut the next clip now:")
    print("  python manage_channels.py cut now")
