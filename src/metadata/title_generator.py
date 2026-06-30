import requests
import json
import os
from src.logger import get_system_logger

logger = get_system_logger()


def load_settings():
    with open('config/settings.json', 'r', encoding='utf-8') as f:
        return json.load(f)


def generate_title(clip_data, video_data):
    settings = load_settings()
    prompt = f"""You are a YouTube Shorts expert.
Create a viral title for this clip.
Channel: {video_data.get('channel', '')}
Clip type: {clip_data.get('clip_type', '')}
Original video: {clip_data.get('title', '')}
Clip reason: {clip_data.get('reason', '')}
Category: {video_data.get('source_category', '')}
Rules:
- Maximum 60 characters
- No words like SHOCKING or YOU WONT BELIEVE
- Make it curious and engaging
- No hashtags in title
- No emojis in title
Return ONLY the title text. Nothing else."""

    try:
        response = requests.post(
            settings['ollama_base_url'] + '/api/generate',
            json={
                'model': settings['ollama_model'],
                'prompt': prompt,
                'stream': False
            },
            timeout=90
        )
        result = response.json()['response'].strip()
        result = result.strip('"').strip("'").strip()
        result = result.split('\n')[0].strip()
        if not result or len(result) > 100:
            raise ValueError('Invalid response')
        return result[:60]
    except Exception as e:
        logger.error(f"Title generation failed: {e}")
        fallback = clip_data.get('title', 'Viral Clip')
        return fallback[:60]
