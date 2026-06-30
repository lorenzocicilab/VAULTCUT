"""
VAULTCUT — Channel Monitor
============================
Handles adding, removing, listing, and updating channels in the
monitored_channels database table.

This module is used by:
  - manage_channels.py  (CLI tool you run manually)
  - youtube_discovery.py (auto-discovery writes here)
  - twitch_discovery.py  (auto-discovery writes here)
  - video_queue.py       (reads channels to check for new videos)

The monitored_channels table stores both YouTube channels and
Twitch streamers in the same table, distinguished by source_type.
"""

import sys
import os
from datetime import datetime
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from src.logger import get_logger
from src.database.init_db import get_connection

logger = get_logger("discovery.monitor")


class ChannelMonitor:
    """
    Manages the monitored_channels table.
    All reads and writes to that table go through this class.
    """

    # ── ADD CHANNELS ──────────────────────────────────────────

    def add_youtube_channel(
        self,
        channel_id: str,
        channel_name: str,
        channel_url: str = "",
        category: str = "entertainment",
        subscriber_count: int = 0,
        avg_views: float = 0.0,
        priority: float = 5.0,
        discovery_source: str = "manual",
        description: str = "",
    ) -> Optional[int]:
        """
        Adds a YouTube channel to the monitored_channels table.

        If the channel is already in the database, this returns the
        existing row's ID without inserting a duplicate.

        Args:
            channel_id:       YouTube channel ID (e.g. UCxxxxxxxxxxxxxxxx)
            channel_name:     Human-readable name (e.g. "MrBeast")
            channel_url:      Full URL to the channel
            category:         gaming/news/sports/entertainment/tech
            subscriber_count: Current subscriber count (0 if unknown)
            avg_views:        Average views per video (0.0 if unknown)
            priority:         Score 1-10 (higher = checked more often)
            discovery_source: 'manual' or 'auto'
            description:      Channel description (optional)

        Returns:
            The database row ID of the inserted or existing channel,
            or None if insert failed.
        """
        conn = get_connection()
        try:
            # Check if this channel already exists
            existing = conn.execute(
                "SELECT id FROM monitored_channels WHERE source_type='youtube' AND channel_id=?",
                (channel_id,)
            ).fetchone()

            if existing:
                logger.info(f"Channel already monitored: {channel_name} (id={existing['id']})")
                conn.close()
                return existing["id"]

            conn.execute("""
                INSERT INTO monitored_channels
                    (source_type, channel_id, channel_name, category, active,
                     added_by, date_added, discovery_source, priority,
                     subscriber_count, avg_views, channel_url, description,
                     total_videos_downloaded, total_clips_generated)
                VALUES
                    ('youtube', ?, ?, ?, 1,
                     ?, ?, ?, ?,
                     ?, ?, ?, ?,
                     0, 0)
            """, (
                channel_id, channel_name, category,
                discovery_source, datetime.now().isoformat(),
                discovery_source, priority,
                subscriber_count, avg_views, channel_url, description
            ))
            conn.commit()

            row_id = conn.execute(
                "SELECT id FROM monitored_channels WHERE source_type='youtube' AND channel_id=?",
                (channel_id,)
            ).fetchone()["id"]

            logger.info(f"Added YouTube channel: {channel_name} | cat={category} | priority={priority} | id={row_id}")
            return row_id

        except Exception as e:
            logger.error(f"Failed to add YouTube channel '{channel_name}': {e}")
            return None
        finally:
            conn.close()

    def add_twitch_streamer(
        self,
        username: str,
        channel_name: str = "",
        category: str = "gaming",
        subscriber_count: int = 0,
        priority: float = 5.0,
        discovery_source: str = "manual",
        description: str = "",
    ) -> Optional[int]:
        """
        Adds a Twitch streamer to the monitored_channels table.

        Args:
            username:    Twitch username (case-insensitive, stored as-is)
            channel_name: Display name (defaults to username if blank)
            category:    Almost always 'gaming' for Twitch
            subscriber_count: Approximate follower count
            priority:    Score 1-10
            discovery_source: 'manual' or 'auto'

        Returns:
            Database row ID of inserted or existing row, or None on error.
        """
        if not channel_name:
            channel_name = username

        conn = get_connection()
        try:
            existing = conn.execute(
                "SELECT id FROM monitored_channels WHERE source_type='twitch' AND username=?",
                (username.lower(),)
            ).fetchone()

            if existing:
                logger.info(f"Twitch streamer already monitored: {username} (id={existing['id']})")
                conn.close()
                return existing["id"]

            conn.execute("""
                INSERT INTO monitored_channels
                    (source_type, username, channel_name, category, active,
                     added_by, date_added, discovery_source, priority,
                     subscriber_count, channel_url, description,
                     total_videos_downloaded, total_clips_generated)
                VALUES
                    ('twitch', ?, ?, ?, 1,
                     ?, ?, ?, ?,
                     ?, ?, ?,
                     0, 0)
            """, (
                username.lower(), channel_name, category,
                discovery_source, datetime.now().isoformat(),
                discovery_source, priority,
                subscriber_count,
                f"https://twitch.tv/{username.lower()}",
                description
            ))
            conn.commit()

            row_id = conn.execute(
                "SELECT id FROM monitored_channels WHERE source_type='twitch' AND username=?",
                (username.lower(),)
            ).fetchone()["id"]

            logger.info(f"Added Twitch streamer: {username} | cat={category} | priority={priority} | id={row_id}")
            return row_id

        except Exception as e:
            logger.error(f"Failed to add Twitch streamer '{username}': {e}")
            return None
        finally:
            conn.close()

    # ── REMOVE / PAUSE ────────────────────────────────────────

    def remove_channel(self, channel_db_id: int) -> bool:
        """
        Marks a channel as inactive (active=0) instead of deleting it.
        This preserves the download history linked to this channel.

        Args:
            channel_db_id: The numeric ID from the monitored_channels table

        Returns:
            True if the channel was found and deactivated, False otherwise.
        """
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT channel_name, source_type FROM monitored_channels WHERE id=?",
                (channel_db_id,)
            ).fetchone()

            if not row:
                logger.warning(f"Cannot remove channel: ID {channel_db_id} not found in database")
                return False

            conn.execute(
                "UPDATE monitored_channels SET active=0 WHERE id=?",
                (channel_db_id,)
            )
            conn.commit()
            logger.info(f"Deactivated channel: {row['channel_name']} ({row['source_type']}) — id={channel_db_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to remove channel id={channel_db_id}: {e}")
            return False
        finally:
            conn.close()

    def reactivate_channel(self, channel_db_id: int) -> bool:
        """Sets active=1 for a previously deactivated channel."""
        conn = get_connection()
        try:
            conn.execute("UPDATE monitored_channels SET active=1 WHERE id=?", (channel_db_id,))
            conn.commit()
            logger.info(f"Reactivated channel id={channel_db_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to reactivate channel id={channel_db_id}: {e}")
            return False
        finally:
            conn.close()

    # ── READ / LIST ───────────────────────────────────────────

    def list_all(self, active_only: bool = True) -> list:
        """
        Returns all monitored channels as a list of dicts.

        Args:
            active_only: If True, only returns channels with active=1.
                         Set to False to see deactivated channels too.

        Returns:
            List of channel dicts with all database columns.
        """
        conn = get_connection()
        try:
            if active_only:
                rows = conn.execute("""
                    SELECT * FROM monitored_channels
                    WHERE active=1
                    ORDER BY priority DESC, date_added ASC
                """).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM monitored_channels
                    ORDER BY active DESC, priority DESC
                """).fetchall()

            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to list channels: {e}")
            return []
        finally:
            conn.close()

    def list_by_source(self, source_type: str, active_only: bool = True) -> list:
        """
        Returns channels filtered by source type.

        Args:
            source_type: 'youtube' or 'twitch'
            active_only: Only return active channels

        Returns:
            List of channel dicts
        """
        conn = get_connection()
        try:
            query = "SELECT * FROM monitored_channels WHERE source_type=?"
            params = [source_type]
            if active_only:
                query += " AND active=1"
            query += " ORDER BY priority DESC"

            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to list {source_type} channels: {e}")
            return []
        finally:
            conn.close()

    def get_channel_by_id(self, channel_db_id: int) -> Optional[dict]:
        """Returns one channel as a dict, or None if not found."""
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM monitored_channels WHERE id=?", (channel_db_id,)
            ).fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get channel id={channel_db_id}: {e}")
            return None
        finally:
            conn.close()

    # ── UPDATE ────────────────────────────────────────────────

    def mark_checked(self, channel_db_id: int):
        """Updates last_checked timestamp for a channel. Called after each video scan."""
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE monitored_channels SET last_checked=? WHERE id=?",
                (datetime.now().isoformat(), channel_db_id)
            )
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to update last_checked for id={channel_db_id}: {e}")
        finally:
            conn.close()

    def increment_downloads(self, channel_db_id: int, count: int = 1):
        """Increments total_videos_downloaded for a channel."""
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE monitored_channels SET total_videos_downloaded = total_videos_downloaded + ? WHERE id=?",
                (count, channel_db_id)
            )
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to increment downloads for channel id={channel_db_id}: {e}")
        finally:
            conn.close()

    def update_priority(self, channel_db_id: int, new_priority: float):
        """Updates the priority score for a channel (1.0 to 10.0)."""
        priority = max(1.0, min(10.0, new_priority))
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE monitored_channels SET priority=? WHERE id=?",
                (priority, channel_db_id)
            )
            conn.commit()
            logger.info(f"Updated priority for channel id={channel_db_id} → {priority}")
        except Exception as e:
            logger.error(f"Failed to update priority for id={channel_db_id}: {e}")
        finally:
            conn.close()

    def get_total_count(self) -> int:
        """Returns the total number of active monitored channels."""
        conn = get_connection()
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM monitored_channels WHERE active=1"
            ).fetchone()[0]
        except Exception:
            return 0
        finally:
            conn.close()
