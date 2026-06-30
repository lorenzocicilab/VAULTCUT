"""
VAULTCUT — Trend Storage
==========================
Saves trend results into the SQLite database (trend_history table)
and provides functions to read them back out.

This module is used by engine.py after collecting trends from
all sources (Google, YouTube, Reddit).

Usage:
    from src.trends.trend_storage import TrendStorage
    storage = TrendStorage()
    count = storage.save_trends(results)      # Save a list of trend dicts
    top10  = storage.get_top_trends(limit=10) # Get today's top trends
"""

import sys
import os
import json
import sqlite3
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from src.logger import get_logger

logger = get_logger("trends.storage")

# Path to the database file
DB_PATH = os.path.join(PROJECT_ROOT, "data", "vaultcut.db")

# Fallback to root if data/ path doesn't exist
if not os.path.exists(os.path.dirname(DB_PATH)):
    DB_PATH = os.path.join(PROJECT_ROOT, "vaultcut.db")


def _get_connection() -> sqlite3.Connection:
    """Opens a connection to the SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # Rows behave like dicts
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


class TrendStorage:
    """
    Handles reading and writing trend data to the SQLite database.

    The trend_history table schema (from init_db.py):
        id            INTEGER  - auto ID
        check_date    TEXT     - ISO datetime of when this was recorded
        source        TEXT     - 'google', 'youtube', 'reddit', 'twitter'
        trend_keyword TEXT     - the topic/title/keyword
        trend_score   REAL     - popularity score (0-100)
        category      TEXT     - gaming/news/sports/entertainment/tech
        region        TEXT     - 'US', 'global', etc.
    """

    def save_trends(self, trends: list) -> int:
        """
        Saves a list of trend results to the database.

        Each item in the list must be a dict with these keys
        (as produced by the fetcher classes):
            keyword   - the trend topic
            score     - popularity score 0-100
            category  - gaming/news/sports/entertainment/tech
            source    - google/youtube/reddit/twitter
            region    - US/global/etc.
            timestamp - ISO datetime string

        Duplicate entries (same source + keyword within 1 hour)
        are skipped to avoid filling the database with repeated data.

        Args:
            trends: List of trend dicts from the fetcher classes

        Returns:
            Number of new rows actually inserted
        """
        if not trends:
            logger.warning("save_trends called with empty list — nothing to save")
            return 0

        conn = _get_connection()
        inserted = 0
        skipped = 0

        try:
            cursor = conn.cursor()

            # Get the cutoff time for deduplication
            # We skip any trend that was already stored in the last 1 hour
            one_hour_ago = (datetime.now() - timedelta(hours=1)).isoformat()

            for trend in trends:
                keyword = str(trend.get("keyword", "")).strip()
                if not keyword:
                    continue

                source = trend.get("source", "unknown")
                score = float(trend.get("score", 0.0))
                category = trend.get("category", "entertainment")
                region = trend.get("region", "US")
                timestamp = trend.get("timestamp", datetime.now().isoformat())

                # Check for duplicate: same source + same keyword in last hour
                cursor.execute("""
                    SELECT id FROM trend_history
                    WHERE source = ?
                      AND trend_keyword = ?
                      AND check_date > ?
                    LIMIT 1
                """, (source, keyword, one_hour_ago))

                if cursor.fetchone():
                    skipped += 1
                    continue

                # Insert the new trend
                cursor.execute("""
                    INSERT INTO trend_history
                        (check_date, source, trend_keyword, trend_score, category, region)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (timestamp, source, keyword, score, category, region))

                inserted += 1

            conn.commit()
            logger.info(f"Trend storage: {inserted} inserted, {skipped} skipped (duplicates)")

        except sqlite3.OperationalError as e:
            logger.error(f"Database error saving trends: {e}")
            logger.error(f"Database path: {DB_PATH}")
            logger.error("Run 'python src/database/init_db.py' to create the database")
        except Exception as e:
            logger.error(f"Unexpected error saving trends: {e}")
        finally:
            conn.close()

        return inserted

    def get_top_trends(self, limit: int = 10, hours_back: int = 24, category: str = None) -> list:
        """
        Returns the top trending topics from the last N hours.

        Results are averaged across all sources so a topic that
        trends on BOTH Google AND Reddit ranks higher than one
        that only appeared on Reddit.

        Args:
            limit: How many trends to return
            hours_back: Look at trends from the last N hours (default 24)
            category: Filter to one category, or None for all

        Returns:
            List of dicts: [{keyword, avg_score, category, sources, count}]
        """
        conn = _get_connection()
        results = []

        try:
            cutoff = (datetime.now() - timedelta(hours=hours_back)).isoformat()

            if category:
                query = """
                    SELECT
                        trend_keyword,
                        AVG(trend_score)        AS avg_score,
                        MAX(trend_score)        AS max_score,
                        category,
                        COUNT(*)                AS appearances,
                        GROUP_CONCAT(DISTINCT source) AS sources
                    FROM trend_history
                    WHERE check_date > ?
                      AND category = ?
                    GROUP BY LOWER(trend_keyword)
                    ORDER BY avg_score DESC, appearances DESC
                    LIMIT ?
                """
                rows = conn.execute(query, (cutoff, category, limit)).fetchall()
            else:
                query = """
                    SELECT
                        trend_keyword,
                        AVG(trend_score)        AS avg_score,
                        MAX(trend_score)        AS max_score,
                        category,
                        COUNT(*)                AS appearances,
                        GROUP_CONCAT(DISTINCT source) AS sources
                    FROM trend_history
                    WHERE check_date > ?
                    GROUP BY LOWER(trend_keyword)
                    ORDER BY avg_score DESC, appearances DESC
                    LIMIT ?
                """
                rows = conn.execute(query, (cutoff, limit)).fetchall()

            for row in rows:
                results.append({
                    "keyword":     row["trend_keyword"],
                    "avg_score":   round(row["avg_score"], 1),
                    "max_score":   round(row["max_score"], 1),
                    "category":    row["category"],
                    "appearances": row["appearances"],
                    "sources":     row["sources"],
                })

        except Exception as e:
            logger.error(f"Error reading top trends: {e}")
        finally:
            conn.close()

        return results

    def get_trends_by_category(self, hours_back: int = 24) -> dict:
        """
        Returns a dict of {category: [top trends]} for all categories.
        Useful for the summary report.

        Returns:
            {"gaming": [...], "news": [...], "sports": [...], ...}
        """
        categories = ["gaming", "news", "sports", "entertainment", "tech"]
        result = {}
        for cat in categories:
            result[cat] = self.get_top_trends(limit=5, hours_back=hours_back, category=cat)
        return result

    def get_source_stats(self, hours_back: int = 24) -> dict:
        """
        Returns how many trends each source contributed in the last N hours.
        Useful for debugging if one source is down.

        Returns:
            {"google": 20, "youtube": 45, "reddit": 120, "twitter": 0}
        """
        conn = _get_connection()
        stats = {}
        try:
            cutoff = (datetime.now() - timedelta(hours=hours_back)).isoformat()
            rows = conn.execute("""
                SELECT source, COUNT(*) as count
                FROM trend_history
                WHERE check_date > ?
                GROUP BY source
            """, (cutoff,)).fetchall()

            for row in rows:
                stats[row["source"]] = row["count"]
        except Exception as e:
            logger.error(f"Error reading source stats: {e}")
        finally:
            conn.close()

        return stats

    def get_total_stored(self) -> int:
        """Returns total number of trend records in the database."""
        conn = _get_connection()
        try:
            count = conn.execute("SELECT COUNT(*) FROM trend_history").fetchone()[0]
            return count
        except Exception:
            return 0
        finally:
            conn.close()

    def cleanup_old_trends(self, days_to_keep: int = 30):
        """
        Deletes trend records older than N days to keep the database small.
        Called automatically by the engine at the end of each run.

        Args:
            days_to_keep: How many days of history to keep (default 30)
        """
        conn = _get_connection()
        try:
            cutoff = (datetime.now() - timedelta(days=days_to_keep)).isoformat()
            cursor = conn.execute(
                "DELETE FROM trend_history WHERE check_date < ?", (cutoff,)
            )
            conn.commit()
            deleted = cursor.rowcount
            if deleted > 0:
                logger.info(f"Cleaned up {deleted} old trend records (older than {days_to_keep} days)")
        except Exception as e:
            logger.error(f"Error cleaning up trends: {e}")
        finally:
            conn.close()
