"""
VAULTCUT — Transcript Reader
==============================
Reads saved transcript JSON files and prepares them for Mistral analysis.

Main job: split the transcript into chunks of max 30 segments each.
Why chunk? Because Mistral has a context window limit. A long video
with 200 segments would overflow that limit or produce unreliable output.
Chunking to 30 segments per call keeps each request small and reliable.

Each chunk is formatted as a human-readable string like:
    0.00→4.80: Hey what is up guys welcome back
    4.80→9.20: Today we are going to do something crazy
    ...

This format is much more compact than full JSON, which matters for fitting
more content into Mistral's context window per API call.

Usage:
    from src.analyzer.transcript_reader import TranscriptReader
    reader = TranscriptReader()
    transcript = reader.load("VIJLIo5yT1I")
    chunks = reader.split_into_chunks(transcript, chunk_size=30)
    # chunks is a list of formatted strings, ready to send to Mistral
"""

import os
import sys
import json
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from src.logger import get_logger
from src.database.init_db import get_connection

logger = get_logger("analyzer.reader")

TRANSCRIPTS_DIR = os.path.join(PROJECT_ROOT, "data", "transcripts")

# Maximum segments per Mistral API call
# 30 segments ≈ ~2-3 minutes of video = comfortable for Mistral's context
CHUNK_SIZE = 30


class TranscriptReader:
    """
    Reads and prepares transcript data for Mistral analysis.
    """

    def load_by_video_id(self, video_id: str) -> Optional[dict]:
        """
        Loads a transcript JSON by video_id string.

        Args:
            video_id: YouTube/Twitch video ID (e.g. 'VIJLIo5yT1I')

        Returns:
            The full transcript dict, or None if not found.
        """
        safe_id   = "".join(c if c.isalnum() or c in "-_" else "_" for c in video_id)
        json_path = os.path.join(TRANSCRIPTS_DIR, f"{safe_id}.json")

        if not os.path.exists(json_path):
            logger.error(f"Transcript not found: {json_path}")
            return None

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.info(
                f"Loaded transcript: {safe_id}.json | "
                f"{len(data.get('segments', []))} segments | "
                f"duration={data.get('duration_seconds', data.get('duration', 0))}s"
            )
            return data
        except json.JSONDecodeError as e:
            logger.error(f"Transcript JSON is malformed: {json_path}: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to load transcript {json_path}: {e}")
            return None

    def load_by_db_id(self, video_db_id: int) -> Optional[dict]:
        """
        Loads a transcript by looking up the transcript_path in the database.

        Args:
            video_db_id: The downloaded_videos.id value

        Returns:
            Transcript dict or None.
        """
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT transcript_path, video_id FROM downloaded_videos WHERE id=?",
                (video_db_id,)
            ).fetchone()
        finally:
            conn.close()

        if not row:
            logger.error(f"No database row found for id={video_db_id}")
            return None

        # Try path from DB first
        path = row["transcript_path"]
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load from db path {path}: {e}")

        # Fallback: construct path from video_id
        if row["video_id"]:
            return self.load_by_video_id(row["video_id"])

        logger.error(f"Cannot locate transcript for db_id={video_db_id}")
        return None

    def get_duration(self, transcript: dict) -> float:
        """
        Extracts video duration from a transcript dict.
        Handles both 'duration_seconds' (Phase 5 format) and 'duration' (your actual file).

        Returns:
            Duration in seconds as a float.
        """
        duration = transcript.get("duration_seconds") or transcript.get("duration") or 0
        return float(duration)

    def get_title(self, transcript: dict) -> str:
        """Returns the video title from a transcript dict."""
        return transcript.get("title", "Untitled Video")

    def get_segments(self, transcript: dict) -> list:
        """
        Returns the segments list from a transcript dict.
        Validates that each segment has start, end, and text fields.

        Returns:
            List of clean segment dicts [{start, end, text}]
        """
        raw = transcript.get("segments", [])
        clean = []
        for seg in raw:
            try:
                start = float(seg.get("start", 0))
                end   = float(seg.get("end",   0))
                text  = str(seg.get("text",   "")).strip()
                if text and end > start:
                    clean.append({"start": start, "end": end, "text": text})
            except (ValueError, TypeError):
                continue
        return clean

    def format_segments_for_prompt(self, segments: list) -> str:
        """
        Formats a list of segments into the compact text block sent to Mistral.

        Output looks like:
            0.00→4.80: Hey what is up guys welcome back
            4.80→9.20: Today we're going to do something crazy
            9.20→15.60: I cannot believe this actually happened

        This format:
        - Is compact (saves Mistral context window space)
        - Gives Mistral the timestamps it needs to suggest clip boundaries
        - Is human-readable so you can read it in logs if something goes wrong

        Args:
            segments: List of {start, end, text} dicts

        Returns:
            Formatted string block
        """
        lines = []
        for seg in segments:
            start = seg["start"]
            end   = seg["end"]
            text  = seg["text"]
            lines.append(f"{start:.2f}→{end:.2f}: {text}")
        return "\n".join(lines)

    def split_into_chunks(self, transcript: dict, chunk_size: int = CHUNK_SIZE) -> list:
        """
        Splits a transcript into chunks of at most chunk_size segments each.

        Each element in the returned list is a dict with:
            {
                "formatted_text":   "0.00→4.80: text\n4.80→9.20: text\n...",
                "segments":         [{start, end, text}, ...],
                "chunk_index":      0,
                "total_chunks":     3,
                "time_start":       0.0,    # start time of first segment in chunk
                "time_end":         90.0,   # end time of last segment in chunk
            }

        Args:
            transcript: Full transcript dict from load_by_video_id()
            chunk_size: Max segments per chunk (default 30)

        Returns:
            List of chunk dicts. Empty list if transcript has no segments.
        """
        segments = self.get_segments(transcript)

        if not segments:
            logger.warning("Transcript has no segments to chunk")
            return []

        chunks = []
        total  = len(segments)
        n_chunks = (total + chunk_size - 1) // chunk_size  # ceiling division

        for i in range(n_chunks):
            start_idx = i * chunk_size
            end_idx   = min(start_idx + chunk_size, total)
            chunk_segs = segments[start_idx:end_idx]

            chunks.append({
                "formatted_text": self.format_segments_for_prompt(chunk_segs),
                "segments":       chunk_segs,
                "chunk_index":    i,
                "total_chunks":   n_chunks,
                "time_start":     chunk_segs[0]["start"],
                "time_end":       chunk_segs[-1]["end"],
            })

        logger.info(
            f"Split {total} segments into {n_chunks} chunks "
            f"({chunk_size} segments each)"
        )
        return chunks

    def is_long_enough_for_clip(self, transcript: dict) -> bool:
        """
        Returns True if the video is long enough to produce at least one 30-second clip.

        A clip needs at least 30 seconds of source material.
        If the video is shorter than 30 seconds, skip it entirely.

        Args:
            transcript: Full transcript dict

        Returns:
            True if video duration >= 30 seconds, False otherwise.
        """
        duration = self.get_duration(transcript)
        if duration < 30:
            title = self.get_title(transcript)
            logger.warning(
                f"Video '{title}' is only {duration:.0f}s long — "
                f"too short for a 30-second clip. Skipping."
            )
            return False
        return True


# ============================================================
# Self-test
# PowerShell: python src\analyzer\transcript_reader.py
# ============================================================
if __name__ == "__main__":
    import glob

    print("VAULTCUT Transcript Reader — Test")
    print("=" * 45)

    # Find all transcripts
    json_files = glob.glob(os.path.join(TRANSCRIPTS_DIR, "*.json"))
    if not json_files:
        print(f"No transcripts found in {TRANSCRIPTS_DIR}")
        print("Run: python manage_channels.py transcribe now")
        sys.exit(1)

    reader = TranscriptReader()

    for json_path in json_files[:2]:  # Test first 2
        video_id = os.path.splitext(os.path.basename(json_path))[0]
        print(f"\nLoading: {video_id}.json")

        transcript = reader.load_by_video_id(video_id)
        if not transcript:
            print("  FAILED to load")
            continue

        title    = reader.get_title(transcript)
        duration = reader.get_duration(transcript)
        segments = reader.get_segments(transcript)
        long_enough = reader.is_long_enough_for_clip(transcript)

        print(f"  Title:    {title}")
        print(f"  Duration: {duration:.0f}s")
        print(f"  Segments: {len(segments)}")
        print(f"  Long enough for clip: {long_enough}")

        chunks = reader.split_into_chunks(transcript, chunk_size=30)
        print(f"  Chunks:   {len(chunks)}")

        if chunks:
            print(f"\n  First chunk preview ({len(chunks[0]['segments'])} segments):")
            lines = chunks[0]["formatted_text"].split("\n")
            for line in lines[:4]:
                print(f"    {line}")
            if len(lines) > 4:
                print(f"    ... and {len(lines)-4} more lines")

    print("\n✓ TranscriptReader working correctly")
