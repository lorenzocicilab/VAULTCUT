"""
VAULTCUT — Trend Engine
=========================
The main trend detection system. Orchestrates all trend sources:
  - Google Trends     (pytrends, no API key)
  - YouTube Trending  (YouTube Data API v3, free key)
  - Reddit Hot Posts  (public JSON, no API key needed)
  - Twitter/X         (DISABLED — skipped, see twitter_trends.py)

After collecting, it:
  1. Saves all results to the SQLite trend_history table
  2. Prints a clean summary of the top 10 trends
  3. Logs everything to logs/

The APScheduler runs this every 12 hours automatically.
You can also run it manually at any time.

Run once manually:
    PowerShell: python src\trends\engine.py

Run with scheduler (every 12 hours):
    Called automatically by main.py
"""

import sys
import os
import json
import time
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from src.logger import get_logger
from src.trends.google_trends   import GoogleTrendsFetcher
from src.trends.youtube_trending import YouTubeTrendingFetcher
from src.trends.reddit_trends   import RedditTrendsFetcher
from src.trends.twitter_trends  import TwitterTrendsFetcher
from src.trends.trend_storage   import TrendStorage

logger = get_logger("trends.engine")


def load_settings() -> dict:
    """Loads config/settings.json and returns it as a dict."""
    settings_path = os.path.join(PROJECT_ROOT, "config", "settings.json")
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error(f"settings.json not found at: {settings_path}")
        return {}
    except json.JSONDecodeError as e:
        logger.error(f"settings.json has a formatting error: {e}")
        return {}


def print_trend_summary(storage: TrendStorage):
    """
    Prints a clean, readable summary of the top trending topics
    from the last 24 hours to the terminal.

    This is the "dashboard" output you'll see after each run.
    """
    separator = "=" * 65

    print()
    print(separator)
    print(f"  VAULTCUT TREND REPORT  —  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(separator)

    # Overall top 10
    top10 = storage.get_top_trends(limit=10, hours_back=24)

    if not top10:
        print("  No trend data yet. Run the engine first.")
        print(separator)
        return

    print(f"\n  TOP 10 TRENDS RIGHT NOW\n")
    for i, trend in enumerate(top10, 1):
        sources = trend["sources"] or "unknown"
        cat = trend["category"].upper()
        score = trend["avg_score"]
        keyword = trend["keyword"][:52]  # Truncate long titles
        appearances = trend["appearances"]

        # Score bar: █ characters to show score visually
        bar_len = int(score / 10)
        bar = "█" * bar_len + "░" * (10 - bar_len)

        print(f"  {i:2d}. [{cat:13s}] {bar} {score:5.1f}  {keyword}")
        print(f"       Sources: {sources}  |  Appearances: {appearances}")

    # Per-category breakdown
    print(f"\n  TOP TREND PER CHANNEL CATEGORY\n")
    by_cat = storage.get_trends_by_category(hours_back=24)
    category_emojis = {
        "gaming":        "GAMING       ",
        "news":          "NEWS         ",
        "sports":        "SPORTS       ",
        "entertainment": "ENTERTAINMENT",
        "tech":          "TECH         ",
    }

    for cat, trends in by_cat.items():
        label = category_emojis.get(cat, cat.upper())
        if trends:
            top = trends[0]
            print(f"  [{label}]  {top['avg_score']:5.1f}  {top['keyword'][:50]}")
        else:
            print(f"  [{label}]  No data yet")

    # Source stats
    print(f"\n  DATA SOURCES THIS RUN\n")
    stats = storage.get_source_stats(hours_back=2)  # Last 2 hours = this run
    source_names = {
        "google":  "Google Trends",
        "youtube": "YouTube Trending",
        "reddit":  "Reddit Hot Posts",
        "twitter": "Twitter/X (disabled)",
    }
    for source, label in source_names.items():
        count = stats.get(source, 0)
        status = f"{count} trends collected" if count > 0 else "0  (not configured or skipped)"
        print(f"  {label:<22}  {status}")

    total = storage.get_total_stored()
    print(f"\n  Total trends in database: {total:,}")
    print(separator)
    print()


def run_trend_engine():
    """
    Runs a complete trend collection cycle:
      1. Load settings
      2. Fetch from all sources
      3. Save to database
      4. Print summary
      5. Clean up old records

    This function is called:
      - Directly when you run: python src\trends\engine.py
      - Every 12 hours by APScheduler (in main.py)
    """
    start_time = time.time()
    logger.info("━" * 50)
    logger.info("TREND ENGINE: Starting collection cycle")
    logger.info("━" * 50)

    settings = load_settings()
    if not settings:
        logger.error("Cannot run trend engine — settings.json failed to load")
        return

    youtube_api_key = settings.get("youtube_api", {}).get("api_key", "")
    region = "US"  # You can make this configurable later

    storage = TrendStorage()
    all_trends = []

    # ----------------------------------------------------------
    # SOURCE 1: Google Trends
    # ----------------------------------------------------------
    logger.info("Source 1/4: Google Trends")
    try:
        google_fetcher = GoogleTrendsFetcher(geo=region)
        google_results = google_fetcher.fetch()
        all_trends.extend(google_results)
        logger.info(f"  Google: {len(google_results)} results")
    except Exception as e:
        logger.error(f"  Google Trends failed: {e}")

    # Short pause between sources
    time.sleep(3)

    # ----------------------------------------------------------
    # SOURCE 2: YouTube Trending
    # ----------------------------------------------------------
    logger.info("Source 2/4: YouTube Trending")
    if youtube_api_key and not youtube_api_key.startswith("YOUR_"):
        try:
            yt_fetcher = YouTubeTrendingFetcher(api_key=youtube_api_key, region=region)
            yt_results = yt_fetcher.fetch()
            all_trends.extend(yt_results)
            logger.info(f"  YouTube: {len(yt_results)} results")
        except Exception as e:
            logger.error(f"  YouTube Trending failed: {e}")
    else:
        logger.warning("  YouTube API key not configured — skipping. Add key to settings.json")

    time.sleep(2)

    # ----------------------------------------------------------
    # SOURCE 3: Reddit
    # ----------------------------------------------------------
    logger.info("Source 3/4: Reddit Hot Posts")
    try:
        reddit_fetcher = RedditTrendsFetcher(settings=settings)
        reddit_results = reddit_fetcher.fetch()
        all_trends.extend(reddit_results)
        logger.info(f"  Reddit: {len(reddit_results)} results")
    except Exception as e:
        logger.error(f"  Reddit failed: {e}")

    # ----------------------------------------------------------
    # SOURCE 4: Twitter/X (disabled — returns empty list)
    # ----------------------------------------------------------
    logger.info("Source 4/4: Twitter/X (disabled)")
    twitter_fetcher = TwitterTrendsFetcher()
    twitter_results = twitter_fetcher.fetch()
    all_trends.extend(twitter_results)

    # ----------------------------------------------------------
    # Save all results to the database
    # ----------------------------------------------------------
    logger.info(f"Total collected: {len(all_trends)} trend results")
    logger.info("Saving to database...")

    if all_trends:
        saved_count = storage.save_trends(all_trends)
        logger.info(f"Saved {saved_count} new trend records to database")
    else:
        logger.warning("No trends collected from any source — check your internet connection")

    # ----------------------------------------------------------
    # Print the readable summary
    # ----------------------------------------------------------
    print_trend_summary(storage)

    # ----------------------------------------------------------
    # Cleanup: remove trends older than 30 days
    # ----------------------------------------------------------
    storage.cleanup_old_trends(days_to_keep=30)

    elapsed = round(time.time() - start_time, 1)
    logger.info(f"Trend engine cycle complete in {elapsed}s")
    logger.info("Next run in 12 hours (or run manually: python src\\trends\\engine.py)")


# ============================================================
# Self-test / manual run
# PowerShell: python src\trends\engine.py
# ============================================================
if __name__ == "__main__":
    print("VAULTCUT Trend Engine — Manual Run")
    print("This will fetch trends from Google, YouTube, and Reddit.")
    print("Takes 1-3 minutes. Watch the log output below.\n")
    run_trend_engine()
