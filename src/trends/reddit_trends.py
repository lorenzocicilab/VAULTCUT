"""
VAULTCUT — Reddit Trends Fetcher
====================================
Fetches hot/trending posts from Reddit's most relevant subreddits.

TWO MODES — works either way:

  MODE 1 (default): No API key needed.
    Uses Reddit's public JSON endpoints. Any Reddit page can be
    accessed as JSON by adding ".json" to the URL. No authentication,
    no rate limiting beyond a User-Agent header.
    Example: https://www.reddit.com/r/gaming/hot.json

  MODE 2: With PRAW (Reddit's official Python API).
    More reliable, higher rate limits, access to more data.
    Enable by filling in reddit_api settings in settings.json.

The module automatically uses MODE 2 if credentials are configured,
and falls back to MODE 1 if they're not.

Subreddits monitored: defined in config/settings.json under
trend_engine.reddit_subreddits

Usage:
    from src.trends.reddit_trends import RedditTrendsFetcher
    fetcher = RedditTrendsFetcher()
    results = fetcher.fetch()
"""

import sys
import os
import time
import json
import requests
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from src.logger import get_logger
from src.trends.category import classify

logger = get_logger("trends.reddit")

# Default subreddits to monitor if settings.json doesn't specify any
DEFAULT_SUBREDDITS = [
    "gaming", "news", "sports", "entertainment", "technology",
    "nba", "soccer", "esports", "LivestreamFail", "PublicFreakout",
    "worldnews", "movies", "television", "music", "science",
]

# How many posts to fetch per subreddit
POSTS_PER_SUBREDDIT = 25

# Minimum score (upvotes) for a post to count as trending
MIN_SCORE = 100

# Delay between subreddit requests (be polite to Reddit's servers)
REQUEST_DELAY_SECONDS = 2


class RedditTrendsFetcher:
    """
    Fetches trending posts from Reddit.

    Reddit is a great trend source because:
    - Posts on r/gaming often predict gaming content that will go viral
    - r/PublicFreakout and r/LivestreamFail are huge clip sources
    - Upvote scores tell us exactly how much people care about something
    """

    def __init__(self, settings: dict = None):
        """
        Args:
            settings: The full settings dict from settings.json.
                      If None, loads from file automatically.
        """
        self.settings = settings or self._load_settings()
        self.subreddits = (
            self.settings.get("trend_engine", {}).get("reddit_subreddits")
            or DEFAULT_SUBREDDITS
        )
        self._praw_reddit = None
        self._use_praw = self._can_use_praw()

        if self._use_praw:
            logger.info("Reddit: Using PRAW (official API with credentials)")
        else:
            logger.info("Reddit: Using public JSON endpoints (no API key needed)")

    def _load_settings(self) -> dict:
        settings_path = os.path.join(PROJECT_ROOT, "config", "settings.json")
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _can_use_praw(self) -> bool:
        """Returns True if PRAW is installed AND Reddit API credentials are configured."""
        try:
            import praw  # noqa: F401
        except ImportError:
            return False

        reddit_settings = self.settings.get("reddit_api", {})
        client_id = reddit_settings.get("client_id", "")
        client_secret = reddit_settings.get("client_secret", "")

        if not client_id or client_id.startswith("YOUR_"):
            return False
        if not client_secret or client_secret.startswith("YOUR_"):
            return False

        return True

    def _get_praw_client(self):
        """Creates a PRAW Reddit client using credentials from settings.json."""
        if self._praw_reddit is not None:
            return self._praw_reddit

        import praw
        reddit_settings = self.settings.get("reddit_api", {})

        self._praw_reddit = praw.Reddit(
            client_id=reddit_settings["client_id"],
            client_secret=reddit_settings["client_secret"],
            user_agent=reddit_settings.get("user_agent", "VAULTCUT Trend Bot 1.0"),
            check_for_async=False,
        )
        return self._praw_reddit

    # ===========================================================
    # MODE 1: Public JSON (no API key)
    # ===========================================================

    def _fetch_subreddit_public(self, subreddit: str) -> list:
        """
        Fetches hot posts from a subreddit using Reddit's public JSON API.

        Reddit returns JSON data from any page if you add .json to the URL.
        No login, no API key. Just a normal HTTPS request.

        Args:
            subreddit: Subreddit name without r/, e.g. 'gaming'

        Returns:
            List of post dicts with title, score, url, etc.
        """
        url = f"https://www.reddit.com/r/{subreddit}/hot.json"
        params = {
            "limit": POSTS_PER_SUBREDDIT,
            "t": "day",   # Time filter: day = posts from last 24h
        }
        # Reddit blocks requests without a User-Agent header
        headers = {
            "User-Agent": "VAULTCUT-TrendBot/1.0 (automated trend monitoring)",
        }

        try:
            response = requests.get(
                url, params=params, headers=headers, timeout=15
            )

            if response.status_code == 429:
                # 429 = Too Many Requests. Wait and the scheduler will retry later.
                logger.warning(f"Reddit rate limited on r/{subreddit}. Will retry next run.")
                return []

            if response.status_code == 403:
                logger.warning(f"r/{subreddit} is private or banned. Skipping.")
                return []

            if response.status_code != 200:
                logger.warning(f"r/{subreddit} returned HTTP {response.status_code}")
                return []

            data = response.json()
            posts = data.get("data", {}).get("children", [])

            results = []
            for post_wrapper in posts:
                post = post_wrapper.get("data", {})

                title = post.get("title", "").strip()
                score = post.get("score", 0)         # Upvotes
                num_comments = post.get("num_comments", 0)
                url_post = post.get("url", "")
                is_video = post.get("is_video", False)

                if not title or score < MIN_SCORE:
                    continue

                # Reddit engagement score:
                # Upvotes are the main signal. Comments add weight (engaged community).
                # We normalize to 0-100 scale.
                # 50,000 upvotes = 100; 10,000 = 80; 1,000 = 50; 100 = 10
                if score >= 50_000:
                    trend_score = 100.0
                elif score >= 10_000:
                    trend_score = 80.0 + (score - 10_000) / 10_000 * 20
                elif score >= 1_000:
                    trend_score = 50.0 + (score - 1_000) / 1_000 * 30
                else:
                    trend_score = max(10.0, score / 100 * 10)

                # Boost score if post has lots of comments
                # (lots of comments = people are talking about it)
                if num_comments > 1000:
                    trend_score = min(100.0, trend_score * 1.1)

                trend_score = round(min(100.0, trend_score), 1)

                # Build full text for category detection
                context = f"{title} r/{subreddit}"
                category = classify(context, default="entertainment")

                results.append({
                    "keyword": title,
                    "score": trend_score,
                    "category": category,
                    "source": "reddit",
                    "region": "global",
                    "timestamp": datetime.now().isoformat(),
                    "extra": json.dumps({
                        "subreddit": subreddit,
                        "upvotes": score,
                        "comments": num_comments,
                        "is_video": is_video,
                        "post_url": url_post,
                        "mode": "public_json",
                    }),
                })

            return results

        except requests.exceptions.ConnectionError:
            logger.warning(f"r/{subreddit}: No internet connection")
            return []
        except requests.exceptions.Timeout:
            logger.warning(f"r/{subreddit}: Request timed out")
            return []
        except Exception as e:
            logger.error(f"r/{subreddit}: Unexpected error: {e}")
            return []

    # ===========================================================
    # MODE 2: PRAW (official API)
    # ===========================================================

    def _fetch_subreddit_praw(self, subreddit: str) -> list:
        """
        Fetches hot posts using PRAW (official Reddit API).
        Only called when PRAW is installed and credentials are configured.

        Args:
            subreddit: Subreddit name without r/

        Returns:
            List of trend result dicts
        """
        reddit = self._get_praw_client()
        results = []

        try:
            sub = reddit.subreddit(subreddit)
            posts = list(sub.hot(limit=POSTS_PER_SUBREDDIT))

            for post in posts:
                if post.score < MIN_SCORE:
                    continue
                if post.stickied:  # Skip pinned mod posts
                    continue

                score = post.score
                num_comments = post.num_comments

                if score >= 50_000:
                    trend_score = 100.0
                elif score >= 10_000:
                    trend_score = 80.0 + (score - 10_000) / 10_000 * 20
                elif score >= 1_000:
                    trend_score = 50.0 + (score - 1_000) / 1_000 * 30
                else:
                    trend_score = max(10.0, score / 100 * 10)

                if num_comments > 1000:
                    trend_score = min(100.0, trend_score * 1.1)

                trend_score = round(min(100.0, trend_score), 1)

                context = f"{post.title} r/{subreddit}"
                category = classify(context, default="entertainment")

                results.append({
                    "keyword": post.title,
                    "score": trend_score,
                    "category": category,
                    "source": "reddit",
                    "region": "global",
                    "timestamp": datetime.now().isoformat(),
                    "extra": json.dumps({
                        "subreddit": subreddit,
                        "upvotes": score,
                        "comments": num_comments,
                        "is_video": post.is_video,
                        "post_url": f"https://reddit.com{post.permalink}",
                        "mode": "praw",
                    }),
                })

        except Exception as e:
            logger.error(f"PRAW fetch failed for r/{subreddit}: {e}")

        return results

    # ===========================================================
    # Main fetch
    # ===========================================================

    def fetch(self) -> list:
        """
        Fetches trending posts from all configured subreddits.
        Automatically uses PRAW if credentials are set, otherwise public JSON.

        Returns:
            Combined list of trend results from all subreddits
        """
        logger.info(f"=== Reddit Trends: Starting fetch ({len(self.subreddits)} subreddits) ===")

        if self._use_praw:
            logger.info("Mode: PRAW (official API)")
        else:
            logger.info("Mode: Public JSON (no API key — add Reddit credentials to settings.json for more data)")

        all_results = []

        for i, subreddit in enumerate(self.subreddits):
            logger.info(f"  [{i+1}/{len(self.subreddits)}] Fetching r/{subreddit}...")

            if self._use_praw:
                posts = self._fetch_subreddit_praw(subreddit)
            else:
                posts = self._fetch_subreddit_public(subreddit)

            all_results.extend(posts)
            logger.info(f"    → {len(posts)} posts found")

            # Pause between requests so Reddit doesn't block us
            if i < len(self.subreddits) - 1:
                time.sleep(REQUEST_DELAY_SECONDS)

        # Sort by score descending so the best trends are first
        all_results.sort(key=lambda x: x["score"], reverse=True)

        logger.info(f"=== Reddit Trends: Done. Total: {len(all_results)} results ===")
        return all_results


# ============================================================
# Self-test
# PowerShell: python src\trends\reddit_trends.py
# ============================================================
if __name__ == "__main__":
    print("Testing Reddit Trends Fetcher...")
    print("Mode: Public JSON (no API key needed)")
    print("(Fetching from Reddit — takes 20-60 seconds)")
    print()

    fetcher = RedditTrendsFetcher()

    # Just test with 3 subreddits to keep it quick
    fetcher.subreddits = ["gaming", "sports", "technology"]

    results = fetcher.fetch()

    if results:
        print(f"\nFound {len(results)} trending posts. Top 15:\n")
        for r in results[:15]:
            extra = {}
            try:
                extra = json.loads(r.get("extra", "{}"))
            except Exception:
                pass
            upvotes = extra.get("upvotes", 0)
            sub = extra.get("subreddit", "?")
            print(f"  [{r['category']:15s}] {upvotes:7,} ↑  r/{sub:<20} {r['keyword'][:55]}")
    else:
        print("No results returned. Check your internet connection.")
        print("Logs: logs/errors.log")
