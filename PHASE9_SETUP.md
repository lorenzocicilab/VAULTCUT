# Phase 9 Setup Guide

## Step 1: Install Dependencies

```powershell
pip install google-api-python-client google-auth google-auth-oauthlib google-auth-httplib2
```

---

## Step 2: Get YouTube API Credentials

1. Go to: https://console.cloud.google.com
2. Create a new project named **VAULTCUT**
3. Go to: **APIs & Services → Enable APIs**
4. Search for and enable: **YouTube Data API v3**
5. Go to: **APIs & Services → Credentials**
6. Click: **Create Credentials → OAuth Client ID**
7. Configure consent screen first if prompted:
   - User type: External
   - App name: VAULTCUT
   - Add your own Google account as a test user
8. Application type: **Desktop app**
9. Name: **VAULTCUT Desktop**
10. Click **Create** → **Download JSON**
11. Rename the downloaded file to: `client_secret.json`
12. Place it at: `config/credentials/client_secret.json`

---

## Step 3: Add upload_privacy to settings.json

Open `config/settings.json` and add:

```json
"upload_privacy": "unlisted"
```

**Options:**
- `"unlisted"` — Only people with the link can see it (recommended for testing)
- `"public"` — Visible to everyone (use when you trust the system)
- `"private"` — Only you can see it

---

## Step 4: Authenticate Each Channel

Run this ONCE for each YouTube channel you want to upload to.
Your browser will open — sign in with the Google account that **owns** the channel.

```powershell
# Authenticate VAULTCUT Entertainment (start here)
python setup_youtube_auth.py "VAULTCUT Entertainment"

# Authenticate other channels when ready
python setup_youtube_auth.py "VAULTCUT Gaming"
python setup_youtube_auth.py "VAULTCUT News"
python setup_youtube_auth.py "VAULTCUT Sports"
python setup_youtube_auth.py "VAULTCUT Tech"
```

Tokens are saved to `config/credentials/` and auto-refresh.
You only need to do this once per channel.

---

## Step 5: Test an Upload

Force upload the next approved clip immediately (ignores schedule):

```powershell
python manage_channels.py upload test
```

Type `YES` when prompted to confirm.

---

## Step 6: Check Upload Status

```powershell
python manage_channels.py upload status
```

Shows:
- Pending / uploaded / failed counts
- Today's API quota usage
- Next 5 scheduled uploads

---

## Step 7: Run the Full System

```powershell
python main.py
```

The upload job runs every 10 minutes and only uploads
when `datetime.now() >= scheduled_time`.

---

## Quota Information

| Action | Quota Cost |
|--------|-----------|
| Upload 1 video | 1,600 units |
| Free daily quota | 10,000 units |
| Max uploads/day | 6 (with 500 unit buffer) |

Quota resets at midnight Pacific Time.

---

## Credential Files

| File | Purpose |
|------|---------|
| `config/credentials/client_secret.json` | OAuth app credentials (shared) |
| `config/credentials/entertainment_token.json` | VAULTCUT Entertainment token |
| `config/credentials/gaming_token.json` | VAULTCUT Gaming token |
| `config/credentials/news_token.json` | VAULTCUT News token |
| `config/credentials/sports_token.json` | VAULTCUT Sports token |
| `config/credentials/tech_token.json` | VAULTCUT Tech token |

**Never commit these files to Git.**

---

## Troubleshooting

**"Missing client_secret.json"**
→ Follow Step 2 above. The file must be at `config/credentials/client_secret.json`.

**"No credentials for channel"**
→ Run `python setup_youtube_auth.py "VAULTCUT Entertainment"` for that channel.

**"Daily quota reached"**
→ Wait until midnight Pacific Time for quota reset.
→ Or request a quota increase at console.cloud.google.com.

**"Token refresh failed"**
→ Delete the token file and re-run `setup_youtube_auth.py` for that channel.

**Upload stuck at 0%**
→ Check your internet connection and firewall.
→ Large files (>50MB) may take several minutes.

**Video uploaded but not showing on channel**
→ YouTube processing can take 5–30 minutes.
→ Check the URL returned in the Telegram notification.
