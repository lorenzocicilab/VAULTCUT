import requests
import json
from src.logger import get_system_logger

logger = get_system_logger()


def load_settings():
    with open('config/settings.json', 'r', encoding='utf-8') as f:
        return json.load(f)


def generate_description(clip_data, video_data, generated_title):
    settings = load_settings()
    channel = video_data.get('channel', '')
    url = video_data.get('url', '')
    category = video_data.get('source_category', '')

    prompt = f"""Write a YouTube Shorts description.
Title: {generated_title}
Category: {category}
Original creator: {channel}
Original URL: {url}
Rules:
- Maximum 300 characters total
- Must include: Credit: {channel} - {url}
- Include 3-5 relevant hashtags
- Keep it short and punchy
- End with hashtags
Return ONLY the description text. Nothing else."""

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
        if not result:
            raise ValueError('Empty response')
        return result[:300]
    except Exception as e:
        logger.error(f"Description generation failed: {e}")
        fallback = f"Credit: {channel} - {url} #shorts #viral"
        return fallback[:300]
