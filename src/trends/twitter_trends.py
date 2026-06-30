"""
VAULTCUT — Twitter/X Trends Fetcher
======================================
STATUS: PLACEHOLDER — Twitter/X scraping is NOT implemented.

WHY IT WAS SKIPPED:
  Twitter/X made its API paid-only in 2023 ($100/month minimum).
  The free scraping library (snscrape) no longer works reliably
  after Twitter changed its login and anti-bot systems.

  Attempting to scrape Twitter without a session is blocked.
  Attempting to scrape WITH a session risks account banning.

  For a system running automatically 24/7, this is too unreliable.

ALTERNATIVES ALREADY COVERED:
  Google Trends  → catches what's trending in search
  YouTube        → shows what videos are going viral
  Reddit         → r/LivestreamFail, r/PublicFreakout, r/gaming
                   cover the same viral moment content Twitter does

HOW TO ADD TWITTER/X LATER (when you have a paid API plan):
  1. Sign up at: https://developer.twitter.com (Basic plan = $100/month)
  2. Get your Bearer Token from the developer dashboard
  3. Add to config/settings.json:
       "twitter_api": {
           "bearer_token": "YOUR_BEARER_TOKEN"
       }
  4. Replace the TwitterTrendsFetcher.fetch() stub below with:

     import tweepy
     client = tweepy.Client(bearer_token=self.bearer_token)
     trends = client.get_place_trends(id=1)  # 1 = Worldwide
     for trend in trends[0]:
         results.append({
             "keyword": trend["name"],
             "score": trend["tweet_volume"] or 1000,
             "category": classify(trend["name"]),
             "source": "twitter",
             "region": "global",
             "timestamp": datetime.now().isoformat(),
             "extra": trend["url"],
         })

The trend engine (engine.py) will call fetch() and get an empty
list from this stub — that's fine, it just skips Twitter silently.
"""

import sys
import os
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from src.logger import get_logger

logger = get_logger("trends.twitter")


class TwitterTrendsFetcher:
    """
    Placeholder class. Returns empty results.
    The trend engine handles the empty list gracefully.
    """

    def __init__(self, settings: dict = None):
        self.enabled = False
        logger.info("Twitter/X trends: DISABLED (see src/trends/twitter_trends.py for details)")

    def fetch(self) -> list:
        """Returns empty list. Twitter/X is not implemented."""
        return []


# ============================================================
# Self-test
# PowerShell: python src\trends\twitter_trends.py
# ============================================================
if __name__ == "__main__":
    print("Twitter/X Trends: DISABLED")
    print()
    print("This source was skipped because Twitter's API is now paid-only.")
    print("The system uses Google Trends, YouTube, and Reddit instead.")
    print()
    print("To add Twitter/X later: read the instructions at the top of this file.")
    fetcher = TwitterTrendsFetcher()
    results = fetcher.fetch()
    print(f"Results returned: {len(results)} (expected: 0)")
