import sqlite3
import json
from src.logger import get_system_logger
from src.metadata.title_generator import generate_title
from src.metadata.description_generator import generate_description
from src.metadata.hashtag_generator import generate_hashtags
from src.metadata.upload_scheduler import calculate_upload_time
from src.metadata.channel_assigner import assign_channel

logger = get_system_logger()
DB_PATH = 'data/vaultcut.db'


def dict_from_row(cursor, row):
    return {col[0]: val for col, val in zip(cursor.description, row)}


def process_clip(clip_id):
    logger.info(f"Processing metadata for clip {clip_id}")

    try:
        conn = sqlite3.connect(DB_PATH)

        cursor = conn.execute("SELECT * FROM clips WHERE id=?", (clip_id,))
        row = cursor.fetchone()
        if not row:
            logger.error(f"Clip {clip_id} not found")
            conn.close()
            return False
        clip = dict_from_row(cursor, row)

        cursor2 = conn.execute(
            "SELECT * FROM downloaded_videos WHERE id=?",
            (clip['video_id'],)
        )
        row2 = cursor2.fetchone()
        video = dict_from_row(cursor2, row2) if row2 else {}

        logger.info("Generating title...")
        generated_title = generate_title(clip, video)
        logger.info(f"Title: {generated_title}")

        logger.info("Generating description...")
        generated_description = generate_description(clip, video, generated_title)
        logger.info(f"Description generated")

        logger.info("Generating hashtags...")
        category = video.get('source_category', 'entertainment')
        hashtags = generate_hashtags(category)
        generated_hashtags = json.dumps(hashtags)
        logger.info(f"Hashtags: {hashtags}")

        logger.info("Assigning channel...")
        channel_name = assign_channel(category)
        logger.info(f"Target channel: {channel_name}")

        logger.info("Calculating upload time...")
        scheduled_time = calculate_upload_time(channel_name)
        logger.info(f"Scheduled: {scheduled_time}")

        conn.execute("""
            UPDATE clips SET
                generated_title=?,
                generated_description=?,
                generated_hashtags=?,
                target_channel=?,
                scheduled_upload_time=?,
                status='pending_approval',
                approval_status='pending'
            WHERE id=?
        """, (
            generated_title,
            generated_description,
            generated_hashtags,
            channel_name,
            scheduled_time,
            clip_id
        ))
        conn.commit()
        conn.close()

        logger.info(f"Clip {clip_id} metadata saved successfully")
        return True

    except Exception as e:
        logger.error(f"Metadata engine failed for clip {clip_id}: {e}")
        return False
