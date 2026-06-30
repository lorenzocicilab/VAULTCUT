"""
VAULTCUT — Transcript Storage
================================
Saves Whisper transcription results to JSON files and updates
the downloaded_videos database table.

JSON file format (what Phase 6 reads):
    data/transcripts/{video_id}.json

This exact structure is what Phase 6 (Mistral analyzer) and
Phase 7 (Clipper) expect. Do not change the field names.

One JSON file per video. If re-transcribed, the file is overwritten.

Usage:
    from src.transcriber.transcript_storage import TranscriptStorage
    ts = TranscriptStorage()
    path = ts.save(video_db_id=1, video_id="abc123",
                   title="My Video", duration=342.0,
                   whisper_result=result_dict)
"""

import os
import sys
import json
from datetime import datetime
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from src.logger import get_logger
from src.database.init_db import get_connection

logger = get_logger("transcriber.storage")

TRANSCRIPTS_DIR = os.path.join(PROJECT_ROOT, "data", "transcripts")


class TranscriptStorage:
    """
    Handles saving transcripts to disk and updating the database.
    """

    def __init__(self):
        os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)

    def _get_transcript_path(self, video_id: str) -> str:
        """Returns the expected path for a video's transcript JSON file."""
        safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in video_id)
        return os.path.join(TRANSCRIPTS_DIR, f"{safe_id}.json")

    def save(
        self,
        video_db_id:    int,
        video_id:       str,
        title:          str,
        duration:       float,
        whisper_result: dict,
        source_url:     str = "",
        uploader:       str = "",
    ) -> Optional[str]:
        """
        Saves the Whisper result to a JSON file and updates the database.

        Args:
            video_db_id:    Row ID in downloaded_videos table
            video_id:       YouTube/Twitch video ID string
            title:          Video title (clean version)
            duration:       Video duration in seconds
            whisper_result: The dict returned by WhisperTranscriber.transcribe()
            source_url:     Original video URL (for reference)
            uploader:       Channel/uploader name

        Returns:
            Full path to the saved JSON file on success, or None on failure.
        """
        if not whisper_result:
            logger.error(f"Cannot save empty transcript for video_id={video_id}")
            return None

        transcript_path = self._get_transcript_path(video_id)

        # ── Build the JSON document ────────────────────────────
        # This is the exact format Phase 6 expects.
        document = {
            "video_id":             video_id,
            "video_db_id":          video_db_id,
            "title":                title,
            "uploader":             uploader,
            "source_url":           source_url,
            "duration_seconds":     duration,
            "language":             whisper_result.get("language", "unknown"),
            "transcribed_at":       datetime.now().isoformat(),
            "whisper_model":        self._get_model_size(),
            "full_text":            whisper_result.get("full_text", ""),
            "word_count":           whisper_result.get("word_count", 0),
            "segment_count":        len(whisper_result.get("segments", [])),
            "transcription_time_seconds": whisper_result.get("transcription_time_seconds", 0),
            "segments":             whisper_result.get("segments", []),
            # Phase 6 will add these fields after Mistral analysis:
            "mistral_analyzed":     False,
            "clip_candidates":      [],
            "content_type":         "",
            "virality_score":       0.0,
        }

        # ── Save to disk ───────────────────────────────────────
        try:
            with open(transcript_path, "w", encoding="utf-8") as f:
                json.dump(document, f, indent=2, ensure_ascii=False)

            size_kb = os.path.getsize(transcript_path) / 1024
            logger.info(f"Transcript saved: {os.path.basename(transcript_path)} ({size_kb:.1f}KB)")

        except OSError as e:
            logger.error(f"Failed to write transcript JSON to {transcript_path}: {e}")
            return None

        # ── Update the database ────────────────────────────────
        conn = get_connection()
        try:
            conn.execute("""
                UPDATE downloaded_videos
                SET transcription_status = 'complete',
                    transcript_path      = ?
                WHERE id = ?
            """, (transcript_path, video_db_id))
            conn.commit()
            logger.info(f"Database updated: transcription_status=complete for id={video_db_id}")
        except Exception as e:
            logger.error(f"Failed to update database for video_db_id={video_db_id}: {e}")
            # File was saved, so return the path even if DB update failed
        finally:
            conn.close()

        return transcript_path

    def _get_model_size(self) -> str:
        """Reads the whisper model name from settings.json."""
        import json as _json
        settings_path = os.path.join(PROJECT_ROOT, "config", "settings.json")
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                return _json.load(f).get("whisper_model", "base")
        except Exception:
            return "base"

    def load(self, video_id: str) -> Optional[dict]:
        """
        Loads a saved transcript JSON for a given video_id.
        Returns None if the transcript doesn't exist yet.

        Args:
            video_id: YouTube/Twitch video ID

        Returns:
            The full transcript dict, or None if not found.
        """
        path = self._get_transcript_path(video_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load transcript {path}: {e}")
            return None

    def load_by_db_id(self, video_db_id: int) -> Optional[dict]:
        """
        Loads a transcript by looking up the path in the database.
        Useful when you only have the database ID, not the video_id string.
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
            return None

        path = row["transcript_path"]
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load transcript from {path}: {e}")

        # Fallback: try by video_id
        if row["video_id"]:
            return self.load(row["video_id"])

        return None

    def mark_failed(self, video_db_id: int, error_message: str):
        """
        Marks a video's transcription as failed in the database.
        Keeps the download file — only transcription status changes.
        """
        conn = get_connection()
        try:
            conn.execute("""
                UPDATE downloaded_videos
                SET transcription_status = 'failed',
                    error_message        = ?
                WHERE id = ?
            """, (str(error_message)[:500], video_db_id))
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to mark transcription failed for id={video_db_id}: {e}")
        finally:
            conn.close()

    def mark_in_progress(self, video_db_id: int):
        """Sets transcription_status='in_progress' at the start of transcription."""
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE downloaded_videos SET transcription_status='in_progress' WHERE id=?",
                (video_db_id,)
            )
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to mark in_progress for id={video_db_id}: {e}")
        finally:
            conn.close()

    def get_stats(self) -> dict:
        """Returns transcription status counts from the database."""
        conn = get_connection()
        try:
            rows = conn.execute("""
                SELECT transcription_status, COUNT(*) as count
                FROM downloaded_videos
                WHERE download_status = 'completed'
                GROUP BY transcription_status
            """).fetchall()
            return {(row["transcription_status"] or "pending"): row["count"] for row in rows}
        except Exception as e:
            logger.error(f"Failed to get transcription stats: {e}")
            return {}
        finally:
            conn.close()

    def reset_in_progress(self):
        """
        Resets any stuck 'in_progress' transcriptions back to 'pending'.
        Called at startup to fix records left over from crashes.
        """
        conn = get_connection()
        try:
            result = conn.execute("""
                UPDATE downloaded_videos
                SET transcription_status = 'pending'
                WHERE transcription_status = 'in_progress'
            """)
            conn.commit()
            if result.rowcount > 0:
                logger.warning(
                    f"Reset {result.rowcount} stuck 'in_progress' "
                    f"transcriptions to 'pending'."
                )
        except Exception as e:
            logger.error(f"Failed to reset in-progress transcriptions: {e}")
        finally:
            conn.close()

    def list_completed(self, limit: int = 20) -> list:
        """Returns a list of videos with completed transcriptions."""
        conn = get_connection()
        try:
            rows = conn.execute("""
                SELECT id, video_id, title, transcript_path, download_date
                FROM downloaded_videos
                WHERE transcription_status = 'complete'
                  AND download_status = 'completed'
                ORDER BY download_date DESC
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to list completed transcriptions: {e}")
            return []
        finally:
            conn.close()
