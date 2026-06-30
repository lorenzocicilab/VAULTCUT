import os
import glob
import json
import time
import sqlite3
import yt_dlp
from src.logger import get_system_logger

logger = get_system_logger()


def load_settings():
    with open('config/settings.json', 'r', encoding='utf-8') as f:
        return json.load(f)


class DownloadManager:
    def __init__(self, db_path='data/vaultcut.db'):
        self.db_path = db_path
        self.settings = load_settings()
        self.output_dir = 'data/downloads'
        os.makedirs(self.output_dir, exist_ok=True)

    def get_ydl_opts(self):
        return {
            'format': 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best[height<=1080]',
            'outtmpl': os.path.join(self.output_dir, '%(id)s.%(ext)s'),
            'quiet': False,
            'no_warnings': False,
            'socket_timeout': 30,
            'retries': 3,
        }

    def get_queued_videos(self, limit=5):
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            """SELECT id, video_id, url, title, view_count
               FROM downloaded_videos
               WHERE download_status='queued'
               ORDER BY view_count DESC NULLS LAST
               LIMIT ?""",
            (limit,)
        ).fetchall()
        conn.close()
        return rows

    def mark_downloading(self, video_id):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE downloaded_videos SET download_status='downloading' WHERE video_id=?",
            (video_id,)
        )
        conn.commit()
        conn.close()

    def mark_completed(self, video_id, file_path):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """UPDATE downloaded_videos
               SET download_status='completed', file_path=?, status='downloaded'
               WHERE video_id=?""",
            (file_path, video_id)
        )
        conn.commit()
        conn.close()
        logger.info(f"Marked {video_id} completed: {file_path}")

    def mark_failed(self, video_id, error_msg):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """UPDATE downloaded_videos
               SET download_status='failed', error_message=?
               WHERE video_id=?""",
            (error_msg, video_id)
        )
        conn.commit()
        conn.close()
        logger.error(f"Marked {video_id} failed: {error_msg}")

    def download_video(self, video_id, url=None):
        if not url or str(url).strip() == '':
            url = f'https://www.youtube.com/watch?v={video_id}'

        logger.info(f"Downloading {video_id} from {url}")
        self.mark_downloading(video_id)

        try:
            ydl_opts = self.get_ydl_opts()
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            pattern = os.path.join(self.output_dir, f'{video_id}.*')
            files = glob.glob(pattern)

            if files and os.path.getsize(files[0]) > 0:
                self.mark_completed(video_id, files[0])
                logger.info(f"Download success: {files[0]}")
                return True
            else:
                self.mark_failed(video_id, 'File not found after download')
                return False

        except Exception as e:
            self.mark_failed(video_id, str(e))
            return False


def run_download_batch():
    manager = DownloadManager()
    videos = manager.get_queued_videos(limit=5)

    if not videos:
        logger.info("No videos queued for download")
        return

    logger.info(f"Starting download batch: {len(videos)} videos")

    for i, (row_id, video_id, url, title, view_count) in enumerate(videos):
        logger.info(f"Downloading [{i+1}/{len(videos)}]: {title or video_id}")
        manager.download_video(video_id, url)
        if i < len(videos) - 1:
            logger.info("Waiting 45 seconds before next download...")
            time.sleep(45)

    logger.info("Download batch complete")
