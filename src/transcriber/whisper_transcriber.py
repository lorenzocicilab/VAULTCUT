"""
VAULTCUT — Whisper Transcriber
================================
Transcribes audio using OpenAI's Whisper model running 100% locally.
No internet needed, no API key, free forever.

Model loading strategy:
  Whisper takes 5-10 seconds to load its model from disk.
  We load it ONCE when the WhisperTranscriber object is created
  and keep it in memory for the entire session.
  This means the second, third, fourth video all transcribe instantly
  without reloading the model each time.

Model sizes and RAM usage on CPU:
  tiny   → ~150MB RAM  → very fast (~10s/min audio)  → lower accuracy
  base   → ~290MB RAM  → fast     (~20s/min audio)  → good accuracy  ← we use this
  small  → ~500MB RAM  → medium   (~40s/min audio)  → better
  medium → ~1.5GB RAM  → slow     (~2min/min audio)  → best practical
  large  → ~3GB RAM    → very slow                   → best accuracy

With 16GB RAM and a 2.5GHz CPU, 'base' is the right choice:
  - Keeps RAM available for other processes
  - Transcribes a 10-minute video in about 3-4 minutes
  - Accuracy is good enough for clip detection

Output format:
  The result dict contains full_text and a segments list.
  Each segment has start/end timestamps in seconds (floats).
  Phase 6 (Clipper) uses these timestamps to find the best moments.

Usage:
    from src.transcriber.whisper_transcriber import WhisperTranscriber
    wt = WhisperTranscriber(model_size="base")
    result = wt.transcribe("/path/to/audio.wav")
    # result["segments"] → list of {start, end, text}
    # result["full_text"] → complete transcript as one string
"""

import os
import sys
import time
from datetime import datetime
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from src.logger import get_logger

logger = get_logger("transcriber.whisper")


def _load_model_size_from_settings() -> str:
    """Reads whisper_model setting from settings.json. Defaults to 'base'."""
    import json
    settings_path = os.path.join(PROJECT_ROOT, "config", "settings.json")
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            settings = json.load(f)
        return settings.get("whisper_model", "base")
    except Exception:
        return "base"


class WhisperTranscriber:
    """
    Wraps the Whisper model with session-level model caching.

    The model is loaded once on first use and kept in memory.
    All subsequent transcriptions in the same process reuse the loaded model.
    """

    # Class-level cache so the model survives across multiple
    # WhisperTranscriber instances created in the same Python process.
    _model_cache = {}

    def __init__(self, model_size: str = None):
        """
        Args:
            model_size: Whisper model to use. If None, reads from settings.json.
                        Options: 'tiny', 'base', 'small', 'medium', 'large'
        """
        self.model_size = model_size or _load_model_size_from_settings()
        self._model = None

    def _get_model(self):
        """
        Returns the loaded Whisper model, loading it from disk if needed.

        Uses a class-level dict to cache the model so it's shared across
        all WhisperTranscriber instances in the same Python session.
        This means the model only loads once per run of main.py.
        """
        if self.model_size in WhisperTranscriber._model_cache:
            return WhisperTranscriber._model_cache[self.model_size]

        try:
            import whisper
        except ImportError:
            logger.error("Whisper not installed. Run: pip install openai-whisper")
            return None

        logger.info(f"Loading Whisper model: '{self.model_size}'")
        logger.info("First load takes 5-15 seconds and downloads the model if needed...")
        logger.info(f"Model will be ~290MB RAM for 'base' size.")

        load_start = time.time()
        try:
            model = whisper.load_model(self.model_size)
            elapsed = round(time.time() - load_start, 1)
            logger.info(f"Whisper '{self.model_size}' model loaded in {elapsed}s")

            # Cache it for future calls
            WhisperTranscriber._model_cache[self.model_size] = model
            return model

        except Exception as e:
            logger.error(f"Failed to load Whisper model '{self.model_size}': {e}")
            logger.error("If this is the first run, Whisper needs to download the model.")
            logger.error("Make sure you have an internet connection for the first use.")
            return None

    def transcribe(self, wav_path: str) -> Optional[dict]:
        """
        Transcribes a WAV audio file and returns the full result with timestamps.

        Args:
            wav_path: Full path to the WAV audio file

        Returns:
            Dict with structure:
            {
                "full_text":  "entire transcript as one string",
                "language":   "en",
                "segments":   [
                    {"start": 0.0, "end": 4.8, "text": "Hello everyone"},
                    {"start": 4.8, "end": 9.2, "text": "Welcome to the stream"},
                    ...
                ],
                "duration_seconds": 342.5,
                "transcription_time_seconds": 87.3,
                "word_count": 1203,
            }
            Returns None on failure.
        """
        if not wav_path or not os.path.exists(wav_path):
            logger.error(f"WAV file not found: {wav_path}")
            return None

        wav_size_mb = os.path.getsize(wav_path) / (1024 * 1024)
        logger.info(f"Starting transcription: {os.path.basename(wav_path)} ({wav_size_mb:.1f}MB)")

        model = self._get_model()
        if model is None:
            return None

        transcribe_start = time.time()

        try:
            # ── Run Whisper ────────────────────────────────────
            # verbose=False suppresses Whisper's built-in progress prints
            # (we do our own logging)
            # fp16=False forces CPU mode (no GPU available on this system)
            # language=None means auto-detect (good for non-English content too)
            result = model.transcribe(
                wav_path,
                verbose=False,
                fp16=False,       # Must be False for CPU-only systems
                language=None,    # Auto-detect language
                task="transcribe",
                # word_timestamps=True  ← uncomment later if you want word-level timestamps
                # (uses more memory but gives finer control)
            )

            elapsed = round(time.time() - transcribe_start, 1)

            # ── Parse the result ───────────────────────────────
            # result is a Whisper dict with keys:
            #   "text"     → full transcript (all segments joined)
            #   "language" → detected language code ("en", "es", etc.)
            #   "segments" → list of segment dicts with timing info

            full_text = result.get("text", "").strip()
            language  = result.get("language", "unknown")
            raw_segs  = result.get("segments", [])

            # Clean up segments — keep only what Phase 6 needs
            segments = []
            for seg in raw_segs:
                start = round(float(seg.get("start", 0.0)), 2)
                end   = round(float(seg.get("end", 0.0)), 2)
                text  = str(seg.get("text", "")).strip()

                if not text:
                    continue  # Skip empty segments

                segments.append({
                    "start": start,
                    "end":   end,
                    "text":  text,
                })

            # Calculate stats
            word_count = len(full_text.split()) if full_text else 0
            duration   = segments[-1]["end"] if segments else 0.0

            logger.info(
                f"Transcription complete in {elapsed}s | "
                f"language={language} | "
                f"{len(segments)} segments | "
                f"{word_count} words"
            )

            # Speed ratio: how many seconds of audio per second of processing
            speed_ratio = round(duration / elapsed, 1) if elapsed > 0 else 0
            logger.info(f"Speed: {speed_ratio}x realtime ({duration:.0f}s audio in {elapsed}s)")

            return {
                "full_text":                   full_text,
                "language":                    language,
                "segments":                    segments,
                "duration_seconds":            round(duration, 2),
                "transcription_time_seconds":  elapsed,
                "word_count":                  word_count,
            }

        except Exception as e:
            elapsed = round(time.time() - transcribe_start, 1)
            logger.error(f"Whisper transcription failed after {elapsed}s: {e}")

            # Give specific guidance for common errors
            error_str = str(e).lower()
            if "out of memory" in error_str or "memory" in error_str:
                logger.error(
                    "RAM issue. Try switching to 'tiny' model in settings.json: "
                    "change whisper_model from 'base' to 'tiny'"
                )
            elif "ffmpeg" in error_str:
                logger.error("FFmpeg issue loading audio. Check FFmpeg is in PATH: ffmpeg -version")
            elif "cuda" in error_str or "gpu" in error_str:
                logger.error("GPU/CUDA error. Set fp16=False (already set). This is a CPU-only system.")

            return None

    def estimate_time(self, audio_duration_seconds: float) -> float:
        """
        Estimates how long transcription will take in seconds.

        Based on benchmarks for 'base' model on a 2.5GHz CPU:
          - Roughly 0.3x realtime = 30s of audio takes ~9s to transcribe
          - This estimate is conservative (actual speed often faster)

        Args:
            audio_duration_seconds: Length of the audio in seconds

        Returns:
            Estimated transcription time in seconds
        """
        # Speed ratios per model (lower = slower)
        speed_ratios = {
            "tiny":   5.0,  # 5x realtime  (fast but less accurate)
            "base":   3.0,  # 3x realtime
            "small":  1.5,  # 1.5x realtime
            "medium": 0.5,  # 0.5x realtime (2x slower than realtime)
            "large":  0.25, # 0.25x realtime (4x slower than realtime)
        }
        ratio = speed_ratios.get(self.model_size, 3.0)
        return round(audio_duration_seconds / ratio, 0)


# ============================================================
# Self-test
# PowerShell: python src\transcriber\whisper_transcriber.py
# ============================================================
if __name__ == "__main__":
    import glob
    from src.transcriber.audio_extractor import AudioExtractor

    print("VAULTCUT Whisper Transcriber — Test")
    print("=" * 45)
    print("This will load the Whisper model and transcribe one video.")
    print("First run may download the model (~140MB for 'base').")
    print()

    # Find a downloaded MP4 to test with
    downloads_dir = os.path.join(PROJECT_ROOT, "data", "downloads")
    mp4_files = glob.glob(os.path.join(downloads_dir, "*.mp4"))

    if not mp4_files:
        print(f"No MP4 files in {downloads_dir}")
        print("Run: python manage_channels.py download now")
        sys.exit(1)

    test_mp4 = mp4_files[0]
    test_id  = os.path.splitext(os.path.basename(test_mp4))[0]
    mp4_size = os.path.getsize(test_mp4) / (1024 * 1024)

    print(f"Test file: {os.path.basename(test_mp4)} ({mp4_size:.1f}MB)")
    print()

    # Extract audio
    print("Step 1/2: Extracting audio...")
    ae = AudioExtractor()
    wav_path = ae.extract(test_mp4, test_id)

    if not wav_path:
        print("Audio extraction failed. Check FFmpeg.")
        sys.exit(1)

    print(f"WAV: {wav_path}")
    print()

    # Transcribe
    print("Step 2/2: Transcribing with Whisper...")
    model_size = _load_model_size_from_settings()
    print(f"Model: {model_size}")

    wt = WhisperTranscriber(model_size=model_size)
    result = wt.transcribe(wav_path)
    ae.cleanup(wav_path)

    if result:
        print()
        print(f"✓ Transcription complete!")
        print(f"  Language:  {result['language']}")
        print(f"  Segments:  {len(result['segments'])}")
        print(f"  Words:     {result['word_count']}")
        print(f"  Time taken: {result['transcription_time_seconds']}s")
        print()
        print("First 3 segments:")
        for seg in result["segments"][:3]:
            print(f"  [{seg['start']:6.2f}s → {seg['end']:6.2f}s]  {seg['text'][:70]}")
        print()
        print("First 200 chars of transcript:")
        print(f"  {result['full_text'][:200]}")
    else:
        print("✗ Transcription failed. Check logs/errors.log")
