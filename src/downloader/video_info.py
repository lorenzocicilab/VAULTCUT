import yt_dlp
import logging

logger = logging.getLogger('vaultcut.downloader.info')

def extract_video_info(url):
    """Extract video metadata using yt-dlp library"""
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                'video_id': info.get('id'),
                'title': info.get('title'),
                'duration': info.get('duration'),
                'uploader': info.get('uploader'),
                'upload_date': info.get('upload_date'),
                'view_count': info.get('view_count'),
                'like_count': info.get('like_count'),
            }
    except Exception as e:
        logger.error(f"yt-dlp error: {e}")
        return None

if __name__ == "__main__":
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    print(f"\nExtracting: {url}\n")
    info = extract_video_info(url)
    if info:
        for key, value in info.items():
            print(f"  {key}: {value}")
    else:
        print("Failed")
