"""
VAULTCUT Category Classifier
==============================
Given a trend keyword or topic, this module guesses which
VAULTCUT channel category it belongs to:
  gaming / news / sports / entertainment / tech

This is used by every trend source (Google, YouTube, Reddit, etc.)
so the category logic lives in one place and is easy to update.

Usage:
    from src.trends.category import classify
    cat = classify("League of Legends patch notes")  # → "gaming"
    cat = classify("earthquake Japan")               # → "news"
"""

# ============================================================
# Keyword lists for each category
# Add more keywords here anytime to improve accuracy.
# Keywords are matched case-insensitively, anywhere in the text.
# ============================================================

CATEGORY_KEYWORDS = {
    "gaming": [
        # Game titles and genres
        "game", "gaming", "gamer", "gameplay", "playthrough",
        "esports", "esport", "twitch", "stream", "streamer",
        "fortnite", "minecraft", "roblox", "valorant", "apex",
        "call of duty", "warzone", "league of legends", "lol",
        "overwatch", "counter-strike", "cs2", "pubg", "gta",
        "elden ring", "zelda", "mario", "pokemon", "fifa",
        "nba 2k", "madden", "battlefield", "halo", "destiny",
        "world of warcraft", "wow", "dota", "rocket league",
        "among us", "fall guys", "terraria", "stardew",
        "new world", "lost ark", "final fantasy", "skyrim",
        "speedrun", "speedrunner", "glitch", "clip", "highlight",
        "xbox", "playstation", "ps5", "nintendo", "switch",
        "steam", "epic games", "ign", "gamespot", "pc gaming",
        "graphics card", "gpu", "rtx", "ray tracing",
        "e3", "gamescom", "gaming tournament", "pro player",
        "clutch", "rage quit", "montage",
    ],

    "news": [
        # Politics and world events
        "election", "president", "government", "congress", "senate",
        "parliament", "prime minister", "minister", "vote", "voting",
        "politics", "political", "democrat", "republican", "liberal",
        "conservative", "policy", "law", "legislation", "bill",
        "war", "conflict", "military", "army", "attack", "bombing",
        "protest", "riot", "demonstration", "march",
        "breaking news", "breaking", "urgent", "alert",
        "earthquake", "hurricane", "tornado", "flood", "tsunami",
        "disaster", "crisis", "emergency", "accident", "crash",
        "investigation", "arrest", "trial", "court", "judge",
        "economy", "inflation", "recession", "stock market", "fed",
        "climate", "global warming", "environment",
        "ukraine", "russia", "china", "north korea", "iran",
        "israel", "gaza", "middle east", "nato",
        "white house", "pentagon", "cia", "fbi",
        "shooting", "crime", "murder", "police",
        "cnn", "bbc", "fox news", "nbc", "abc news",
    ],

    "sports": [
        # Sports and leagues
        "nba", "nfl", "nhl", "mlb", "mls",
        "soccer", "football", "basketball", "baseball", "hockey",
        "tennis", "golf", "boxing", "mma", "ufc",
        "olympics", "world cup", "super bowl", "nba finals",
        "championship", "playoff", "final", "tournament",
        "touchdown", "home run", "slam dunk", "goal",
        "lebron", "curry", "mahomes", "messi", "ronaldo",
        "federer", "djokovic", "tiger woods",
        "match", "game recap", "highlights", "sports highlights",
        "trade", "transfer", "draft pick", "injury",
        "coach", "referee", "umpire",
        "formula 1", "f1", "nascar", "racing",
        "wrestling", "wwe", "aew",
        "espn", "bleacher report", "sports illustrated",
    ],

    "entertainment": [
        # Movies, TV, music, celebrity
        "movie", "film", "cinema", "trailer", "teaser",
        "tv show", "series", "episode", "season", "netflix",
        "hulu", "disney plus", "hbo", "amazon prime",
        "celebrity", "famous", "star", "actor", "actress",
        "singer", "rapper", "musician", "band", "album",
        "song", "music video", "billboard", "grammy", "oscar",
        "emmy", "golden globe", "award", "red carpet",
        "drama", "comedy", "horror", "thriller", "action",
        "marvel", "dc", "superhero", "avengers", "batman",
        "taylor swift", "beyonce", "drake", "kanye", "rihanna",
        "kardashian", "influencer", "tiktok", "viral",
        "meme", "funny", "reaction", "prank", "roast",
        "youtube drama", "beef", "controversy", "scandal",
        "interview", "talk show", "podcast",
        "anime", "manga", "cosplay",
    ],

    "tech": [
        # Technology and science
        "ai", "artificial intelligence", "machine learning", "chatgpt",
        "openai", "google ai", "anthropic", "gemini", "claude",
        "robot", "robotics", "automation",
        "apple", "iphone", "ipad", "mac", "ios",
        "google", "android", "pixel",
        "microsoft", "windows", "azure",
        "amazon", "aws", "meta", "facebook", "instagram",
        "startup", "ipo", "funding", "venture capital",
        "bitcoin", "crypto", "ethereum", "blockchain", "nft",
        "space", "nasa", "spacex", "elon musk", "satellite",
        "cybersecurity", "hack", "data breach", "privacy",
        "chip", "semiconductor", "processor", "cpu",
        "electric vehicle", "ev", "tesla", "self-driving",
        "5g", "internet", "wifi", "software", "app",
        "vr", "ar", "virtual reality", "augmented reality",
        "science", "discovery", "research", "study",
        "climate tech", "solar", "renewable energy",
        "gadget", "review", "unboxing", "hands on",
        "tech news", "wired", "techcrunch", "verge",
    ],
}


def classify(text: str, default: str = "entertainment") -> str:
    """
    Classifies a text string (keyword, title, description) into
    one of the five VAULTCUT categories.

    How it works:
    - Converts the text to lowercase
    - Counts how many keywords from each category appear in the text
    - Returns the category with the most keyword matches
    - If no keywords match at all, returns the default category

    Args:
        text: Any string — trend keyword, video title, headline, etc.
        default: Category to return when nothing matches (default: 'entertainment')

    Returns:
        One of: 'gaming', 'news', 'sports', 'entertainment', 'tech'

    Examples:
        classify("Lebron James scores 50 points")      → 'sports'
        classify("New iPhone 16 review unboxing")      → 'tech'
        classify("Fortnite new season highlights")     → 'gaming'
        classify("Breaking: earthquake hits Turkey")   → 'news'
        classify("Taylor Swift new album drops")       → 'entertainment'
        classify("random topic with no matches")       → 'entertainment'
    """
    if not text:
        return default

    text_lower = text.lower()
    scores = {}

    for category, keywords in CATEGORY_KEYWORDS.items():
        score = 0
        for kw in keywords:
            if kw in text_lower:
                # Longer keyword matches are worth more
                # "call of duty" (3 words) is more specific than "game" (1 word)
                score += len(kw.split())
        scores[category] = score

    # Find the category with the highest score
    best_category = max(scores, key=scores.get)
    best_score = scores[best_category]

    # If nothing matched, return default
    if best_score == 0:
        return default

    return best_category


def classify_batch(texts: list, default: str = "entertainment") -> list:
    """
    Classifies a list of texts. Returns a list of category strings
    in the same order as the input.

    Args:
        texts: List of strings to classify
        default: Default category when nothing matches

    Returns:
        List of category strings
    """
    return [classify(t, default) for t in texts]


# ============================================================
# Self-test
# PowerShell: python src\trends\category.py
# ============================================================
if __name__ == "__main__":
    test_cases = [
        ("Fortnite new season highlights",          "gaming"),
        ("Breaking: earthquake hits Japan",          "news"),
        ("LeBron James 40 point game highlights",   "sports"),
        ("Taylor Swift new album announcement",     "entertainment"),
        ("New iPhone 16 Pro unboxing review",       "tech"),
        ("ChatGPT vs Gemini comparison",            "tech"),
        ("NBA Finals game 7 highlights",            "sports"),
        ("Valorant pro player clutch moments",      "gaming"),
        ("Marvel movie trailer reaction",           "entertainment"),
        ("US election results breaking news",       "news"),
    ]

    print("Category Classifier Test")
    print("=" * 60)
    all_pass = True
    for text, expected in test_cases:
        result = classify(text)
        ok = "✓" if result == expected else "✗"
        if result != expected:
            all_pass = False
        print(f"  {ok}  [{result:15s}]  {text[:50]}")

    print()
    print("All tests passed!" if all_pass else "Some tests failed — check keyword lists above.")
