"""
VAULTCUT — YouTube Channel Discovery
======================================
Automatically finds YouTube channels that match current trending topics.

How it works:
  1. Reads the top trending keywords from the trend_history table
  2. For each keyword, calls YouTube Data API v3 to search for channels
  3. Scores each channel found (subscribers, views, upload frequency)
  4. Adds high-scoring channels to monitored_channels automatically

This runs every 12 hours alongside the trend engine.

API cost: ~3 units per search query (very cheap — daily limit is 10,000)
With 20 keywords searched, that is 60 units out of 10,000 per run.

Usage:
    from src.discovery.youtube_discovery import YouTubeDiscovery
    disc = YouTubeDiscovery(api_key="YOUR_KEY")
    disc.run()
"""

import sys
import os
import json
import time
from datetime import datetime
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from src.logger import get_logger
from src.trends.trend_storage import TrendStorage
from src.trends.category import classify
from src.discovery.channel_monitor import ChannelMonitor

logger = get_logger("discovery.youtube")

# How many trending keywords to search for channels (keeps API usage low)
MAX_KEYWORDS_TO_SEARCH = 15

# Minimum score for a channel to be auto-added
MIN_AUTO_ADD_SCORE = 5.0

# Minimum subscriber count to consider a channel worth monitoring
MIN_SUBSCRIBER_COUNT = 10_000

# How many channel results to examine per search query
RESULTS_PER_SEARCH = 5


class YouTubeDiscovery:
    """
    Searches YouTube for channels that match current trending topics
    and adds the best ones to the monitored_channels table.
    """

    def __init__(self, api_key: str, region: str = "US"):
        """
        Args:
            api_key: YouTube Data API v3 key (free, from Google Cloud Console)
            region:  Country code for search results
        """
        self.api_key = api_key
        self.region = region
        self._service = None
        self.monitor = ChannelMonitor()
        self.trend_storage = TrendStorage()

    def _get_service(self):
        """Builds the YouTube API client. Returns None if unavailable."""
        if self._service:
            return self._service
        if not self.api_key or self.api_key.startswith("YOUR_"):
            logger.warning("YouTube API key not configured — skipping discovery")
            return None
        try:
            from googleapiclient.discovery import build
            self._service = build(
                "youtube", "v3",
                developerKey=self.api_key,
                cache_discovery=False,
            )
            return self._service
        except ImportError:
            logger.error("google-api-python-client not installed. Run: pip install google-api-python-client")
            return None
        except Exception as e:
            logger.error(f"Failed to build YouTube API service: {e}")
            return None

    def _score_channel(self, channel_data: dict) -> float:
        """
        Scores a YouTube channel on a 0-10 scale based on:
          - Subscriber count  (bigger = more reach)
          - View count        (more total views = proven content)
          - Video count       (consistent uploader = reliable source)

        Args:
            channel_data: The 'statistics' dict from a YouTube channel API response

        Returns:
            Score from 0.0 to 10.0
        """
        try:
            subs = int(channel_data.get("subscriberCount", 0))
            views = int(channel_data.get("viewCount", 0))
            videos = int(channel_data.get("videoCount", 0))
        except (ValueError, TypeError):
            return 0.0

        # Subscriber score: 1M+ = 10, 100k = 7, 10k = 4, <10k = 1
        if subs >= 1_000_000:
            sub_score = 10.0
        elif subs >= 500_000:
            sub_score = 9.0
        elif subs >= 100_000:
            sub_score = 7.0
        elif subs >= 50_000:
            sub_score = 6.0
        elif subs >= 10_000:
            sub_score = 4.0
        else:
            sub_score = 1.0

        # Video count score: active uploaders score higher
        # 500+ videos = 10, 100 = 7, 50 = 5, 10 = 3
        if videos >= 500:
            vid_score = 10.0
        elif videos >= 100:
            vid_score = 7.0
        elif videos >= 50:
            vid_score = 5.0
        elif videos >= 10:
            vid_score = 3.0
        else:
            vid_score = 1.0

        # Weighted average: subscribers matter most (60%), videos matter (40%)
        final_score = (sub_score * 0.6) + (vid_score * 0.4)
        return round(min(10.0, final_score), 2)

    def _search_channels_for_keyword(self, keyword: str, category: str) -> list:
        """
        Searches YouTube for channels related to a keyword.
        Returns a list of channel info dicts.

        Args:
            keyword:  The trend keyword to search for
            category: The VAULTCUT category (gaming/news/sports/entertainment/tech)

        Returns:
            List of dicts with channel_id, channel_name, score, etc.
        """
        service = self._get_service()
        if not service:
            return []

        results = []
        try:
            # Step 1: Search for channels matching the keyword
            # type='channel' means only return channels, not videos
            search_response = service.search().list(
                part="snippet",
                q=keyword,
                type="channel",
                maxResults=RESULTS_PER_SEARCH,
                regionCode=self.region,
                relevanceLanguage="en",
            ).execute()

            channel_ids = []
            channel_snippets = {}
            for item in search_response.get("items", []):
                ch_id = item["snippet"]["channelId"]
                channel_ids.append(ch_id)
                channel_snippets[ch_id] = item["snippet"]

            if not channel_ids:
                return []

            # Step 2: Get statistics for the found channels
            # This tells us subscriber counts, view counts, video counts
            stats_response = service.channels().list(
                part="statistics,snippet",
                id=",".join(channel_ids),
            ).execute()

            for channel in stats_response.get("items", []):
                ch_id = channel["id"]
                stats = channel.get("statistics", {})
                snippet = channel.get("snippet", {})

                subs = int(stats.get("subscriberCount", 0))

                # Skip tiny channels — not worth monitoring
                if subs < MIN_SUBSCRIBER_COUNT:
                    logger.info(f"    Skip (too small): {snippet.get('title', '?')} — {subs:,} subs")
                    continue

                score = self._score_channel(stats)

                # Use avg views as a proxy: total views / number of videos
                total_views = int(stats.get("viewCount", 0))
                video_count = int(stats.get("videoCount", 1))
                avg_views = total_views / max(video_count, 1)

                results.append({
                    "channel_id":       ch_id,
                    "channel_name":     snippet.get("title", "Unknown"),
                    "description":      snippet.get("description", "")[:300],
                    "channel_url":      f"https://www.youtube.com/channel/{ch_id}",
                    "category":         category,
                    "subscriber_count": subs,
                    "avg_views":        round(avg_views, 0),
                    "video_count":      video_count,
                    "score":            score,
                    "discovery_keyword": keyword,
                })

                logger.info(
                    f"    Found: {snippet.get('title', '?')} — "
                    f"{subs:,} subs, score={score}"
                )

            time.sleep(1)  # Be polite to the API

        except Exception as e:
            error_str = str(e)
            if "quotaExceeded" in error_str:
                logger.error("YouTube API quota exceeded. Discovery paused until midnight PT.")
            elif "403" in error_str:
                logger.error("YouTube API key rejected (403). Check settings.json.")
            else:
                logger.error(f"YouTube search failed for '{keyword}': {e}")

        return results

    def run(self) -> int:
        """
        Main entry point for YouTube auto-discovery.

        Reads top trends → searches YouTube for matching channels →
        scores them → adds the best ones to monitored_channels.

        Returns:
            Number of new channels added to the database.
        """
        service = self._get_service()
        if not service:
            return 0

        logger.info("=== YouTube Discovery: Starting ===")

        # Get the top trending keywords from the last 24 hours
        top_trends = self.trend_storage.get_top_trends(
            limit=MAX_KEYWORDS_TO_SEARCH,
            hours_back=24
        )

        if not top_trends:
            logger.warning("No trends found in database — run the trend engine first")
            return 0

        logger.info(f"Using {len(top_trends)} trending keywords for channel discovery")

        all_candidates = []

        for i, trend in enumerate(top_trends):
            keyword = trend["keyword"]
            category = trend["category"]
            logger.info(f"  [{i+1}/{len(top_trends)}] Searching: '{keyword[:50]}' [{category}]")

            channels = self._search_channels_for_keyword(keyword, category)
            all_candidates.extend(channels)

            # Pause between searches to stay within API rate limits
            time.sleep(2)

        # Sort all candidates by score (best first)
        all_candidates.sort(key=lambda x: x["score"], reverse=True)

        # Deduplicate: same channel_id might appear from multiple keyword searches
        seen_ids = set()
        unique_candidates = []
        for ch in all_candidates:
            if ch["channel_id"] not in seen_ids:
                seen_ids.add(ch["channel_id"])
                unique_candidates.append(ch)

        logger.info(f"Found {len(unique_candidates)} unique channel candidates")

        # Add qualifying channels to the database
        added = 0
        for ch in unique_candidates:
            if ch["score"] < MIN_AUTO_ADD_SCORE:
                logger.info(f"  Skip (low score {ch['score']}): {ch['channel_name']}")
                continue

            row_id = self.monitor.add_youtube_channel(
                channel_id=ch["channel_id"],
                channel_name=ch["channel_name"],
                channel_url=ch["channel_url"],
                category=ch["category"],
                subscriber_count=ch["subscriber_count"],
                avg_views=ch["avg_views"],
                priority=ch["score"],
                discovery_source="auto",
                description=ch["description"],
            )

            if row_id:
                added += 1

        logger.info(f"=== YouTube Discovery: Done. {added} new channels added ===")
        return added
