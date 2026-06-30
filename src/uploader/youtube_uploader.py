import os
import json
import sqlite3
from datetime import datetime
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
from src.uploader.youtube_auth import get_credentials
from src.uploader.channel_config import get_channel_config
from src.logger import get_system_logger

logger = get_system_logger()

DB_PATH = 'data/vaultcut.db'


def load_settings():
    with open('config/settings.json', 'r', encoding='utf-8') as f:
        return json.load(f)


def upload_video(clip_id, channel_name):
    """Upload a clip to YouTube. Returns (success, video_id_or_error)."""
    logger.info(f"Starting upload: clip {clip_id} to {channel_name}")

    try:
        settings = load_settings()
    except Exception as e:
        return False, f"Failed to load settings: {e}"

    privacy = settings.get('upload_privacy', 'unlisted')

    config = get_channel_config(channel_name)
    if not config:
        return False, f"No config found for channel: {channel_name}"

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        clip = conn.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
        conn.close()
    except Exception as e:
        return False, f"Database error reading clip: {e}"

    if not clip:
        return False, f"Clip {clip_id} not found in database"

    clip = dict(clip)
    file_path = clip.get('file_path', '')

    if not file_path or not os.path.exists(file_path):
        return False, f"Clip file not found: {file_path}"

    title = clip.get('generated_title') or clip.get('title') or 'VAULTCUT Clip'
    description = clip.get('generated_description') or 'VAULTCUT'

    hashtags_json = clip.get('generated_hashtags', '[]')
    try:
        hashtags = json.loads(hashtags_json) if hashtags_json else []
    except Exception:
        hashtags = []

    tags = [tag.strip('#') for tag in hashtags if tag]
    tags.extend(config.get('default_tags', []))
    tags = list(set(tags))[:30]

    try:
        creds = get_credentials(channel_name)
        if not creds:
            return False, "Failed to get YouTube credentials"

        youtube = build('youtube', 'v3', credentials=creds)

        body = {
            'snippet': {
                'title': title[:100],
                'description': description[:5000],
                'tags': tags,
                'categoryId': config['category_id']
            },
            'status': {
                'privacyStatus': privacy,
                'selfDeclaredMadeForKids': False
            }
        }

        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        logger.info(f"Uploading {file_path} ({file_size_mb:.1f}MB) as '{title}'")

        media = MediaFileUpload(
            file_path,
            chunksize=-1,
            resumable=True,
            mimetype='video/mp4'
        )

        request = youtube.videos().insert(
            part='snippet,status',
            body=body,
            media_body=media
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info(f"Upload progress: {int(status.progress() * 100)}%")

        video_id = response.get('id')
        if video_id:
            youtube_url = f"https://youtube.com/shorts/{video_id}"
            logger.info(f"Upload complete: {youtube_url}")
            update_clip_uploaded(clip_id, video_id, youtube_url, privacy)
            track_quota(channel_name, 1600)
            return True, video_id
        else:
            return False, f"No video ID in response: {response}"

    except HttpError as e:
        try:
            error_detail = e.content.decode('utf-8', errors='replace')[:200]
        except Exception:
            error_detail = str(e)
        error_msg = f"YouTube API error {e.resp.status}: {error_detail}"
        logger.error(error_msg)
        return False, error_msg
    except Exception as e:
        logger.error(f"Upload exception for clip {clip_id}: {e}")
        return False, str(e)


def update_clip_uploaded(clip_id, video_id, url, privacy):
    """Mark clip as uploaded in the database."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            UPDATE clips SET
                youtube_video_id=?,
                youtube_url=?,
                uploaded_at=?,
                privacy_status=?,
                upload_status='uploaded',
                status='uploaded'
            WHERE id=?
        """, (video_id, url, datetime.now().isoformat(), privacy, clip_id))
        conn.execute("""
            UPDATE upload_schedule SET
                status='uploaded',
                youtube_video_id=?
            WHERE clip_id=?
        """, (video_id, clip_id))
        conn.commit()
        conn.close()
        logger.info(f"Clip {clip_id} marked as uploaded in database")
    except Exception as e:
        logger.error(f"Failed to update clip {clip_id} in database: {e}")


def track_quota(channel_name, units):
    """Track YouTube API quota usage."""
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        conn = sqlite3.connect(DB_PATH)
        existing = conn.execute(
            "SELECT id, units_used, uploads_count FROM upload_quota WHERE date=? AND channel=?",
            (today, channel_name)
        ).fetchone()
        if existing:
            conn.execute("""
                UPDATE upload_quota SET
                    units_used=units_used+?,
                    uploads_count=uploads_count+1
                WHERE id=?
            """, (units, existing[0]))
        else:
            conn.execute("""
                INSERT INTO upload_quota (date, channel, units_used, uploads_count)
                VALUES (?, ?, ?, 1)
            """, (today, channel_name, units))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to track quota: {e}")


def check_quota_available(channel_name):
    """Check if we have quota remaining for another upload today."""
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT units_used FROM upload_quota WHERE date=? AND channel=?",
            (today, channel_name)
        ).fetchone()
        conn.close()
        used = row[0] if row else 0
        return used + 1600 <= 9500
    except Exception as e:
        logger.error(f"Failed to check quota: {e}")
        return True
