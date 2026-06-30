"""
VAULTCUT — Clip Validator
===========================
Takes raw clip suggestions from Mistral and enforces:
  1. Duration: every clip must be 30-59 seconds
     - Too short → extend end time
     - Too long  → trim to 59s from start
  2. Score:    only clips with virality_score >= 7.0 pass
  3. Bounds:   clip cannot start before 0 or end after video duration
  4. Fields:   every clip must have start, end, virality_score, clip_type, reason
  5. Overlaps: if two clips overlap by > 50% of the shorter one, keep only the higher-scored

Why strict validation?
  Mistral is very good at identifying interesting moments but sometimes:
  - Suggests a 10-second "clip" (too short for Shorts)
  - Forgets to include required fields
  - Suggests times outside the actual video duration
  - Suggests two clips for basically the same moment

This module catches all of those and fixes or discards them before
anything gets saved to the database or cut by FFmpeg.

Usage:
    from src.analyzer.clip_validator import ClipValidator
    validator = ClipValidator(video_duration=342.0, min_score=7.0)
    valid_clips = validator.validate_all(raw_clips_from_mistral)
"""

import os
import sys
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from src.logger import get_logger

logger = get_logger("analyzer.validator")

# YouTube Shorts duration requirements
MIN_CLIP_DURATION = 30   # seconds
MAX_CLIP_DURATION = 59   # seconds

# Minimum virality score to keep a clip
DEFAULT_MIN_SCORE = 7.0

# If two clips overlap by more than this fraction of the shorter clip's length,
# they are considered duplicates. Keep only the higher-scored one.
MAX_OVERLAP_FRACTION = 0.5

# Valid clip types — if Mistral returns something else, we default to 'highlight'
VALID_CLIP_TYPES = {"hook", "reaction", "highlight", "funny", "shocking"}


def _load_min_score() -> float:
    """Reads min_virality_score from settings.json."""
    import json
    settings_path = os.path.join(PROJECT_ROOT, "config", "settings.json")
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            return float(json.load(f).get("min_virality_score", DEFAULT_MIN_SCORE))
    except Exception:
        return DEFAULT_MIN_SCORE


class ClipValidator:
    """
    Validates and cleans a list of raw clip dicts from Mistral.

    Instantiate with the video's duration so bounds can be enforced.
    """

    def __init__(self, video_duration: float, min_score: float = None):
        """
        Args:
            video_duration: Total video length in seconds (from transcript)
            min_score:      Minimum virality_score to keep. If None, reads from settings.json.
        """
        self.video_duration = float(video_duration) if video_duration else 0.0
        self.min_score      = min_score if min_score is not None else _load_min_score()

    def _validate_fields(self, clip: dict) -> Optional[dict]:
        """
        Checks that a clip has all required fields and correct types.
        Returns a normalized clip dict, or None if the clip is invalid.

        Required fields: start, end, virality_score
        Optional with defaults: clip_type, reason
        """
        # start and end are required
        try:
            start = float(clip.get("start", -1))
            end   = float(clip.get("end",   -1))
        except (ValueError, TypeError):
            logger.debug(f"  Clip rejected: non-numeric start/end: {clip}")
            return None

        if start < 0 or end < 0:
            logger.debug(f"  Clip rejected: missing start or end: {clip}")
            return None

        # virality_score is required
        try:
            score = float(clip.get("virality_score", -1))
        except (ValueError, TypeError):
            logger.debug(f"  Clip rejected: non-numeric virality_score: {clip}")
            return None

        if score < 0:
            logger.debug(f"  Clip rejected: missing virality_score: {clip}")
            return None

        # Clamp score to valid range
        score = min(10.0, max(0.0, score))

        # Optional fields with defaults
        clip_type = str(clip.get("clip_type", "highlight")).lower().strip()
        if clip_type not in VALID_CLIP_TYPES:
            clip_type = "highlight"

        reason = str(clip.get("reason", "")).strip()[:300]

        return {
            "start":          round(start, 2),
            "end":            round(end, 2),
            "virality_score": round(score, 1),
            "clip_type":      clip_type,
            "reason":         reason,
        }

    def _enforce_duration(self, clip: dict) -> Optional[dict]:
        """
        Ensures the clip is between MIN_CLIP_DURATION and MAX_CLIP_DURATION seconds.

        Rules:
          - If duration < 30s: extend the end time forward to make it 30s.
            If that would go past the video end: extend backward from end instead.
            If the video itself is too short: discard.
          - If duration > 59s: trim end to start + 59s.
          - If start >= end after adjustments: discard.

        Args:
            clip: Validated clip dict with start, end, virality_score, etc.

        Returns:
            Adjusted clip dict, or None if it cannot be made valid.
        """
        start    = clip["start"]
        end      = clip["end"]
        duration = end - start

        # ── Too long: trim ────────────────────────────────────
        if duration > MAX_CLIP_DURATION:
            old_end = end
            end     = start + MAX_CLIP_DURATION
            logger.debug(
                f"  Clip trimmed: {start:.1f}→{old_end:.1f} "
                f"({duration:.0f}s) → {start:.1f}→{end:.1f} ({MAX_CLIP_DURATION}s)"
            )

        # ── Too short: extend ─────────────────────────────────
        elif duration < MIN_CLIP_DURATION:
            target_duration = MIN_CLIP_DURATION
            old_end = end

            # Try extending forward first (most natural)
            new_end = start + target_duration
            if new_end <= self.video_duration:
                end = new_end
            else:
                # Can't extend forward — try extending backward
                new_start = end - target_duration
                if new_start >= 0:
                    start = new_start
                    # end stays the same
                else:
                    # Video is genuinely too short for this clip
                    logger.debug(
                        f"  Clip discarded: too short ({duration:.0f}s) "
                        f"and cannot be extended within video bounds"
                    )
                    return None

            logger.debug(
                f"  Clip extended: {clip['start']:.1f}→{old_end:.1f} "
                f"({duration:.0f}s) → {start:.1f}→{end:.1f} "
                f"({end-start:.0f}s)"
            )

        # ── Bounds check ──────────────────────────────────────
        # Clip cannot start before the video or end after it
        if self.video_duration > 0:
            start = max(0.0, start)
            end   = min(self.video_duration, end)

        # Final sanity check
        if end - start < MIN_CLIP_DURATION:
            logger.debug(
                f"  Clip discarded after adjustment: "
                f"{start:.1f}→{end:.1f} ({end-start:.0f}s) still too short"
            )
            return None

        clip = dict(clip)  # copy to avoid mutating the original
        clip["start"] = round(start, 2)
        clip["end"]   = round(end,   2)
        return clip

    def _apply_score_filter(self, clip: dict) -> bool:
        """Returns True if the clip's virality_score meets the minimum threshold."""
        return clip["virality_score"] >= self.min_score

    def _remove_overlaps(self, clips: list) -> list:
        """
        Removes duplicate clips that cover nearly the same moment.

        Algorithm:
          1. Sort by virality_score descending (best first)
          2. For each clip, check if it overlaps > 50% with any already-kept clip
          3. If it does: discard (the better-scored version was already kept)
          4. If it doesn't: keep it

        Overlap fraction is calculated relative to the shorter clip's duration.
        This means a tiny 30s clip overlapping a 59s clip counts as fully overlapping
        if 15+ seconds of the short clip are inside the long one.

        Args:
            clips: List of validated clip dicts

        Returns:
            Deduplicated list with overlapping clips removed.
        """
        if len(clips) <= 1:
            return clips

        # Sort best score first so we always keep the better clip when there's a conflict
        sorted_clips = sorted(clips, key=lambda c: c["virality_score"], reverse=True)
        kept = []

        for candidate in sorted_clips:
            c_start = candidate["start"]
            c_end   = candidate["end"]
            c_len   = c_end - c_start

            overlaps_existing = False

            for existing in kept:
                e_start = existing["start"]
                e_end   = existing["end"]
                e_len   = e_end - e_start

                # Calculate overlap between candidate and existing
                overlap_start = max(c_start, e_start)
                overlap_end   = min(c_end,   e_end)
                overlap_len   = max(0.0, overlap_end - overlap_start)

                # Overlap fraction relative to the shorter clip
                shorter_len       = min(c_len, e_len)
                overlap_fraction  = overlap_len / shorter_len if shorter_len > 0 else 0

                if overlap_fraction > MAX_OVERLAP_FRACTION:
                    overlaps_existing = True
                    logger.debug(
                        f"  Duplicate removed: [{c_start:.1f}→{c_end:.1f}] "
                        f"overlaps {overlap_fraction:.0%} with [{e_start:.1f}→{e_end:.1f}]"
                    )
                    break

            if not overlaps_existing:
                kept.append(candidate)

        removed = len(sorted_clips) - len(kept)
        if removed > 0:
            logger.info(f"  Overlap check: removed {removed} duplicate clip(s)")

        return kept

    def validate_all(self, raw_clips: list) -> list:
        """
        Main entry point. Takes a list of raw Mistral clip dicts and returns
        a clean, validated list ready for database insertion.

        Full pipeline per clip:
          1. Check required fields exist (discard if malformed)
          2. Enforce 30-59s duration (extend/trim/discard as needed)
          3. Filter by virality_score >= min_score (discard if too low)
          4. Remove overlapping duplicates (keep higher-scored version)

        Args:
            raw_clips: List of dicts from MistralAnalyzer.analyze_video()

        Returns:
            List of clean, validated clip dicts.
            May be shorter than (or empty compared to) the input.
        """
        if not raw_clips:
            return []

        logger.info(f"Validating {len(raw_clips)} raw clips...")

        step1_field_check = []
        for clip in raw_clips:
            normalized = self._validate_fields(clip)
            if normalized:
                step1_field_check.append(normalized)

        logger.info(
            f"  After field check:   {len(step1_field_check)}/{len(raw_clips)} clips"
        )

        step2_duration = []
        for clip in step1_field_check:
            adjusted = self._enforce_duration(clip)
            if adjusted:
                step2_duration.append(adjusted)

        logger.info(
            f"  After duration check: {len(step2_duration)}/{len(step1_field_check)} clips"
        )

        step3_score = [c for c in step2_duration if self._apply_score_filter(c)]
        logger.info(
            f"  After score filter (≥{self.min_score}): "
            f"{len(step3_score)}/{len(step2_duration)} clips"
        )

        step4_deduped = self._remove_overlaps(step3_score)
        logger.info(
            f"  After overlap removal: {len(step4_deduped)} final clips"
        )

        # Log each surviving clip
        for i, clip in enumerate(step4_deduped, 1):
            dur = clip["end"] - clip["start"]
            logger.info(
                f"  Clip {i}: [{clip['start']:.1f}s→{clip['end']:.1f}s] "
                f"({dur:.0f}s) | score={clip['virality_score']} | "
                f"type={clip['clip_type']}"
            )
            logger.info(f"    Reason: {clip['reason'][:80]}")

        return step4_deduped


# ============================================================
# Self-test
# PowerShell: python src\analyzer\clip_validator.py
# ============================================================
if __name__ == "__main__":
    print("VAULTCUT Clip Validator — Test")
    print("=" * 45)

    validator = ClipValidator(video_duration=300.0, min_score=7.0)

    test_clips = [
        # Should PASS: perfect clip
        {"start": 10.0, "end": 55.0, "virality_score": 8.5, "clip_type": "hook",
         "reason": "Shocking reveal grabs attention immediately"},
        # Should PASS then be extended to 30s: too short but extendable
        {"start": 60.0, "end": 70.0, "virality_score": 8.0, "clip_type": "funny",
         "reason": "Hilarious reaction moment"},
        # Should be TRIMMED to 59s: too long
        {"start": 100.0, "end": 200.0, "virality_score": 9.0, "clip_type": "shocking",
         "reason": "Extended dramatic moment"},
        # Should be DISCARDED: score too low
        {"start": 150.0, "end": 190.0, "virality_score": 5.0, "clip_type": "highlight",
         "reason": "Mediocre moment"},
        # Should be DISCARDED: overlaps with clip 1
        {"start": 12.0, "end": 50.0, "virality_score": 7.5, "clip_type": "reaction",
         "reason": "Same moment as clip 1"},
        # Should be DISCARDED: missing required field
        {"start": 200.0, "virality_score": 8.0},
    ]

    print(f"Input: {len(test_clips)} raw clips")
    print(f"min_score={validator.min_score}, video_duration={validator.video_duration}s")
    print()

    results = validator.validate_all(test_clips)

    print(f"\nOutput: {len(results)} valid clips")
    for i, c in enumerate(results, 1):
        dur = c['end'] - c['start']
        print(
            f"  {i}. [{c['start']:.1f}s→{c['end']:.1f}s] "
            f"({dur:.0f}s) score={c['virality_score']} "
            f"type={c['clip_type']}"
        )
        print(f"     {c['reason'][:70]}")
