"""
VAULTCUT — Twitch Streamer Discovery
======================================
Automatically finds Twitch streamers in categories that match
current gaming trends.

Two modes:
  MODE A (with API): Uses Twitch Helix API with client_id + client_secret
                     from settings.json. Gets live viewer counts,
                     game categories, and top streamers.

  MODE B (no API):   Skips gracefully and logs a clear message.
                     Twitch streamers can still be added manually via
                     manage_channels.py.

The Twitch API is completely free — just register an app:
  https://dev.twitch.tv/console → Applications → Register Your Application
  Callback URL: http://localhost  (just type this, it doesn't need to work)

Usage:
    from src.discovery.twitch_discovery import TwitchDiscovery
    disc = TwitchDiscovery(settings)
    disc.run()
"""

import sys
import os
import json
import time
import requests
from datetime import datetime
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from src.logger import get_logger
from src.trends.trend_storage import TrendStorage
from src.discovery.channel_monitor import ChannelMonitor

logger = get_logger("discovery.twitch")

# Minimum live viewer count to consider a streamer worth monitoring
MIN_VIEWER_COUNT = 500

# Minimum score to auto-add a streamer
MIN_AUTO_ADD_SCORE = 4.0

# How many top streamers to examine per game category
STREAMERS_PER_CATEGORY = 10

# How many trending gaming keywords to process per run
MAX_GAMING_TRENDS = 8

# Twitch OAuth token endpoint
TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
TWITCH_API_BASE  = "https://api.twitch.tv/helix"


class TwitchDiscovery:
    """
    Finds Twitch streamers in gaming categories that match trending topics.

    If the Twitch API is not configured, this class skips gracefully
    without crashing — manual channel addition still works.
    """

    def __init__(self, settings: dict = None):
        """
        Args:
            settings: The full settings dict from settings.json.
                      If None, loads from file automatically.
        """
        self.settings = settings or self._load_settings()
        self.monitor = ChannelMonitor()
        self.trend_storage = TrendStorage()
        self._access_token = None
        self._token_expiry = 0

        twitch = self.settings.get("twitch_api", {})
        self.client_id = twitch.get("client_id", "")
        self.client_secret = twitch.get("client_secret", "")

    def _load_settings(self) -> dict:
        path = os.path.join(PROJECT_ROOT, "config", "settings.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _is_configured(self) -> bool:
        """Returns True only if real Twitch API credentials are in settings.json."""
        if not self.client_id or self.client_id.startswith("YOUR_"):
            return False
        if not self.client_secret or self.client_secret.startswith("YOUR_"):
            return False
        return True

    def _get_access_token(self) -> Optional[str]:
        """
        Gets a Twitch OAuth2 App Access Token.

        Twitch requires a token for every API request. App Access Tokens
        are for server-to-server calls (no user login needed).
        They expire after ~60 days but we cache them in memory per session.

        Returns:
            The access token string, or None on failure.
        """
        # Return cached token if it hasn't expired
        if self._access_token and time.time() < self._token_expiry:
            return self._access_token

        try:
            response = requests.post(
                TWITCH_TOKEN_URL,
                data={
                    "client_id":     self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type":    "client_credentials",
                },
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()

            self._access_token = data["access_token"]
            # expires_in is in seconds; subtract 60s buffer
            self._token_expiry = time.time() + data.get("expires_in", 3600) - 60

            logger.info("Twitch OAuth token obtained successfully")
            return self._access_token

        except requests.exceptions.ConnectionError:
            logger.error("Twitch: No internet connection")
        except Exception as e:
            logger.error(f"Twitch: Failed to get OAuth token: {e}")
            logger.error("Check your client_id and client_secret in settings.json")

        return None

    def _api_get(self, endpoint: str, params: dict) -> Optional[dict]:
        """
        Makes an authenticated GET request to the Twitch Helix API.

        Args:
            endpoint: API path, e.g. '/streams' or '/games'
            params:   Query parameters as a dict

        Returns:
            Parsed JSON response dict, or None on error.
        """
        token = self._get_access_token()
        if not token:
            return None

        headers = {
            "Authorization": f"Bearer {token}",
            "Client-Id":     self.client_id,
        }

        try:
            response = requests.get(
                f"{TWITCH_API_BASE}{endpoint}",
                headers=headers,
                params=params,
                timeout=15,
            )

            if response.status_code == 401:
                logger.warning("Twitch token expired — refreshing")
                self._access_token = None
                return self._api_get(endpoint, params)  # Retry once

            if response.status_code == 429:
                logger.warning("Twitch API rate limited — waiting 30 seconds")
                time.sleep(30)
                return None

            response.raise_for_status()
            return response.json()

        except Exception as e:
            logger.error(f"Twitch API call failed ({endpoint}): {e}")
            return None

    def _find_game_id(self, game_name: str) -> Optional[str]:
        """
        Looks up a Twitch game ID by name.
        The Twitch API needs a numeric game ID to search for streams.

        Args:
            game_name: Game name string, e.g. 'Fortnite'

        Returns:
            Twitch game ID string, or None if not found.
        """
        data = self._api_get("/games", {"name": game_name})
        if not data:
            return None
        games = data.get("data", [])
        if games:
            return games[0]["id"]
        return None

    def _score_streamer(self, viewer_count: int, stream_data: dict) -> float:
        """
        Scores a live Twitch stream on a 0-10 scale.

        Args:
            viewer_count: Current live viewer count
            stream_data:  The stream data dict from Twitch API

        Returns:
            Score from 0.0 to 10.0
        """
        # Viewer count scoring
        if viewer_count >= 50_000:
            score = 10.0
        elif viewer_count >= 10_000:
            score = 9.0
        elif viewer_count >= 5_000:
            score = 8.0
        elif viewer_count >= 1_000:
            score = 6.0
        elif viewer_count >= 500:
            score = 4.0
        else:
            score = 2.0

        return round(score, 2)

    def _get_top_streamers_for_game(self, game_id: str, game_name: str, category: str) -> list:
        """
        Gets the top live streams for a specific Twitch game.

        Args:
            game_id:   Twitch game ID
            game_name: Human-readable game name
            category:  VAULTCUT category (almost always 'gaming')

        Returns:
            List of streamer info dicts
        """
        data = self._api_get("/streams", {
            "game_id":   game_id,
            "first":     STREAMERS_PER_CATEGORY,
            "language":  "en",
        })

        if not data:
            return []

        streamers = []
        for stream in data.get("data", []):
            viewer_count = stream.get("viewer_count", 0)

            if viewer_count < MIN_VIEWER_COUNT:
                continue

            score = self._score_streamer(viewer_count, stream)
            username = stream.get("user_login", "").lower()
            display_name = stream.get("user_name", username)

            streamers.append({
                "username":      username,
                "channel_name":  display_name,
                "category":      category,
                "viewer_count":  viewer_count,
                "score":         score,
                "description":   f"Top Twitch streamer for {game_name} ({viewer_count:,} viewers)",
                "game_name":     game_name,
            })

            logger.info(f"    Found: {display_name} — {viewer_count:,} viewers, score={score}")

        return streamers

    def run(self) -> int:
        """
        Main entry point for Twitch auto-discovery.

        Reads trending gaming keywords → finds matching Twitch games →
        gets top streamers → adds them to monitored_channels.

        Returns:
            Number of new streamers added. Returns 0 if API not configured.
        """
        if not self._is_configured():
            logger.info(
                "Twitch API not configured — skipping auto-discovery. "
                "Add streamers manually: python manage_channels.py add twitch USERNAME. "
                "To enable: fill in twitch_api.client_id and twitch_api.client_secret in settings.json "
                "(free at: https://dev.twitch.tv/console)"
            )
            return 0

        logger.info("=== Twitch Discovery: Starting ===")

        # Get trending gaming keywords
        gaming_trends = self.trend_storage.get_top_trends(
            limit=MAX_GAMING_TRENDS,
            hours_back=24,
            category="gaming"
        )

        if not gaming_trends:
            logger.info("No gaming trends found — skipping Twitch discovery this run")
            return 0

        logger.info(f"Found {len(gaming_trends)} gaming trends to search on Twitch")

        all_streamers = []

        for trend in gaming_trends:
            # Extract the likely game name from the trend keyword
            # e.g. "Fortnite new season highlights" → try "Fortnite" first
            keyword = trend["keyword"]

            # Try the first word (usually the game title)
            game_name_guess = keyword.split()[0] if keyword else keyword
            logger.info(f"  Searching Twitch for game: '{game_name_guess}'")

            game_id = self._find_game_id(game_name_guess)
            if not game_id:
                # Try the full keyword
                game_id = self._find_game_id(keyword[:30])

            if not game_id:
                logger.info(f"    No Twitch game found for '{game_name_guess}' — skipping")
                time.sleep(1)
                continue

            streamers = self._get_top_streamers_for_game(game_id, game_name_guess, "gaming")
            all_streamers.extend(streamers)
            time.sleep(1)

        # Deduplicate by username
        seen = set()
        unique = []
        for s in all_streamers:
            if s["username"] not in seen:
                seen.add(s["username"])
                unique.append(s)

        logger.info(f"Found {len(unique)} unique streamer candidates")

        added = 0
        for s in unique:
            if s["score"] < MIN_AUTO_ADD_SCORE:
                continue

            row_id = self.monitor.add_twitch_streamer(
                username=s["username"],
                channel_name=s["channel_name"],
                category=s["category"],
                subscriber_count=s["viewer_count"],  # Using viewer count as proxy
                priority=s["score"],
                discovery_source="auto",
                description=s["description"],
            )

            if row_id:
                added += 1

        logger.info(f"=== Twitch Discovery: Done. {added} new streamers added ===")
        return added


# ─────────────────────────────────────────────────────────────
# FUTURE SOURCE PLACEHOLDERS
# To add TikTok discovery later:
#   class TikTokDiscovery:
#       def run(self): ...
#
# To add Instagram discovery later:
#   class InstagramDiscovery:
#       def run(self): ...
# ─────────────────────────────────────────────────────────────
