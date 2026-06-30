"""
VAULTCUT — Google Trends Fetcher
===================================
Fetches currently trending topics from Google Trends using pytrends.
pytrends is a free, unofficial Python wrapper — no API key needed.

What it fetches:
  1. Daily trending searches (what people searched most today)
  2. Interest over time for VAULTCUT's content categories
     (to know which categories are hot right now)

pytrends can be rate-limited by Google if you call it too often.
The 12-hour scheduler in the trend engine handles this safely.

Usage:
    from src.trends.google_trends import GoogleTrendsFetcher
    fetcher = GoogleTrendsFetcher()
    results = fetcher.fetch()
    # results is a list of dicts, each with: keyword, score, category, source
"""

import time
import sys
import os
from datetime import datetime

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from src.logger import get_logger
from src.trends.category import classify

logger = get_logger("trends.google")


class GoogleTrendsFetcher:
    """
    Fetches trending topics from Google Trends.

    Google Trends shows what millions of people are searching for
    right now — very useful for knowing what content will get views.
    """

    def __init__(self, geo: str = "US", language: str = "en-US"):
        """
        Args:
            geo: Country code for trends. 'US' for United States.
                 Other options: 'GB' (UK), 'CA' (Canada), 'AU' (Australia)
            language: Language code for the request
        """
        self.geo = geo
        self.language = language
        self._pytrends = None

    def _get_client(self):
        """
        Creates and returns a pytrends client.
        Called lazily so the import error is helpful if pytrends is missing.
        """
        if self._pytrends is not None:
            return self._pytrends

        try:
            from pytrends.request import TrendReq
            # timeout=(connect_seconds, read_seconds)
            # retries=2 means try up to 3 times if it fails
            self._pytrends = TrendReq(
                hl=self.language,
                tz=0,               # UTC timezone
                timeout=(10, 30),
                retries=2,
                backoff_factor=0.5, # Wait 0.5s between retries
            )
            return self._pytrends
        except ImportError:
            logger.error("pytrends not installed. Run: pip install pytrends")
            return None

    def fetch_daily_trends(self) -> list:
        """
        Gets today's trending searches from Google.

        Google provides a list of the top ~20 topics that had the
        biggest search spikes today. Each topic comes with related
        news articles as context.

        Returns:
            List of dicts: [{"keyword": ..., "score": ..., "category": ..., "source": "google"}]
        """
        client = self._get_client()
        if client is None:
            return []

        results = []
        try:
            logger.info(f"Fetching Google daily trending searches for: {self.geo}")

            # trending_searches() returns a pandas DataFrame
            # It has one column called 0 containing the trending keywords
            df = client.trending_searches(pn=self.geo.lower())

            if df is None or df.empty:
                logger.warning("Google Trends returned empty results")
                return []

            # df[0] is the column with keyword strings
            keywords = df[0].tolist()
            logger.info(f"Got {len(keywords)} trending searches from Google")

            for i, keyword in enumerate(keywords):
                keyword = str(keyword).strip()
                if not keyword:
                    continue

                # Google doesn't give a numerical score for daily trends,
                # so we invent a rank-based score: #1 = score 100, #20 = score 5
                # Higher ranked = more searches = higher score
                rank_score = max(5.0, 100.0 - (i * 5.0))

                category = classify(keyword)

                results.append({
                    "keyword": keyword,
                    "score": rank_score,
                    "category": category,
                    "source": "google",
                    "region": self.geo,
                    "timestamp": datetime.now().isoformat(),
                    "extra": f"rank_{i+1}_of_{len(keywords)}",
                })

            # Be polite — wait a moment before the next API call
            time.sleep(2)

        except Exception as e:
            logger.error(f"Google daily trends fetch failed: {e}")
            # Don't crash — just return empty and let other sources fill in
            return []

        return results

    def fetch_category_interest(self) -> list:
        """
        Checks how much interest each VAULTCUT category has on Google right now.

        This tells us whether, for example, gaming is more popular than sports
        this week, so we can prioritize what content to make.

        It does this by searching for representative keywords for each category
        and checking the "interest over time" score (0-100).

        Returns:
            List of dicts with category interest scores
        """
        client = self._get_client()
        if client is None:
            return []

        # Representative keywords for each category
        # pytrends can compare up to 5 keywords at once
        category_keywords = {
            "gaming":        "gaming highlights",
            "news":          "breaking news today",
            "sports":        "sports highlights",
            "entertainment": "viral video",
            "tech":          "tech news",
        }

        results = []
        try:
            logger.info("Fetching Google Trends category interest scores...")

            keywords_list = list(category_keywords.values())
            categories_list = list(category_keywords.keys())

            # Build a payload — tells pytrends what to search for
            # timeframe='now 1-d' means: last 24 hours
            client.build_payload(
                kw_list=keywords_list,
                cat=0,          # All categories
                timeframe="now 7-d",  # Last 7 days for more stable data
                geo=self.geo,
            )

            # interest_over_time() returns a DataFrame where:
            # - rows = time points
            # - columns = each keyword
            # - values = interest score 0-100
            df = client.interest_over_time()

            if df is None or df.empty:
                logger.warning("Google Trends interest_over_time returned empty")
                return []

            # Average each keyword's score over the time period
            # This gives us one number per category
            for kw, category in zip(keywords_list, categories_list):
                if kw in df.columns:
                    avg_score = float(df[kw].mean())
                    results.append({
                        "keyword": f"{category} (category interest)",
                        "score": round(avg_score, 1),
                        "category": category,
                        "source": "google",
                        "region": self.geo,
                        "timestamp": datetime.now().isoformat(),
                        "extra": "category_interest_7d",
                    })
                    logger.info(f"  {category}: {avg_score:.1f}/100")

            time.sleep(2)

        except Exception as e:
            logger.error(f"Google category interest fetch failed: {e}")
            return []

        return results

    def fetch(self) -> list:
        """
        Main entry point — runs all Google Trends checks and returns combined results.

        Returns:
            Combined list of trend results from daily trends + category interest
        """
        logger.info("=== Google Trends: Starting fetch ===")
        all_results = []

        # 1. Get today's trending searches
        daily = self.fetch_daily_trends()
        all_results.extend(daily)
        logger.info(f"Daily trends: {len(daily)} results")

        # Small pause between API calls to be polite
        time.sleep(3)

        # 2. Get category interest scores
        category = self.fetch_category_interest()
        all_results.extend(category)
        logger.info(f"Category interest: {len(category)} results")

        logger.info(f"=== Google Trends: Done. Total: {len(all_results)} results ===")
        return all_results


# ============================================================
# Self-test
# PowerShell: python src\trends\google_trends.py
# ============================================================
if __name__ == "__main__":
    print("Testing Google Trends Fetcher...")
    print("(This makes real network requests — takes 10-30 seconds)")
    print()

    fetcher = GoogleTrendsFetcher(geo="US")
    results = fetcher.fetch()

    if results:
        print(f"\nFound {len(results)} trend results:\n")
        for r in results[:10]:  # Show first 10
            print(f"  [{r['category']:15s}] score={r['score']:5.1f}  {r['keyword']}")
        if len(results) > 10:
            print(f"  ... and {len(results)-10} more")
    else:
        print("No results — check internet connection or pytrends installation")
        print("Install: pip install pytrends")
