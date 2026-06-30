"""
VAULTCUT - Heartbeat System
"""

import os
import json
from datetime import datetime, timedelta

HEARTBEAT_FILE = 'data/heartbeat.json'
_started_at = datetime.now().isoformat()


def write_heartbeat():
    """Write current timestamp to heartbeat file."""
    os.makedirs(os.path.dirname(HEARTBEAT_FILE), exist_ok=True)
    existing = {}
    try:
        with open(HEARTBEAT_FILE, 'r', encoding='utf-8') as f:
            existing = json.load(f)
    except Exception:
        pass

    data = {
        'timestamp': datetime.now().isoformat(),
        'pid': os.getpid(),
        'started_at': existing.get('started_at', _started_at)
    }
    with open(HEARTBEAT_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f)


def get_last_heartbeat():
    """Get last heartbeat timestamp. Returns None if file missing."""
    if not os.path.exists(HEARTBEAT_FILE):
        return None
    try:
        with open(HEARTBEAT_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return datetime.fromisoformat(data['timestamp'])
    except Exception:
        return None


def detect_downtime():
    """Returns downtime info if system was offline."""
    last = get_last_heartbeat()
    if last is None:
        return None
    now = datetime.now()
    delta = now - last
    if delta < timedelta(minutes=2):
        return None
    return {
        'offline_from': last,
        'online_at': now,
        'duration': delta,
        'duration_str': format_duration(delta)
    }


def detect_crash():
    """If heartbeat is recent but we are starting fresh, previous instance crashed."""
    last = get_last_heartbeat()
    if last is None:
        return False
    delta = datetime.now() - last
    return delta < timedelta(minutes=5) and delta > timedelta(seconds=30)


def format_duration(delta):
    """Format timedelta as human readable string."""
    total_seconds = int(delta.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    parts = []
    if hours > 0:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes > 0:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")
    return ' '.join(parts)
