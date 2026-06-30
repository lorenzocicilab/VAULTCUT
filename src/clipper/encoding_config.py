"""
VAULTCUT — Encoding Configuration
=====================================
Defines the exact FFmpeg parameters used when writing final Shorts clips.

YouTube Shorts requirements (as of 2024):
  - Format:       MP4
  - Codec:        H.264 (libx264)
  - Audio:        AAC
  - Aspect ratio: 9:16 (vertical)
  - Resolution:   1080x1920 recommended
  - Duration:     15-60 seconds
  - FPS:          30 (YouTube re-encodes anyway, but 30 is standard)
  - Bitrate:      YouTube recommends 8Mbps for 1080p

Why these specific settings:
  - 'medium' preset: good balance of speed and quality on a 2.5GHz CPU
    (fast=larger file, slow=better quality but takes 10x longer)
  - 8000k bitrate: high enough for crisp text on subtitles
  - 192k audio: standard high quality AAC
  - threads=2: leaves CPU headroom for other VAULTCUT processes
    (don't use all 4 threads or the whole PC slows to a crawl)

Usage:
    from src.clipper.encoding_config import get_encoding_params, SHORTS_RESOLUTION
    params = get_encoding_params()
    clip.write_videofile(path, **params)
"""

import os
import sys
import json

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Target resolution ─────────────────────────────────────────────────────────
# YouTube Shorts standard resolution
SHORTS_WIDTH  = 1080
SHORTS_HEIGHT = 1920
SHORTS_RESOLUTION = (SHORTS_WIDTH, SHORTS_HEIGHT)

# Minimum acceptable resolution (if CPU struggles with 1080p)
MIN_WIDTH  = 720
MIN_HEIGHT = 1280

# ── Default encoding params ───────────────────────────────────────────────────
# These are passed directly to moviepy's write_videofile()
SHORTS_ENCODING_PARAMS = {
    "codec":        "libx264",   # H.264 — universally compatible
    "audio_codec":  "aac",       # AAC audio — YouTube standard
    "bitrate":      "8000k",     # 8 Mbps video — crisp 1080p
    "audio_bitrate":"192k",      # High-quality audio
    "preset":       "medium",    # Encoding speed vs quality trade-off
    "fps":          30,          # Standard frame rate
    "threads":      2,           # Leave headroom for other VAULTCUT processes
    "logger":       None,        # Suppress moviepy's built-in progress bar
                                 # (we do our own logging)
}

# ── Aspect ratio math ─────────────────────────────────────────────────────────
# 9:16 = 0.5625
SHORTS_ASPECT_RATIO = 9 / 16

# When cropping a 16:9 source to 9:16:
# The crop width = source_height × (9/16)
# Example: 1080p source (1920×1080) → crop width = 1080 × 0.5625 = 607.5 ≈ 607
def get_crop_width(source_height: int) -> int:
    """
    Calculates the crop width needed to get a 9:16 ratio from a 16:9 source.

    The crop takes the center portion of the frame horizontally.
    The full height is kept — we crop width, not height.

    Args:
        source_height: Height of the source video in pixels

    Returns:
        Width to crop to (in pixels), rounded to nearest even number
        (FFmpeg requires even pixel dimensions)
    """
    crop_w = source_height * SHORTS_ASPECT_RATIO
    # Round to nearest even number (FFmpeg requirement)
    return int(crop_w) if int(crop_w) % 2 == 0 else int(crop_w) - 1


def get_encoding_params(use_lower_res: bool = False) -> dict:
    """
    Returns the FFmpeg encoding parameters for moviepy.

    Args:
        use_lower_res: If True, returns params for 720x1280 instead of 1080x1920.
                       Use this if encoding is too slow on your CPU.

    Returns:
        Dict of kwargs ready to pass to moviepy's write_videofile()
    """
    params = dict(SHORTS_ENCODING_PARAMS)

    if use_lower_res:
        # 720p takes ~40% less time to encode
        params["bitrate"] = "4000k"

    return params


def should_use_lower_res() -> bool:
    """
    Reads the resolution preference from settings.json.
    If the user hasn't set it, defaults to full 1080p.
    """
    settings_path = os.path.join(PROJECT_ROOT, "config", "settings.json")
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            settings = json.load(f)
        clipper = settings.get("clipper", {})
        resolution = clipper.get("output_resolution", "1080x1920")
        return resolution == "720x1280"
    except Exception:
        return False


def get_target_resolution() -> tuple:
    """Returns the (width, height) tuple for the target output resolution."""
    if should_use_lower_res():
        return (MIN_WIDTH, MIN_HEIGHT)
    return SHORTS_RESOLUTION


# ============================================================
# Self-test
# PowerShell: python src\clipper\encoding_config.py
# ============================================================
if __name__ == "__main__":
    print("VAULTCUT Encoding Configuration")
    print("=" * 45)
    print()

    target = get_target_resolution()
    params = get_encoding_params()

    print(f"Target resolution: {target[0]}x{target[1]}")
    print(f"Aspect ratio:      9:16 (vertical)")
    print()
    print("Encoding parameters:")
    for k, v in params.items():
        if v is not None:
            print(f"  {k:<15} {v}")

    print()
    print("Crop width calculations for common source resolutions:")
    for h, label in [(1080, "1920x1080"), (720, "1280x720"), (480, "854x480")]:
        cw = get_crop_width(h)
        print(f"  {label} source → crop to {cw}x{h} → scale to {target[0]}x{target[1]}")
