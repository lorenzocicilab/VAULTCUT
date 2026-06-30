import sqlite3
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.downloader.download_manager import DownloadManager
import logging

logger = logging.getLogger('vaultcut.downloader.queue')

def get_queue_counts():
    """Get queue statistics"""
    conn = sqlite3.connect('data/vaultcut.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT download_status, COUNT(*) 
        FROM downloaded_videos 
        GROUP BY download_status
    """)
    stats = {}
    for status, count in cursor.fetchall():
        stats[status] = count
    conn.close()
    return stats

def download_one_now():
    """Download one video immediately"""
    manager = DownloadManager()
    videos = manager.get_queued_videos(1)
    
    if not videos:
        logger.info("No videos queued")
        return False
    
    db_id, video_id, title, channel, url, duration = videos[0]
    logger.info(f"Downloading: {title}")
    logger.info(f"URL: {url}")
    
    return manager.download_video(video_id, url)

if __name__ == "__main__":
    download_one_now()

def reset_stuck_downloads():
    """Reset videos stuck in 'downloading' status"""
    import sqlite3
    conn = sqlite3.connect('data/vaultcut.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE downloaded_videos SET download_status='queued' WHERE download_status='downloading'")
    count = cursor.rowcount
    conn.commit()
    conn.close()
    return count

def run_download_queue(batch_size=3):
    """
    Main entry point called by scheduler and main.py.
    Downloads next batch of queued videos.
    """
    logger.info(f"=== Download Queue: Starting batch (max {batch_size}) ===")
    
    results = {
        'attempted': 0,
        'completed': 0,
        'failed': 0,
        'skipped': 0
    }
    
    manager = DownloadManager()
    queued = manager.get_queued_videos(limit=batch_size)
    
    if not queued:
        logger.info("Download Queue: Nothing in queue, skipping.")
        return results
    
    logger.info(f"Download Queue: Found {len(queued)} videos to process.")
    
    for video in queued:
        video_id = video['video_id']
        url = video.get('url') or f'https://www.youtube.com/watch?v={video_id}'
        title = video.get('title', 'Unknown')
        
        logger.info(f"Downloading: {title} ({video_id})")
        results['attempted'] += 1
        
        success = manager.download_video(video_id, url)
        
        if success:
            results['completed'] += 1
            logger.info(f"? Completed: {title}")
        else:
            results['failed'] += 1
            logger.warning(f"? Failed: {title}")
    
    logger.info(
        f"=== Download Queue: Done. "
        f"{results['completed']} completed, "
        f"{results['failed']} failed ==="
    )
    return results
