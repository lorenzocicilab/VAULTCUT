"""
VAULTCUT — Video Queue Builder
================================
For every monitored channel, checks YouTube/Twitch for new videos
and adds them to the downloaded_videos table with status='queued'.

This is the bridge between Phase 3 (discovery) and Phase 4 (downloading).
Phase 4 will read the 'queued' rows and actually download the files.

How it works:
  YouTube channels:
    - Calls YouTube Data API to get the most recent uploads
    - Only adds videos that aren't already in downloaded_videos
    - Filters by duration (must fit in 60-second clip = needs 30s minimum source)

  Twitch:
    - Calls Twitch API to get recent VODs (recorded past streams)
    - Same deduplication logic

Run schedule (set in main.py):
  - YouTube: every 1 hour
  - Twitch:  every 30 minutes

Usage:
    from src.discovery.video_queue import VideoQueue
    vq = VideoQueue(youtube_api_key="YOUR_KEY")
    vq.check_all_channels()
"""

import sys
import os
import json
import time
import requests
from datetime import datetime, timedelta
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from src.logger import get_logger
from src.database.init_db import get_connection
from src.discovery.channel_monitor import ChannelMonitor

logger = get_logger("discovery.queue")

# Maximum video duration to consider queueing (seconds)
# We need at least 30s to make a Shorts clip from it
MAX_VIDEO_DURATION = 7200    # 2 hours
MIN_VIDEO_DURATION = 30      # 30 seconds

# How many recent videos to check per channel per run
VIDEOS_TO_CHECK_PER_CHANNEL = 10

# Twitch VOD API settings
TWITCH_API_BASE = "https://api.twitch.tv/helix"


class VideoQueue:
    """
    Checks monitored channels for new content and adds it to the
    download queue (downloaded_videos table with download_status='queued').

    Phase 4 (Downloader) reads these queued rows and downloads the files.
    """

    def __init__(self, youtube_api_key: str = "", twitch_settings: dict = None):
        """
        Args:
            youtube_api_key: YouTube Data API v3 key
            twitch_settings: Dict with client_id and client_secret for Twitch
        """
        self.youtube_api_key = youtube_api_key
        self.twitch_settings = twitch_settings or {}
        self.monitor = ChannelMonitor()
        self._yt_service = None
        self._twitch_token = None
        self._twitch_token_expiry = 0

    # ── YouTube ───────────────────────────────────────────────

    def _get_yt_service(self):
        """Builds YouTube API client. Returns None if unavailable."""
        if self._yt_service:
            return self._yt_service
        if not self.youtube_api_key or self.youtube_api_key.startswith("YOUR_"):
            return None
        try:
            from googleapiclient.discovery import build
            self._yt_service = build(
                "youtube", "v3",
                developerKey=self.youtube_api_key,
                cache_discovery=False,
            )
            return self._yt_service
        except Exception as e:
            logger.error(f"YouTube API client failed: {e}")
            return None

    def _video_already_queued(self, source_type: str, video_id: str) -> bool:
        """
        Returns True if this video is already in the downloaded_videos table.
        This is how we avoid adding the same video twice.
        """
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT id FROM downloaded_videos WHERE source_type=? AND video_id=?",
                (source_type, video_id)
            ).fetchone()
            return row is not None
        except Exception:
            return False
        finally:
            conn.close()

    def _queue_video(
        self,
        source_type: str,
        video_id: str,
        channel_db_id: int,
        title: str,
        uploader: str,
        source_url: str,
        duration_seconds: int,
        source_category: str,
        published_at: str = "",
        view_count: int = 0,
        like_count: int = 0,
    ) -> bool:
        """
        Inserts a video into downloaded_videos with download_status='queued'.
        Phase 4 will pick it up from there.

        Returns True if the video was newly inserted, False if it already existed.
        """
        if self._video_already_queued(source_type, video_id):
            return False  # Already in queue or downloaded

        conn = get_connection()
        try:
            conn.execute("""
                INSERT INTO downloaded_videos
                    (source_type, video_id, channel_id, title, uploader,
                     source_url, duration_seconds, download_date, queued_date,
                     download_status, source_category, published_at,
                     view_count, like_count,
                     transcription_status, analysis_status, deleted)
                VALUES
                    (?, ?, ?, ?, ?,
                     ?, ?, ?, ?,
                     'queued', ?, ?,
                     ?, ?,
                     'pending', 'pending', 0)
            """, (
                source_type, video_id, channel_db_id, title, uploader,
                source_url, duration_seconds,
                datetime.now().isoformat(),
                datetime.now().isoformat(),
                source_category,
                published_at,
                view_count, like_count,
            ))
            conn.commit()
            logger.info(f"  Queued: [{source_type}] {title[:60]}")
            return True

        except Exception as e:
            logger.error(f"Failed to queue video '{title}': {e}")
            return False
        finally:
            conn.close()

    def _parse_duration_seconds(self, iso_duration: str) -> int:
        """
        Converts YouTube's ISO 8601 duration format to total seconds.

        YouTube returns durations like 'PT4M13S' (4 minutes 13 seconds)
        or 'PT1H30M' (1 hour 30 minutes).

        Args:
            iso_duration: YouTube duration string like 'PT4M13S'

        Returns:
            Duration in seconds as an integer
        """
        import re
        if not iso_duration:
            return 0
        hours   = int((re.search(r'(\d+)H', iso_duration) or [0, 0])[1])
        minutes = int((re.search(r'(\d+)M', iso_duration) or [0, 0])[1])
        seconds = int((re.search(r'(\d+)S', iso_duration) or [0, 0])[1])
        return (hours * 3600) + (minutes * 60) + seconds

    def check_youtube_channel(self, channel: dict) -> int:
        """
        Checks one YouTube channel for new videos and queues them.

        Args:
            channel: A monitored_channels row as a dict (from ChannelMonitor.list_by_source)

        Returns:
            Number of new videos added to the queue.
        """
        service = self._get_yt_service()
        if not service:
            return 0

        channel_db_id = channel["id"]
        channel_id    = channel.get("channel_id", "")
        channel_name  = channel.get("channel_name", "unknown")
        category      = channel.get("category", "entertainment")

        if not channel_id:
            logger.warning(f"Channel '{channel_name}' has no YouTube channel_id — skipping")
            return 0

        logger.info(f"  Checking YouTube: {channel_name}")
        queued = 0

        try:
            # Get the channel's uploads playlist ID
            # Every YouTube channel has a hidden "uploads" playlist
            # which contains all their videos in chronological order.
            ch_response = service.channels().list(
                part="contentDetails",
                id=channel_id,
            ).execute()

            items = ch_response.get("items", [])
            if not items:
                logger.warning(f"    No channel data returned for {channel_name}")
                return 0

            uploads_playlist_id = (
                items[0]
                .get("contentDetails", {})
                .get("relatedPlaylists", {})
                .get("uploads", "")
            )

            if not uploads_playlist_id:
                logger.warning(f"    Could not get uploads playlist for {channel_name}")
                return 0

            # Get the most recent videos from the uploads playlist
            playlist_response = service.playlistItems().list(
                part="snippet",
                playlistId=uploads_playlist_id,
                maxResults=VIDEOS_TO_CHECK_PER_CHANNEL,
            ).execute()

            video_ids = []
            video_meta = {}
            for item in playlist_response.get("items", []):
                snippet = item.get("snippet", {})
                vid_id = snippet.get("resourceId", {}).get("videoId", "")
                if vid_id:
                    video_ids.append(vid_id)
                    video_meta[vid_id] = {
                        "title":        snippet.get("title", ""),
                        "published_at": snippet.get("publishedAt", ""),
                    }

            if not video_ids:
                logger.info(f"    No videos found for {channel_name}")
                return 0

            # Get duration and view count for each video
            # (playlistItems only gives us title, we need a separate call for stats)
            details_response = service.videos().list(
                part="contentDetails,statistics",
                id=",".join(video_ids),
            ).execute()

            for video in details_response.get("items", []):
                vid_id    = video["id"]
                duration  = self._parse_duration_seconds(
                    video.get("contentDetails", {}).get("duration", "")
                )
                stats     = video.get("statistics", {})
                view_count = int(stats.get("viewCount", 0))
                like_count = int(stats.get("likeCount", 0))
                meta       = video_meta.get(vid_id, {})

                # Skip videos that are too short or too long
                if duration < MIN_VIDEO_DURATION:
                    logger.info(f"    Skip (too short {duration}s): {meta.get('title','?')[:40]}")
                    continue
                if duration > MAX_VIDEO_DURATION:
                    logger.info(f"    Skip (too long {duration}s): {meta.get('title','?')[:40]}")
                    continue

                newly_queued = self._queue_video(
                    source_type="youtube",
                    video_id=vid_id,
                    channel_db_id=channel_db_id,
                    title=meta.get("title", ""),
                    uploader=channel_name,
                    source_url=f"https://www.youtube.com/watch?v={vid_id}",
                    duration_seconds=duration,
                    source_category=category,
                    published_at=meta.get("published_at", ""),
                    view_count=view_count,
                    like_count=like_count,
                )

                if newly_queued:
                    queued += 1

            time.sleep(1)

        except Exception as e:
            error_str = str(e)
            if "quotaExceeded" in error_str:
                logger.error("YouTube API quota exceeded — stopping queue check for this run")
                return queued
            logger.error(f"    Error checking YouTube channel {channel_name}: {e}")

        self.monitor.mark_checked(channel_db_id)
        return queued

    # ── Twitch ────────────────────────────────────────────────

    def _get_twitch_token(self) -> Optional[str]:
        """Gets a Twitch OAuth token. Returns None if not configured."""
        client_id = self.twitch_settings.get("client_id", "")
        client_secret = self.twitch_settings.get("client_secret", "")

        if not client_id or client_id.startswith("YOUR_"):
            return None

        if self._twitch_token and time.time() < self._twitch_token_expiry:
            return self._twitch_token

        try:
            r = requests.post(
                "https://id.twitch.tv/oauth2/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": "client_credentials",
                },
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            self._twitch_token = data["access_token"]
            self._twitch_token_expiry = time.time() + data.get("expires_in", 3600) - 60
            return self._twitch_token
        except Exception as e:
            logger.error(f"Twitch token fetch failed: {e}")
            return None

    def check_twitch_streamer(self, channel: dict) -> int:
        """
        Checks one Twitch streamer for recent VODs and queues them.

        Args:
            channel: A monitored_channels row dict

        Returns:
            Number of new VODs added to the queue.
        """
        token = self._get_twitch_token()
        if not token:
            return 0

        client_id = self.twitch_settings.get("client_id", "")
        username   = channel.get("username", "")
        channel_db_id = channel["id"]
        channel_name = channel.get("channel_name", username)

        if not username:
            return 0

        logger.info(f"  Checking Twitch VODs: {channel_name}")
        queued = 0

        try:
            headers = {
                "Authorization": f"Bearer {token}",
                "Client-Id":     client_id,
            }

            # Get Twitch user ID from username
            user_resp = requests.get(
                f"{TWITCH_API_BASE}/users",
                headers=headers,
                params={"login": username},
                timeout=10,
            )
            user_data = user_resp.json().get("data", [])
            if not user_data:
                logger.warning(f"    Twitch user not found: {username}")
                return 0

            user_id = user_data[0]["id"]

            # Get recent VODs for this user
            vods_resp = requests.get(
                f"{TWITCH_API_BASE}/videos",
                headers=headers,
                params={
                    "user_id": user_id,
                    "type":    "archive",  # 'archive' = past streams (VODs)
                    "first":   VIDEOS_TO_CHECK_PER_CHANNEL,
                },
                timeout=10,
            )
            vods = vods_resp.json().get("data", [])
            logger.info(f"    Found {len(vods)} recent VODs")

            for vod in vods:
                vod_id   = vod.get("id", "")
                title    = vod.get("title", "")
                duration_str = vod.get("duration", "0s")  # Twitch gives "1h30m5s" format
                url      = vod.get("url", f"https://www.twitch.tv/videos/{vod_id}")
                created  = vod.get("created_at", "")

                # Parse Twitch duration format: "1h30m5s"
                import re
                hours   = int((re.search(r'(\d+)h', duration_str) or [0, 0])[1])
                minutes = int((re.search(r'(\d+)m', duration_str) or [0, 0])[1])
                seconds = int((re.search(r'(\d+)s', duration_str) or [0, 0])[1])
                duration = (hours * 3600) + (minutes * 60) + seconds

                if duration < MIN_VIDEO_DURATION:
                    continue
                if duration > MAX_VIDEO_DURATION:
                    continue

                newly_queued = self._queue_video(
                    source_type="twitch",
                    video_id=vod_id,
                    channel_db_id=channel_db_id,
                    title=title,
                    uploader=channel_name,
                    source_url=url,
                    duration_seconds=duration,
                    source_category="gaming",
                    published_at=created,
                )

                if newly_queued:
                    queued += 1

            time.sleep(1)

        except Exception as e:
            logger.error(f"    Error checking Twitch VODs for {channel_name}: {e}")

        self.monitor.mark_checked(channel_db_id)
        return queued

    # ── Master check ──────────────────────────────────────────

    def check_all_youtube_channels(self) -> int:
        """
        Checks every active YouTube channel for new videos.
        Called by the scheduler every hour.

        Returns:
            Total new videos queued across all channels.
        """
        logger.info("=== Video Queue: Checking YouTube channels ===")

        if not self.youtube_api_key or self.youtube_api_key.startswith("YOUR_"):
            logger.warning("YouTube API key not configured — skipping queue check")
            return 0

        channels = self.monitor.list_by_source("youtube", active_only=True)
        if not channels:
            logger.info("No YouTube channels being monitored yet.")
            logger.info("Add channels with: python manage_channels.py add youtube URL")
            return 0

        logger.info(f"Checking {len(channels)} YouTube channels for new videos...")

        total_queued = 0
        for channel in channels:
            new = self.check_youtube_channel(channel)
            total_queued += new

        logger.info(f"=== YouTube Queue: Done. {total_queued} new videos queued ===")
        return total_queued

    def check_all_twitch_streamers(self) -> int:
        """
        Checks every active Twitch streamer for new VODs.
        Called by the scheduler every 30 minutes.

        Returns:
            Total new VODs queued.
        """
        logger.info("=== Video Queue: Checking Twitch VODs ===")

        client_id = self.twitch_settings.get("client_id", "")
        if not client_id or client_id.startswith("YOUR_"):
            logger.info("Twitch API not configured — skipping. Add credentials to settings.json")
            return 0

        streamers = self.monitor.list_by_source("twitch", active_only=True)
        if not streamers:
            logger.info("No Twitch streamers being monitored yet.")
            logger.info("Add streamers with: python manage_channels.py add twitch USERNAME")
            return 0

        logger.info(f"Checking {len(streamers)} Twitch streamers for new VODs...")

        total_queued = 0
        for channel in streamers:
            new = self.check_twitch_streamer(channel)
            total_queued += new

        logger.info(f"=== Twitch Queue: Done. {total_queued} new VODs queued ===")
        return total_queued

    def get_queue_stats(self) -> dict:
        """
        Returns a summary of the current download queue state.
        Shows how many videos are waiting, downloading, done, etc.
        """
        conn = get_connection()
        try:
            rows = conn.execute("""
                SELECT download_status, COUNT(*) as count
                FROM downloaded_videos
                WHERE deleted = 0
                GROUP BY download_status
            """).fetchall()

            stats = {row["download_status"]: row["count"] for row in rows}
            return stats
        except Exception as e:
            logger.error(f"Failed to get queue stats: {e}")
            return {}
        finally:
            conn.close()
