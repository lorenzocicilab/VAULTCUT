"""
VAULTCUT — YouTube Trending Fetcher
======================================
Fetches currently trending videos from YouTube using the official
YouTube Data API v3. This is the best source for VAULTCUT because
it shows exactly what people are watching on YouTube right now.

The free API quota is 10,000 units/day.
This module uses about 100 units per run (very efficient).

What it fetches:
  - Top 50 trending videos per category
  - Categories: Gaming, News, Sports, Entertainment, Science & Tech

Requirements:
  - YouTube Data API v3 key in config/settings.json
  - Free from: https://console.cloud.google.com

Usage:
    from src.trends.youtube_trending import YouTubeTrendingFetcher
    fetcher = YouTubeTrendingFetcher(api_key="YOUR_KEY")
    results = fetcher.fetch()
"""

import sys
import os
import json
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from src.logger import get_logger
from src.trends.category import classify

logger = get_logger("trends.youtube")


# YouTube Data API category IDs
# These are the official numeric IDs YouTube uses internally.
# Full list: https://developers.google.com/youtube/v3/docs/videoCategories/list
YOUTUBE_CATEGORY_IDS = {
    "gaming":        "20",   # Gaming
    "news":          "25",   # News & Politics
    "sports":        "17",   # Sports
    "entertainment": "24",   # Entertainment (also try "1" = Film & Animation)
    "tech":          "28",   # Science & Technology
    "music":         "10",   # Music (bonus category)
}

# How many trending videos to fetch per category (max 50)
MAX_RESULTS_PER_CATEGORY = 50


class YouTubeTrendingFetcher:
    """
    Fetches trending YouTube videos using the Data API v3.

    Why YouTube trends matter for VAULTCUT:
    - Shows exactly what content gets views right now
    - Gives us real video titles (good for clip title generation)
    - Shows view counts = popularity signals
    - Covers our exact categories (gaming/news/sports/entertainment/tech)
    """

    def __init__(self, api_key: str, region: str = "US"):
        """
        Args:
            api_key: Your YouTube Data API v3 key (free from Google Cloud Console)
            region: Country code. 'US' for United States.
        """
        self.api_key = api_key
        self.region = region
        self._service = None

    def _get_service(self):
        """
        Builds and returns the YouTube API client.
        Called lazily so import errors are clear.
        """
        if self._service is not None:
            return self._service

        try:
            from googleapiclient.discovery import build
            from googleapiclient.errors import HttpError

            self._service = build(
                "youtube", "v3",
                developerKey=self.api_key,
                cache_discovery=False,  # Avoids a file permission warning on Windows
            )
            return self._service

        except ImportError:
            logger.error("google-api-python-client not installed.")
            logger.error("Run: pip install google-api-python-client")
            return None
        except Exception as e:
            logger.error(f"Failed to build YouTube API client: {e}")
            return None

    def _check_api_key(self) -> bool:
        """Checks the API key looks valid before making requests."""
        if not self.api_key:
            logger.error("YouTube API key is empty. Fill it in config/settings.json")
            return False
        if self.api_key.startswith("YOUR_"):
            logger.error("YouTube API key is still a placeholder. Fill it in config/settings.json")
            return False
        return True

    def fetch_trending_by_category(self, category_name: str, category_id: str) -> list:
        """
        Fetches trending videos for one specific YouTube category.

        Args:
            category_name: Our internal name like 'gaming', 'news'
            category_id: YouTube's numeric category ID

        Returns:
            List of trend result dicts
        """
        service = self._get_service()
        if service is None:
            return []

        results = []
        try:
            logger.info(f"  Fetching YouTube trending: {category_name} (category_id={category_id})")

            # This is the YouTube API call.
            # videos().list() gets video details.
            # chart='mostPopular' means: the trending chart
            # videoCategoryId filters to a specific category
            request = service.videos().list(
                part="snippet,statistics",  # snippet=title/description, statistics=views/likes
                chart="mostPopular",
                regionCode=self.region,
                videoCategoryId=category_id,
                maxResults=MAX_RESULTS_PER_CATEGORY,
            )
            response = request.execute()

            videos = response.get("items", [])
            logger.info(f"  Got {len(videos)} trending videos for {category_name}")

            for rank, video in enumerate(videos):
                snippet = video.get("snippet", {})
                stats = video.get("statistics", {})

                title = snippet.get("title", "")
                channel_name = snippet.get("channelTitle", "")
                video_id = video.get("id", "")
                description = snippet.get("description", "")[:200]  # First 200 chars only

                # View count tells us how popular this video is
                view_count = int(stats.get("viewCount", 0))
                like_count = int(stats.get("likeCount", 0))

                # Convert view count to a 0-100 score
                # 10M views = 100, 1M = 80, 100k = 50, 10k = 20
                if view_count >= 10_000_000:
                    score = 100.0
                elif view_count >= 1_000_000:
                    score = 80.0 + (view_count - 1_000_000) / 1_000_000 * 20
                elif view_count >= 100_000:
                    score = 50.0 + (view_count - 100_000) / 100_000 * 30
                elif view_count >= 10_000:
                    score = 20.0 + (view_count - 10_000) / 10_000 * 30
                else:
                    score = max(5.0, view_count / 10_000 * 20)

                score = min(100.0, round(score, 1))

                # Use our classifier to double-check the category
                # (YouTube's categories are sometimes wrong)
                combined_text = f"{title} {channel_name} {description}"
                detected_category = classify(combined_text, default=category_name)

                results.append({
                    "keyword": title,
                    "score": score,
                    "category": detected_category,
                    "source": "youtube",
                    "region": self.region,
                    "timestamp": datetime.now().isoformat(),
                    "extra": json.dumps({
                        "video_id": video_id,
                        "channel": channel_name,
                        "view_count": view_count,
                        "like_count": like_count,
                        "rank": rank + 1,
                        "youtube_category": category_name,
                    }),
                })

        except Exception as e:
            # Check for common API errors
            error_str = str(e)
            if "quotaExceeded" in error_str:
                logger.error("YouTube API quota exceeded (10,000 units/day limit reached)")
                logger.error("Wait until midnight Pacific Time for quota to reset")
            elif "forbidden" in error_str.lower() or "403" in error_str:
                logger.error("YouTube API key rejected (403 Forbidden)")
                logger.error("Check your API key in settings.json and that YouTube Data API v3 is enabled")
            elif "keyInvalid" in error_str:
                logger.error("YouTube API key is invalid. Check settings.json")
            else:
                logger.error(f"YouTube trending fetch failed for {category_name}: {e}")

        return results

    def fetch_general_trending(self) -> list:
        """
        Fetches the overall YouTube trending page (not filtered by category).
        This catches crossover hits that don't fit neatly into one category.

        Returns:
            List of trend result dicts
        """
        service = self._get_service()
        if service is None:
            return []

        results = []
        try:
            logger.info("  Fetching YouTube general trending (all categories)")

            request = service.videos().list(
                part="snippet,statistics",
                chart="mostPopular",
                regionCode=self.region,
                maxResults=50,
                # No videoCategoryId = all categories mixed together
            )
            response = request.execute()

            videos = response.get("items", [])
            logger.info(f"  Got {len(videos)} general trending videos")

            for rank, video in enumerate(videos):
                snippet = video.get("snippet", {})
                stats = video.get("statistics", {})

                title = snippet.get("title", "")
                channel_name = snippet.get("channelTitle", "")
                video_id = video.get("id", "")
                view_count = int(stats.get("viewCount", 0))

                rank_score = max(5.0, 100.0 - (rank * 2.0))

                combined_text = f"{title} {channel_name}"
                category = classify(combined_text)

                results.append({
                    "keyword": title,
                    "score": rank_score,
                    "category": category,
                    "source": "youtube",
                    "region": self.region,
                    "timestamp": datetime.now().isoformat(),
                    "extra": json.dumps({
                        "video_id": video_id,
                        "channel": channel_name,
                        "view_count": view_count,
                        "rank": rank + 1,
                        "youtube_category": "general",
                    }),
                })

        except Exception as e:
            logger.error(f"YouTube general trending fetch failed: {e}")

        return results

    def fetch(self) -> list:
        """
        Main entry point — fetches trending videos from all YouTube categories.

        Returns:
            Combined list of all trend results
        """
        if not self._check_api_key():
            logger.warning("Skipping YouTube trends — API key not configured")
            return []

        logger.info("=== YouTube Trending: Starting fetch ===")
        all_results = []

        # Fetch general trending first (most important)
        general = self.fetch_general_trending()
        all_results.extend(general)

        # Then fetch each category
        for category_name, category_id in YOUTUBE_CATEGORY_IDS.items():
            category_results = self.fetch_trending_by_category(category_name, category_id)
            all_results.extend(category_results)

        # Deduplicate by keyword (same video might appear in multiple categories)
        seen_keywords = set()
        deduped = []
        for r in all_results:
            key = r["keyword"].lower().strip()
            if key not in seen_keywords:
                seen_keywords.add(key)
                deduped.append(r)

        logger.info(f"=== YouTube Trending: Done. {len(deduped)} unique results ===")
        return deduped


# ============================================================
# Self-test
# PowerShell: python src/trends/youtube_trending.py
# ============================================================
if __name__ == "__main__":
    import json as _json
    import os
    import sys
    
    # Change to project root directory
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    os.chdir(project_root)
    
    settings_path = "config/settings.json"
    
    if not os.path.exists(settings_path):
        print(f"ERROR: settings.json not found at {settings_path}")
        print(f"Current directory: {os.getcwd()}")
        sys.exit(1)
    
    with open(settings_path, "r") as f:
        settings = _json.load(f)

    # Try both possible key locations
    api_key = settings.get("youtube_api_key", "") or settings.get("youtube_api", {}).get("api_key", "")

    if not api_key or api_key.startswith("YOUR_"):
        print("ERROR: YouTube API key not set in config/settings.json")
        print("Get a free key at: https://console.cloud.google.com")
        sys.exit(1)

    print(f"Testing YouTube Trending Fetcher with key: {api_key[:8]}...")
    print("(Making real API requests — takes 10-20 seconds)")
    print()

    fetcher = YouTubeTrendingFetcher(api_key=api_key, region="US")
    results = fetcher.fetch()

    if results:
        print(f"\nFound {len(results)} trend results. Top 15:\n")
        for r in results[:15]:
            extra = {}
            try:
                extra = _json.loads(r.get("extra", "{}"))
            except Exception:
                pass
            views = extra.get("view_count", 0)
            views_str = f"{views/1_000_000:.1f}M views" if views >= 1_000_000 else f"{views/1_000:.0f}K views"
            print(f"  [{r['category']:15s}] {r['score']:5.1f}  {views_str:12s}  {r['keyword'][:60]}")
    else:
        print("No results returned. Check:")
        print("  1. API key is correct in config/settings.json")
        print("  2. YouTube Data API v3 is enabled in Google Cloud Console")
        print("  3. Check logs/errors.log for details")
