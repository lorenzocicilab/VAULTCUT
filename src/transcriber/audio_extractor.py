"""
VAULTCUT — Audio Extractor
============================
Uses FFmpeg to extract audio from a downloaded MP4 file and saves it
as a WAV file that Whisper can process.

Why WAV and not MP3?
  Whisper works best with raw WAV (PCM 16kHz mono).
  Converting to this exact format avoids any decoding issues inside Whisper.
  The WAV file is temporary — deleted immediately after transcription.

Why extract audio at all instead of giving Whisper the MP4?
  Whisper CAN read MP4 directly, but extracting to WAV first:
  - Makes the transcription faster (no video decoding overhead)
  - Uses less RAM (video frames never need to load)
  - Gives cleaner error messages if something goes wrong
  - Lets us delete the audio immediately, freeing space

FFmpeg command used:
  ffmpeg -i input.mp4 -vn -ac 1 -ar 16000 -acodec pcm_s16le output.wav
  Flags:
    -vn         → no video (audio only)
    -ac 1       → mono (Whisper doesn't use stereo)
    -ar 16000   → 16kHz sample rate (Whisper's native rate)
    -acodec pcm_s16le → 16-bit PCM (uncompressed, Whisper's preference)
    -y          → overwrite output if it exists

Usage:
    from src.transcriber.audio_extractor import AudioExtractor
    ae = AudioExtractor()
    wav_path = ae.extract(mp4_path, video_id)
    # wav_path is the path to the WAV file, or None on failure
    ae.cleanup(wav_path)  # deletes the WAV file
"""

import os
import sys
import subprocess
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from src.logger import get_logger

logger = get_logger("transcriber.audio")

# Where temporary WAV files go
TEMP_AUDIO_DIR = os.path.join(PROJECT_ROOT, "data", "temp_audio")

# FFmpeg extraction settings
SAMPLE_RATE  = 16000   # Hz — Whisper's native sample rate
CHANNELS     = 1       # Mono — Whisper doesn't use stereo
AUDIO_CODEC  = "pcm_s16le"  # 16-bit PCM — Whisper's preference

# Timeout for the FFmpeg extraction process (seconds)
# A 30-minute video at 16kHz mono takes ~5s to extract
EXTRACTION_TIMEOUT = 120


class AudioExtractor:
    """
    Extracts WAV audio from MP4 files for Whisper transcription.
    Handles temp file creation and cleanup.
    """

    def __init__(self):
        os.makedirs(TEMP_AUDIO_DIR, exist_ok=True)

    def get_wav_path(self, video_id: str) -> str:
        """
        Returns the expected path for the WAV temp file for a given video_id.
        Does not check if the file exists.
        """
        # Sanitize video_id for use as filename
        safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in video_id)
        return os.path.join(TEMP_AUDIO_DIR, f"{safe_id}.wav")

    def extract(self, mp4_path: str, video_id: str) -> str | None:
        """
        Extracts mono 16kHz WAV audio from an MP4 file using FFmpeg.

        Args:
            mp4_path:  Full path to the source MP4 file
            video_id:  Video ID string (used to name the WAV file)

        Returns:
            Full path to the created WAV file on success.
            None on failure.
        """
        # ── Validate input ─────────────────────────────────────
        if not mp4_path or not os.path.exists(mp4_path):
            logger.error(f"MP4 file not found: {mp4_path}")
            return None

        mp4_size_mb = os.path.getsize(mp4_path) / (1024 * 1024)
        if mp4_size_mb < 0.01:
            logger.error(f"MP4 file is too small ({mp4_size_mb:.2f}MB): {mp4_path}")
            return None

        wav_path = self.get_wav_path(video_id)

        # Delete any previous temp file for this video_id
        if os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except OSError:
                pass

        # ── Build FFmpeg command ────────────────────────────────
        cmd = [
            "ffmpeg",
            "-i",       mp4_path,      # Input file
            "-vn",                     # No video — audio only
            "-ac",      str(CHANNELS), # Mono
            "-ar",      str(SAMPLE_RATE), # 16 kHz
            "-acodec",  AUDIO_CODEC,   # PCM 16-bit
            "-y",                      # Overwrite output without asking
            "-loglevel", "error",      # Only show errors (not the usual wall of text)
            wav_path,                  # Output file
        ]

        logger.info(f"Extracting audio: {os.path.basename(mp4_path)} → {os.path.basename(wav_path)}")
        start = datetime.now()

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=EXTRACTION_TIMEOUT,
                encoding="utf-8",
                errors="replace",
            )

            elapsed = round((datetime.now() - start).total_seconds(), 1)

            if result.returncode != 0:
                error_msg = result.stderr.strip()[:300]
                logger.error(f"FFmpeg audio extraction failed (exit {result.returncode}): {error_msg}")
                return None

            # Verify the WAV file was created and has content
            if not os.path.exists(wav_path):
                logger.error(f"FFmpeg completed but WAV not found at: {wav_path}")
                return None

            wav_size_mb = os.path.getsize(wav_path) / (1024 * 1024)
            if wav_size_mb < 0.001:
                logger.error(f"Extracted WAV is empty ({wav_size_mb:.3f}MB)")
                self.cleanup(wav_path)
                return None

            logger.info(
                f"Audio extracted: {wav_size_mb:.1f}MB WAV in {elapsed}s "
                f"(from {mp4_size_mb:.1f}MB MP4)"
            )
            return wav_path

        except subprocess.TimeoutExpired:
            logger.error(f"FFmpeg timed out after {EXTRACTION_TIMEOUT}s for: {mp4_path}")
            self.cleanup(wav_path)
            return None

        except FileNotFoundError:
            logger.error("FFmpeg not found in PATH. Install FFmpeg and add it to your PATH.")
            return None

        except Exception as e:
            logger.error(f"Unexpected error during audio extraction: {e}")
            self.cleanup(wav_path)
            return None

    def cleanup(self, wav_path: str | None):
        """
        Deletes the temporary WAV file.
        Safe to call with None or a non-existent path.

        Args:
            wav_path: Path to the WAV file to delete, or None.
        """
        if not wav_path:
            return

        try:
            if os.path.exists(wav_path):
                os.remove(wav_path)
                logger.info(f"Temp audio deleted: {os.path.basename(wav_path)}")
        except OSError as e:
            logger.warning(f"Could not delete temp WAV {wav_path}: {e}")

    def cleanup_all_temp_files(self):
        """
        Deletes ALL WAV files in data/temp_audio/.
        Called at startup to clean up after crashes.

        If the system crashed mid-transcription, a WAV file may be
        left behind. This removes all of them safely.
        """
        if not os.path.exists(TEMP_AUDIO_DIR):
            return

        wav_files = [
            f for f in os.listdir(TEMP_AUDIO_DIR)
            if f.endswith(".wav")
        ]

        if not wav_files:
            return

        logger.info(f"Cleaning up {len(wav_files)} leftover temp audio files...")
        for filename in wav_files:
            full_path = os.path.join(TEMP_AUDIO_DIR, filename)
            try:
                os.remove(full_path)
                logger.info(f"  Deleted stale temp file: {filename}")
            except OSError as e:
                logger.warning(f"  Could not delete {filename}: {e}")


# ============================================================
# Self-test
# PowerShell: python src\transcriber\audio_extractor.py
# ============================================================
if __name__ == "__main__":
    import glob

    print("VAULTCUT Audio Extractor — Test")
    print("=" * 45)

    # Find a downloaded video to test with
    downloads_dir = os.path.join(PROJECT_ROOT, "data", "downloads")
    mp4_files = glob.glob(os.path.join(downloads_dir, "*.mp4"))

    if not mp4_files:
        print(f"No MP4 files found in {downloads_dir}")
        print("Download a video first: python manage_channels.py download now")
        sys.exit(1)

    test_mp4 = mp4_files[0]
    test_id  = os.path.splitext(os.path.basename(test_mp4))[0]

    print(f"Test file: {test_mp4}")
    print(f"Size:      {os.path.getsize(test_mp4)/1024/1024:.2f}MB")
    print()

    ae = AudioExtractor()

    print("Extracting audio...")
    wav_path = ae.extract(test_mp4, test_id)

    if wav_path:
        wav_size = os.path.getsize(wav_path) / (1024 * 1024)
        print(f"\n✓ WAV created: {wav_path}")
        print(f"  Size: {wav_size:.2f}MB")
        print()
        print("Cleaning up temp file...")
        ae.cleanup(wav_path)
        print("✓ Temp file deleted")
        print()
        print("AudioExtractor is working correctly.")
    else:
        print("\n✗ Audio extraction failed. Check that FFmpeg is in PATH:")
        print("  ffmpeg -version")
