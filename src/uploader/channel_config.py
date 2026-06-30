CHANNELS = {
    'VAULTCUT Entertainment': {
        'normalized': 'vaultcut_entertainment',
        'category_id': '24',
        'default_tags': ['entertainment', 'viral', 'shorts'],
        'token_file': 'config/credentials/entertainment_token.json',
        'client_secret_file': 'config/credentials/client_secret.json'
    },
    'VAULTCUT Gaming': {
        'normalized': 'vaultcut_gaming',
        'category_id': '20',
        'default_tags': ['gaming', 'gameplay', 'shorts'],
        'token_file': 'config/credentials/gaming_token.json',
        'client_secret_file': 'config/credentials/client_secret.json'
    },
    'VAULTCUT News': {
        'normalized': 'vaultcut_news',
        'category_id': '25',
        'default_tags': ['news', 'breaking', 'shorts'],
        'token_file': 'config/credentials/news_token.json',
        'client_secret_file': 'config/credentials/client_secret.json'
    },
    'VAULTCUT Sports': {
        'normalized': 'vaultcut_sports',
        'category_id': '17',
        'default_tags': ['sports', 'highlights', 'shorts'],
        'token_file': 'config/credentials/sports_token.json',
        'client_secret_file': 'config/credentials/client_secret.json'
    },
    'VAULTCUT Tech': {
        'normalized': 'vaultcut_tech',
        'category_id': '28',
        'default_tags': ['tech', 'technology', 'shorts'],
        'token_file': 'config/credentials/tech_token.json',
        'client_secret_file': 'config/credentials/client_secret.json'
    }
}


def normalize_channel_name(name):
    """Handle both formats: 'VAULTCUT Entertainment' and 'vaultcut_entertainment'"""
    if not name:
        return None
    name_lower = name.lower().replace(' ', '_')
    for key, config in CHANNELS.items():
        if config['normalized'] == name_lower or key.lower() == name.lower():
            return key
    return None


def get_channel_config(channel_name):
    normalized = normalize_channel_name(channel_name)
    if normalized:
        return CHANNELS.get(normalized)
    return None
