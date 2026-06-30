import os
import json
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from src.logger import get_system_logger

logger = get_system_logger()

SCOPES = ['https://www.googleapis.com/auth/youtube.upload']


def get_credentials(channel_name):
    """Get or refresh OAuth credentials for a channel."""
    from src.uploader.channel_config import get_channel_config
    config = get_channel_config(channel_name)
    if not config:
        logger.error(f"No config for channel: {channel_name}")
        return None

    token_file = config['token_file']
    client_secret = config['client_secret_file']

    if not os.path.exists(client_secret):
        logger.error(f"Missing client_secret.json at: {client_secret}")
        logger.error("Get it from console.cloud.google.com → Credentials → OAuth Client ID")
        return None

    creds = None

    if os.path.exists(token_file):
        try:
            with open(token_file, 'r') as f:
                token_data = json.load(f)
            creds = Credentials.from_authorized_user_info(token_data, SCOPES)
        except Exception as e:
            logger.error(f"Failed loading token for {channel_name}: {e}")
            creds = None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            save_credentials(token_file, creds)
            logger.info(f"Refreshed token for {channel_name}")
        except Exception as e:
            logger.error(f"Token refresh failed for {channel_name}: {e}")
            creds = None

    if not creds or not creds.valid:
        logger.info(f"Starting OAuth flow for {channel_name}")
        logger.info("Browser will open for YouTube login...")
        try:
            flow = InstalledAppFlow.from_client_secrets_file(client_secret, SCOPES)
            creds = flow.run_local_server(port=0)
            save_credentials(token_file, creds)
            logger.info(f"OAuth complete, token saved to {token_file}")
        except Exception as e:
            logger.error(f"OAuth flow failed for {channel_name}: {e}")
            return None

    return creds


def save_credentials(token_file, creds):
    """Save OAuth credentials to JSON token file."""
    try:
        os.makedirs(os.path.dirname(token_file), exist_ok=True)
        token_data = {
            'token': creds.token,
            'refresh_token': creds.refresh_token,
            'token_uri': creds.token_uri,
            'client_id': creds.client_id,
            'client_secret': creds.client_secret,
            'scopes': list(creds.scopes) if creds.scopes else []
        }
        with open(token_file, 'w') as f:
            json.dump(token_data, f, indent=2)
    except Exception as e:
        logger.error(f"Failed saving credentials to {token_file}: {e}")


def has_valid_credentials(channel_name):
    """Check if channel has a saved token file without triggering OAuth."""
    from src.uploader.channel_config import get_channel_config
    config = get_channel_config(channel_name)
    if not config:
        return False
    return os.path.exists(config['token_file'])
