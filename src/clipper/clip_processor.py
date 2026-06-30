"""
VAULTCUT — Clip Processor
===========================
Orchestrates the complete clip creation pipeline for one clip record:

  Source MP4
    → Extract timestamp range  (VideoCutter)
    → Crop to vertical 9:16    (VerticalCropper)
    → Encode to Shorts MP4     (moviepy write_videofile)
    → Save to data/clips/{id}.mp4
    → Update database           (ClipStorage)

This module is the only place where moviepy's write_videofile() is called.
All resource cleanup (closing VideoFileClip objects) happens here.

Design note — why we close clips explicitly:
  moviepy VideoFileClip objects hold open file handles and FFmpeg subprocesses.
  If they're not closed, Python's garbage collector eventually cleans them up,
  but on a long-running process like VAULTCUT this can leak memory and file
  handles. We always close in a try/finally block.

Usage:
    from src.clipper.clip_processor import ClipProcessor
    processor = ClipProcessor()
    result = processor.process_clip(clip_record, source_video_path)
    # result = {"success": True, "file_path": "...", "file_size_mb": 12.4}
    # result = {"success": False, "error": "...", "status": "encoding_failed"}
"""

import os
import sys
import time
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from src.logger import get_logger
from src.clipper.video_cutter    import VideoCutter
from src.clipper.vertical_crop   import VerticalCropper
from src.clipper.encoding_config import get_encoding_params, should_use_lower_res
from src.clipper.clip_storage    import ClipStorage, CLIPS_DIR

logger = get_logger("clipper.processor")


class ClipProcessor:
    """
    Handles the end-to-end creation of one YouTube Shorts clip.
    """

    def __init__(self):
        self.cutter  = VideoCutter()
        self.cropper = VerticalCropper()
        self.storage = ClipStorage()

    def process_clip(self, clip_record: dict, source_video_path: str) -> dict:
        """
        Runs the full pipeline: extract → crop → encode → save → update DB.

        Args:
            clip_record:       Row from the clips table as a dict.
                               Must have: clip_id, start_time, end_time,
                               title, content_type, target_channel
            source_video_path: Full path to the source MP4 file

        Returns:
            Dict with keys:
              success (bool)
              file_path (str)   — path to output file (if success)
              file_size_mb (float) — output file size (if success)
              processing_time (float) — seconds taken
              error (str)       — error message (if not success)
              status (str)      — DB status string set on failure
        """
        clip_id    = clip_record.get("clip_id") or clip_record.get("id")
        start      = float(clip_record.get("start_time", 0))
        end        = float(clip_record.get("end_time",   0))
        title      = clip_record.get("title", f"clip_{clip_id}") or f"clip_{clip_id}"

        output_path = self.storage.get_output_path(clip_id)

        logger.info("─" * 55)
        logger.info(f"Processing clip id={clip_id}: '{title[:50]}'")
        logger.info(
            f"Timestamps: {start:.2f}s → {end:.2f}s "
            f"({end-start:.1f}s)"
        )
        logger.info(f"Source:  {os.path.basename(source_video_path)}")
        logger.info(f"Output:  {output_path}")

        process_start = time.time()

        # ── Guard: skip if output already exists ──────────────
        if os.path.exists(output_path):
            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            logger.info(
                f"Output already exists ({size_mb:.1f}MB) — "
                f"skipping re-encode. Marking as ready_to_upload."
            )
            self.storage.update_clip_complete(clip_id, output_path, size_mb)
            return {
                "success":         True,
                "file_path":       output_path,
                "file_size_mb":    size_mb,
                "processing_time": 0.0,
                "skipped":         True,
            }

        # ── Guard: verify source file exists ──────────────────
        if not source_video_path or not os.path.exists(source_video_path):
            error = f"Source file not found: {source_video_path}"
            logger.error(error)
            self.storage.update_clip_failed(clip_id, error, "source_missing")
            return {"success": False, "error": error, "status": "source_missing",
                    "processing_time": 0.0}

        # ── Main pipeline ──────────────────────────────────────
        source_clip   = None
        subclip       = None
        vertical_clip = None

        try:
            # Step 1: Extract timestamp range
            logger.info("Step 1/3: Extracting clip from source...")
            subclip, extract_error = self.cutter.extract(
                source_path=source_video_path,
                start=start,
                end=end,
            )

            if subclip is None:
                # Determine failure status from error message
                if "not found" in extract_error or "disappeared" in extract_error:
                    status = "source_missing"
                elif "timestamp" in extract_error or "duration" in extract_error:
                    status = "invalid_timestamps"
                else:
                    status = "failed"
                self.storage.update_clip_failed(clip_id, extract_error, status)
                return {"success": False, "error": extract_error, "status": status,
                        "processing_time": round(time.time() - process_start, 1)}

            logger.info(
                f"  Extracted: {subclip.duration:.1f}s | "
                f"{subclip.w}×{subclip.h}"
            )

            # Step 2: Crop to vertical 9:16
            logger.info("Step 2/3: Cropping to vertical 9:16 format...")
            vertical_clip = self.cropper.crop(subclip)

            logger.info(
                f"  Cropped: {vertical_clip.w}×{vertical_clip.h}"
            )

            # Step 3: Encode and write to disk
            logger.info("Step 3/3: Encoding final MP4...")
            logger.info(
                f"  This may take 30-120 seconds depending on clip length."
            )

            encoding_params = get_encoding_params(
                use_lower_res=should_use_lower_res()
            )

            # Ensure output directory exists
            os.makedirs(CLIPS_DIR, exist_ok=True)

            encode_start = time.time()

            vertical_clip.write_videofile(
                output_path,
                **encoding_params,
            )

            encode_elapsed = round(time.time() - encode_start, 1)
            logger.info(f"  Encoding complete in {encode_elapsed}s")

            # ── Verify output file ─────────────────────────────
            if not os.path.exists(output_path):
                error = f"write_videofile completed but output not found: {output_path}"
                logger.error(error)
                self.storage.update_clip_failed(clip_id, error, "encoding_failed")
                return {"success": False, "error": error, "status": "encoding_failed",
                        "processing_time": round(time.time() - process_start, 1)}

            file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
            if file_size_mb < 0.1:
                error = f"Output file is too small ({file_size_mb:.3f}MB)"
                logger.error(error)
                try:
                    os.remove(output_path)
                except OSError:
                    pass
                self.storage.update_clip_failed(clip_id, error, "encoding_failed")
                return {"success": False, "error": error, "status": "encoding_failed",
                        "processing_time": round(time.time() - process_start, 1)}

            # ── Update database ────────────────────────────────
            self.storage.update_clip_complete(clip_id, output_path, file_size_mb)

            total_elapsed = round(time.time() - process_start, 1)

            logger.info(f"")
            logger.info(f"✓ CLIP CREATED: '{title[:45]}'")
            logger.info(f"  Output:  {output_path}")
            logger.info(f"  Size:    {file_size_mb:.1f}MB")
            logger.info(f"  Time:    {total_elapsed}s")
            logger.info(f"  Status:  ready_to_upload")

            return {
                "success":         True,
                "file_path":       output_path,
                "file_size_mb":    file_size_mb,
                "processing_time": total_elapsed,
            }

        except Exception as e:
            elapsed = round(time.time() - process_start, 1)
            error   = f"Unexpected error after {elapsed}s: {str(e)[:400]}"
            logger.error(f"Clip id={clip_id} failed: {error}")

            # Try to delete partial output file
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                    logger.info("Removed partial output file.")
                except OSError:
                    pass

            self.storage.update_clip_failed(clip_id, error, "encoding_failed")
            return {"success": False, "error": error, "status": "encoding_failed",
                    "processing_time": elapsed}

        finally:
            # ── Always free memory ─────────────────────────────
            # Close clips in reverse order of creation.
            # This ensures FFmpeg subprocesses and file handles are freed.
            for clip_obj in [vertical_clip, subclip, source_clip]:
                if clip_obj is not None:
                    try:
                        clip_obj.close()
                    except Exception:
                        pass  # Don't crash cleanup over a close() error
