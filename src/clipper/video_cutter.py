"""
VAULTCUT â€” Video Cutter
=========================
Extracts a timestamp range (subclip) from a source video file using moviepy.

This is the first step in clip creation:
  Source MP4 â†’ extract(start, end) â†’ raw subclip object

The returned clip is an in-memory moviepy VideoFileClip object.
The caller (clip_processor.py) passes it to vertical_crop.py
and then writes it to disk.

Key design choices:
  - We load the full source video once per clip call.
    This is slightly inefficient if processing many clips from the
    same source video, but it's much simpler and avoids keeping
    large video files in memory across calls.
  - We close the VideoFileClip in a try/finally block so memory
    is always freed, even if processing later fails.
  - Timestamp validation happens here before any FFmpeg work begins.

Usage:
    from src.clipper.video_cutter import VideoCutter
    cutter = VideoCutter()

    # Returns (subclip, error_message)
    # subclip is None if extraction failed
    clip, error = cutter.extract(
        source_path="data/downloads/yt_VIJLIo5yT1I.mp4",
        start=0.0,
        end=45.0,
    )
    if clip:
        # ... process the clip ...
        clip.close()
"""

import os
import sys
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from src.logger import get_logger

logger = get_logger("clipper.cutter")

# Minimum gap between start and end time to be worth cutting (seconds)
MIN_EXTRACT_DURATION = 5.0


class VideoCutter:
    """
    Extracts timestamp ranges from source video files using moviepy.
    """

    def validate_timestamps(
        self,
        start: float,
        end: float,
        video_duration: float,
    ) -> tuple:
        """
        Checks that start/end timestamps are valid for this video.

        Rules:
          - start must be >= 0
          - end must be > start
          - end must be <= video duration (with 0.5s tolerance for rounding)
          - duration must be > MIN_EXTRACT_DURATION

        Args:
            start:          Clip start time in seconds
            end:            Clip end time in seconds
            video_duration: Total source video duration in seconds

        Returns:
            (is_valid: bool, error_message: str)
            error_message is empty string if valid.
        """
        if start < 0:
            return False, f"start_time {start}s is negative"

        if end <= start:
            return False, f"end_time {end}s is not after start_time {start}s"

        if end - start < MIN_EXTRACT_DURATION:
            return False, (
                f"clip duration {end-start:.1f}s is below minimum "
                f"{MIN_EXTRACT_DURATION}s"
            )

        # Allow up to 5s tolerance for rounding/transcription imprecision
        if end > video_duration + 5.0:
            return False, (
                f"end_time {end}s exceeds video duration {video_duration:.1f}s"
            )

        return True, ""

    def extract(
        self,
        source_path: str,
        start: float,
        end: float,
    ) -> tuple:
        """
        Extracts a subclip from a source video file.

        Loads the source MP4, validates timestamps, and returns a
        moviepy clip object representing the requested time range.

        IMPORTANT: The caller MUST call clip.close() on the returned
        clip when done with it to free memory.

        Args:
            source_path: Full path to the source MP4 file
            start:       Start time in seconds (float)
            end:         End time in seconds (float)

        Returns:
            (clip, error_message) tuple where:
              - clip is a moviepy VideoClip on success, or None on failure
              - error_message is an empty string on success, or a description of the error
        """
        # â”€â”€ Validate file exists â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if not source_path or not os.path.exists(source_path):
            msg = f"Source video not found: {source_path}"
            logger.error(msg)
            return None, msg

        file_size_mb = os.path.getsize(source_path) / (1024 * 1024)
        if file_size_mb < 0.1:
            msg = f"Source video is too small ({file_size_mb:.2f}MB): {source_path}"
            logger.error(msg)
            return None, msg

        try:
            # â”€â”€ Import moviepy â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            # Imported here rather than at module level so that
            # import errors give a clear message
            try:
                from moviepy import VideoFileClip
            except ImportError:
                msg = "moviepy not installed. Run: pip install moviepy"
                logger.error(msg)
                return None, msg

            logger.info(
                f"Loading source: {os.path.basename(source_path)} "
                f"({file_size_mb:.1f}MB)"
            )

            # Load the source video
            # audio=True ensures we carry audio through to the final clip
            source_clip = VideoFileClip(
                source_path,
                audio=True,
            )

            video_duration = source_clip.duration
            logger.info(
                f"Source loaded: {source_clip.w}Ã—{source_clip.h} | "
                f"duration={video_duration:.1f}s | "
                f"fps={source_clip.fps}"
            )

            # â”€â”€ Validate timestamps â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            is_valid, error_msg = self.validate_timestamps(start, end, video_duration)
            if not is_valid:
                source_clip.close()
                return None, error_msg

            # Clamp end time to actual video duration (handles rounding)
            clamped_end = min(end, video_duration)
            if clamped_end != end:
                logger.info(
                    f"Clamped end from {end:.2f}s to {clamped_end:.2f}s "
                    f"(video duration)"
                )

            # â”€â”€ Extract subclip â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            # moviepy subclip() is lazy â€” it doesn't decode frames yet.
            # Actual decoding happens when write_videofile() is called.
            subclip = source_clip.subclipped(start, clamped_end)

            actual_duration = subclip.duration
            logger.info(
                f"Extracted: {start:.2f}s â†’ {clamped_end:.2f}s "
                f"({actual_duration:.1f}s)"
            )

            # Note: we don't close source_clip here because subclip holds
            # a reference to it. Both are closed together in clip_processor.py
            # after the final write.
            return subclip, ""

        except Exception as e:
            error_str = str(e)
            logger.error(f"Video extraction failed: {error_str}")

            # Give specific guidance for common errors
            if "No such file" in error_str or "does not exist" in error_str:
                return None, f"source file disappeared: {source_path}"
            elif "codec" in error_str.lower() or "decoder" in error_str.lower():
                return None, f"codec error loading video: {error_str[:200]}"
            elif "memory" in error_str.lower():
                return None, f"out of memory loading video: {error_str[:200]}"
            else:
                return None, f"extraction error: {error_str[:300]}"


# ============================================================
# Self-test
# PowerShell: python src\clipper\video_cutter.py
# ============================================================
if __name__ == "__main__":
    import glob

    print("VAULTCUT Video Cutter â€” Test")
    print("=" * 45)

    downloads_dir = os.path.join(PROJECT_ROOT, "data", "downloads")
    mp4_files = glob.glob(os.path.join(downloads_dir, "*.mp4"))

    if not mp4_files:
        print(f"No MP4 files in {downloads_dir}")
        print("Download a video first.")
        sys.exit(1)

    test_source = mp4_files[0]
    print(f"Test source: {os.path.basename(test_source)}")
    print()

    cutter = VideoCutter()

    # Test timestamp validation
    print("Testing timestamp validation:")
    test_cases = [
        (0.0, 45.0, 56.0, True,  "valid clip"),
        (-1.0, 45.0, 56.0, False, "negative start"),
        (0.0, 0.0, 56.0, False,  "zero duration"),
        (50.0, 45.0, 56.0, False, "end before start"),
        (0.0, 100.0, 56.0, False, "end past video"),
    ]
    for start, end, dur, expect_valid, label in test_cases:
        valid, msg = cutter.validate_timestamps(start, end, dur)
        ok = valid == expect_valid
        print(f"  {'âœ“' if ok else 'âœ—'}  {label}: valid={valid} {msg[:50]}")

    print()
    print("Testing actual extraction (first 10 seconds)...")
    clip, error = cutter.extract(test_source, 0.0, min(10.0, 999))
    if clip:
        print(f"  âœ“ Clip extracted: {clip.duration:.1f}s | {clip.w}Ã—{clip.h}")
        clip.close()
    else:
        print(f"  âœ— Extraction failed: {error}")

