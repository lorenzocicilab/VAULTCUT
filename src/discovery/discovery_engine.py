"""
VAULTCUT — Discovery Engine
==============================
Master controller for Phase 3. Orchestrates all discovery jobs:
  1. YouTube channel auto-discovery  (from trends)
  2. Twitch streamer auto-discovery  (from gaming trends)
  3. YouTube video queue builder     (new videos from monitored channels)
  4. Twitch VOD queue builder        (new VODs from monitored streamers)

This is the single function called by the scheduler in main.py.
Each job can also be run independently for testing.

Called by main.py:
  - run_channel_discovery()  → every 12 hours (same as trend engine)
  - run_youtube_queue_check() → every 1 hour
  - run_twitch_queue_check()  → every 30 minutes

Manual run (all discovery at once):
    PowerShell: python src\discovery\discovery_engine.py
"""

import sys
import os
import json
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from src.logger import get_logger
from src.discovery.channel_monitor  import ChannelMonitor
from src.discovery.youtube_discovery import YouTubeDiscovery
from src.discovery.twitch_discovery  import TwitchDiscovery
from src.discovery.video_queue       import VideoQueue

logger = get_logger("discovery.engine")


def _load_settings() -> dict:
    """Loads config/settings.json. Returns empty dict on error."""
    path = os.path.join(PROJECT_ROOT, "config", "settings.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error(f"settings.json not found at: {path}")
        return {}
    except json.JSONDecodeError as e:
        logger.error(f"settings.json has a formatting error: {e}")
        return {}


def run_channel_discovery():
    """
    Runs YouTube and Twitch auto-discovery.
    Reads current trends and finds matching channels/streamers.
    Adds new ones to monitored_channels table.

    Called every 12 hours by the scheduler.
    Can also be run manually: python src\discovery\discovery_engine.py
    """
    logger.info("=" * 55)
    logger.info("DISCOVERY ENGINE: Channel discovery starting")
    logger.info("=" * 55)

    settings = _load_settings()
    if not settings:
        logger.error("Cannot run discovery — settings.json failed to load")
        return

    youtube_api_key = settings.get("youtube_api", {}).get("api_key", "")

    # ── YouTube channel discovery ──────────────────────────────
    logger.info("Step 1/2: YouTube channel discovery")
    try:
        yt_disc = YouTubeDiscovery(api_key=youtube_api_key)
        new_yt = yt_disc.run()
        logger.info(f"  YouTube discovery: {new_yt} new channels added")
    except Exception as e:
        logger.error(f"  YouTube discovery failed: {e}")

    # ── Twitch streamer discovery ──────────────────────────────
    logger.info("Step 2/2: Twitch streamer discovery")
    try:
        tw_disc = TwitchDiscovery(settings=settings)
        new_tw = tw_disc.run()
        logger.info(f"  Twitch discovery: {new_tw} new streamers added")
    except Exception as e:
        logger.error(f"  Twitch discovery failed: {e}")

    # ── Print summary ─────────────────────────────────────────
    monitor = ChannelMonitor()
    all_channels = monitor.list_all(active_only=True)
    yt_count = sum(1 for c in all_channels if c["source_type"] == "youtube")
    tw_count = sum(1 for c in all_channels if c["source_type"] == "twitch")

    logger.info("")
    logger.info(f"MONITORED CHANNELS: {yt_count} YouTube  |  {tw_count} Twitch  |  {len(all_channels)} total")
    logger.info("=" * 55)


def run_youtube_queue_check():
    """
    Checks all monitored YouTube channels for new videos
    and adds them to the download queue.

    Called every 1 hour by the scheduler.
    """
    logger.info("QUEUE CHECK: YouTube channels")
    settings = _load_settings()
    youtube_api_key = settings.get("youtube_api", {}).get("api_key", "")
    twitch_settings = settings.get("twitch_api", {})

    try:
        vq = VideoQueue(
            youtube_api_key=youtube_api_key,
            twitch_settings=twitch_settings,
        )
        queued = vq.check_all_youtube_channels()
        stats = vq.get_queue_stats()

        logger.info(f"Queue check complete. {queued} new videos added.")
        logger.info(f"Queue state: {stats}")
    except Exception as e:
        logger.error(f"YouTube queue check failed: {e}")


def run_twitch_queue_check():
    """
    Checks all monitored Twitch streamers for new VODs
    and adds them to the download queue.

    Called every 30 minutes by the scheduler.
    """
    logger.info("QUEUE CHECK: Twitch VODs")
    settings = _load_settings()
    twitch_settings = settings.get("twitch_api", {})

    try:
        vq = VideoQueue(twitch_settings=twitch_settings)
        queued = vq.check_all_twitch_streamers()
        logger.info(f"Twitch queue check complete. {queued} new VODs added.")
    except Exception as e:
        logger.error(f"Twitch queue check failed: {e}")


def print_discovery_status():
    """
    Prints a readable status summary to the terminal.
    Shows all monitored channels and the current queue state.
    """
    monitor = ChannelMonitor()
    settings = _load_settings()
    twitch_settings = settings.get("twitch_api", {})

    separator = "=" * 65

    print()
    print(separator)
    print(f"  VAULTCUT DISCOVERY STATUS  —  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(separator)

    all_channels = monitor.list_all(active_only=False)
    active = [c for c in all_channels if c["active"]]
    inactive = [c for c in all_channels if not c["active"]]

    if not all_channels:
        print()
        print("  No channels in database yet.")
        print()
        print("  Add channels manually:")
        print("    python manage_channels.py add youtube https://youtube.com/c/ChannelName")
        print("    python manage_channels.py add twitch username")
        print()
        print("  Or run auto-discovery (needs trend data + YouTube API key):")
        print("    python src\\discovery\\discovery_engine.py")
        print(separator)
        return

    print(f"\n  ACTIVE CHANNELS ({len(active)} total)\n")

    # Group by source type
    yt_channels = [c for c in active if c["source_type"] == "youtube"]
    tw_channels  = [c for c in active if c["source_type"] == "twitch"]

    if yt_channels:
        print(f"  YouTube ({len(yt_channels)}):")
        for ch in yt_channels[:20]:  # Show max 20
            src = "auto" if ch.get("discovery_source") == "auto" else "manual"
            subs = ch.get("subscriber_count", 0)
            subs_str = f"{subs/1_000_000:.1f}M" if subs >= 1_000_000 else f"{subs/1_000:.0f}K" if subs >= 1_000 else str(subs)
            print(
                f"    [id={ch['id']:3d}] [{ch['category']:13s}] "
                f"[{src:6s}] [{subs_str:6s} subs]  {ch['channel_name']}"
            )

    if tw_channels:
        print(f"\n  Twitch ({len(tw_channels)}):")
        for ch in tw_channels[:20]:
            src = "auto" if ch.get("discovery_source") == "auto" else "manual"
            print(
                f"    [id={ch['id']:3d}] [{ch['category']:13s}] "
                f"[{src:6s}]  {ch['channel_name']}"
            )

    if inactive:
        print(f"\n  INACTIVE ({len(inactive)} channels — use 'list --all' to see)")

    # Queue state
    try:
        vq = VideoQueue(twitch_settings=twitch_settings)
        queue_stats = vq.get_queue_stats()
        print(f"\n  DOWNLOAD QUEUE")
        if queue_stats:
            for status, count in queue_stats.items():
                print(f"    {status:<20} {count} videos")
        else:
            print("    Queue is empty.")
    except Exception:
        pass

    print(separator)
    print()


# ============================================================
# Run directly to do a manual discovery run
# PowerShell: python src\discovery\discovery_engine.py
# ============================================================
if __name__ == "__main__":
    print("VAULTCUT Discovery Engine — Manual Run")
    print("This will search for new channels and check monitored ones for new videos.")
    print()

    # Show current status first
    print_discovery_status()

    # Run discovery
    run_channel_discovery()

    # Then check queues
    run_youtube_queue_check()
    run_twitch_queue_check()

    # Show updated status
    print("\nUpdated status:")
    print_discovery_status()

def run_discovery():
    """Wrapper for backward compatibility with main.py scheduler."""
    run_channel_discovery()
    run_youtube_queue_check()
    run_twitch_queue_check()
