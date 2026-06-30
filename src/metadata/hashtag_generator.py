import sqlite3
import re
from src.logger import get_system_logger

logger = get_system_logger()

CATEGORY_TAGS = {
    'gaming': ['#gaming', '#games', '#gamer'],
    'news': ['#news', '#breakingnews'],
    'sports': ['#sports', '#highlights'],
    'entertainment': ['#entertainment', '#trending'],
    'tech': ['#tech', '#technology']
}


def generate_hashtags(category, db_path='data/vaultcut.db'):
    tags = ['#shorts', '#viral']
    category_lower = str(category).lower()
    extra = CATEGORY_TAGS.get(category_lower, [])
    for tag in extra:
        if tag not in tags:
            tags.append(tag)
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            """SELECT trend_keyword FROM trend_history
               WHERE category=?
               ORDER BY trend_score DESC LIMIT 5""",
            (category_lower,)
        ).fetchall()
        conn.close()
        for (keyword,) in rows:
            if len(tags) >= 10:
                break
            cleaned = re.sub(r'[^a-zA-Z0-9]', '', keyword)
            if cleaned and len(cleaned) > 2:
                tag = f'#{cleaned.lower()}'
                if tag not in tags:
                    tags.append(tag)
    except Exception as e:
        logger.error(f"Hashtag DB query failed: {e}")
    return tags[:10]


def hashtags_to_string(hashtag_list):
    return ' '.join(hashtag_list)
