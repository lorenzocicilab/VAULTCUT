from moviepy import VideoFileClip
from moviepy.video.fx import Crop
from src.logger import get_system_logger

logger = get_system_logger()


def make_vertical(clip):
    try:
        width, height = clip.size

        if width > height:
            crop_width = int(height * 9 / 16)
            x_center = width // 2
            x1 = x_center - crop_width // 2
            x2 = x1 + crop_width
            clip = Crop(x1=x1, x2=x2).apply(clip)
            clip = clip.resized((1080, 1920))
        else:
            clip = clip.resized((1080, 1920))

        return clip

    except Exception as e:
        logger.error(f"Vertical crop failed: {e}")
        return clip


class VerticalCropper:
    """Wrapper class for backward compatibility with clip_processor."""
    def crop(self, clip):
        return make_vertical(clip)
