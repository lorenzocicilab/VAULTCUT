def assign_channel(source_category):
    channel_map = {
        'gaming': 'VAULTCUT Gaming',
        'news': 'VAULTCUT News',
        'sports': 'VAULTCUT Sports',
        'entertainment': 'VAULTCUT Entertainment',
        'tech': 'VAULTCUT Tech'
    }
    category = str(source_category).lower().strip()
    return channel_map.get(category, 'VAULTCUT Entertainment')


def get_channel_category(channel_name):
    reverse_map = {
        'VAULTCUT Gaming': 'gaming',
        'VAULTCUT News': 'news',
        'VAULTCUT Sports': 'sports',
        'VAULTCUT Entertainment': 'entertainment',
        'VAULTCUT Tech': 'tech'
    }
    return reverse_map.get(channel_name, 'entertainment')
