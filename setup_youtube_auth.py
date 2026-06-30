"""
First-time setup script for YouTube channel authentication.
Run this ONCE per channel before that channel can upload.

Usage:
    python setup_youtube_auth.py "VAULTCUT Entertainment"
    python setup_youtube_auth.py "VAULTCUT Gaming"
    python setup_youtube_auth.py "VAULTCUT News"
    python setup_youtube_auth.py "VAULTCUT Sports"
    python setup_youtube_auth.py "VAULTCUT Tech"
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
os.chdir(os.path.abspath(os.path.dirname(__file__)))

from src.uploader.youtube_auth import get_credentials
from src.uploader.channel_config import CHANNELS


def main():
    if len(sys.argv) < 2:
        print("\nAvailable channels:")
        for ch in CHANNELS.keys():
            print(f"  - {ch}")
        print("\nUsage: python setup_youtube_auth.py \"VAULTCUT Entertainment\"")
        print("\nPrerequisites:")
        print("  1. Place client_secret.json in config/credentials/")
        print("  2. Get it from console.cloud.google.com")
        print("     → Credentials → OAuth Client ID → Desktop app")
        return

    channel = ' '.join(sys.argv[1:])

    if channel not in CHANNELS:
        print(f"\n❌ Unknown channel: {channel}")
        print("\nAvailable channels:")
        for ch in CHANNELS.keys():
            print(f"  - {ch}")
        return

    client_secret = CHANNELS[channel]['client_secret_file']
    if not os.path.exists(client_secret):
        print(f"\n❌ Missing: {client_secret}")
        print("\nTo fix:")
        print("  1. Go to console.cloud.google.com")
        print("  2. APIs & Services → Credentials")
        print("  3. Create OAuth Client ID → Desktop app")
        print("  4. Download JSON → rename to client_secret.json")
        print(f"  5. Place in: config/credentials/")
        return

    print(f"\nSetting up OAuth for: {channel}")
    print("Your browser will open for Google login.")
    print("Sign in with the account that OWNS this YouTube channel.\n")

    try:
        creds = get_credentials(channel)
        if creds and creds.valid:
            token_file = CHANNELS[channel]['token_file']
            print(f"\n✅ Authentication successful for {channel}")
            print(f"   Token saved to: {token_file}")
            print(f"   This channel can now upload videos automatically.")
        else:
            print(f"\n❌ Authentication failed for {channel}")
            print("   Check logs for details.")
    except Exception as e:
        print(f"\n❌ Error during authentication: {e}")


if __name__ == '__main__':
    main()
