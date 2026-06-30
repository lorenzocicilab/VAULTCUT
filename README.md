# VAULTCUT 🎬

> **Fully automated YouTube Shorts factory.**
> Discovers viral content, downloads it, cuts the best moments, generates metadata, sends clips for your approval on Telegram, and uploads them to YouTube — all without manual intervention.

---

## 🚀 What It Does

VAULTCUT runs 24/7 as a background process and handles the entire short-form content pipeline:

```
YouTube Channels → Download → Transcribe → Analyze → Cut → Metadata → Approve → Upload
```

1. **Trend Discovery** — Monitors YouTube trends and finds viral content in your niche
2. **Channel Monitoring** — Tracks selected channels for new high-performing videos
3. **Auto Download** — Downloads videos automatically using yt-dlp
4. **Transcription** — Transcribes audio to text for content analysis
5. **AI Analysis** — Uses Mistral AI to identify the most viral moments
6. **Clip Cutting** — Cuts and crops clips to vertical 9:16 format (YouTube Shorts ready)
7. **Metadata Generation** — Auto-generates titles, descriptions, and hashtags
8. **Telegram Approval** — Sends each clip to your Telegram bot for review before upload
9. **YouTube Upload** — Uploads approved clips automatically with smart scheduling

---

## 📋 Features

### Core Pipeline
- Fully automated end-to-end pipeline
- Vertical crop (9:16) optimized for YouTube Shorts
- AI-powered viral moment detection (Mistral)
- Smart upload scheduling by channel and time slot
- Duplicate clip detection
- Automatic stuck download recovery
- Timestamp clamp (no more failed clips from rounding errors)

### Telegram Bot
- /status — Real-time dashboard with all system stats
- Clip preview with video and buttons in a single message
- APPROVE / REJECT / EDIT TITLE buttons per clip
- Daily report every morning at 09:00
- Online/Offline/Crash notifications
- Uptime tracking with heartbeat system

### Reliability
- Heartbeat monitor (detects crashes and downtime)
- Auto-retry failed downloads after 24 hours
- Graceful shutdown with Telegram notification
- APScheduler with job error logging
- Full SQLite database for all state management

---

## 🗂 Project Structure

```
VAULTCUT/
├── main.py                        # Entry point — starts all jobs
├── config/
│   ├── settings.json              # API keys and config (git-ignored)
│   ├── settings.example.json      # Template — copy and fill in your keys
│   └── credentials/               # YouTube OAuth tokens (git-ignored)
├── data/
│   ├── vaultcut.db                # SQLite database (git-ignored)
│   ├── downloads/                 # Downloaded source videos
│   └── clips/                     # Processed Shorts clips
└── src/
    ├── trends/                    # Trend discovery engine
    ├── discovery/                 # Channel discovery
    ├── downloader/                # yt-dlp download manager + stuck fixer
    ├── transcriber/               # Audio transcription
    ├── analyzer/                  # Mistral AI analysis
    ├── clipper/                   # FFmpeg clip cutting + duplicate detector
    ├── metadata/                  # Title / description / hashtag generation
    ├── telegram_bot/              # Bot handlers + approval queue
    ├── uploader/                  # YouTube upload manager
    ├── system_monitor/            # Heartbeat + daily report + error notifier
    ├── database/                  # DB init and connection
    └── logger.py                  # Centralized logging
```

---

## ⚙️ Scheduler — Job Intervals

| Job              | Frequency          | Description                        |
|------------------|--------------------|------------------------------------|
| Trend Engine     | Every 12 hours     | Scans YouTube trends               |
| Discovery        | Every 1 hour       | Finds new channels to monitor      |
| Downloads        | Every 30 minutes   | Downloads queued videos            |
| Transcription    | Every 30 minutes   | Transcribes downloaded videos      |
| Analysis         | Every 20 minutes   | AI identifies viral moments        |
| Clip Cutting     | Every 15 minutes   | Cuts and crops clips with FFmpeg   |
| Metadata         | Every 30 minutes   | Generates titles and descriptions  |
| Telegram         | Every 30 minutes   | Sends clips for approval           |
| YouTube Upload   | Every 10 minutes   | Uploads approved clips             |
| Heartbeat        | Every 1 minute     | Writes uptime file                 |
| Daily Report     | Every day at 09:00 | Sends full stats to Telegram       |

---

## 🛠 Requirements

- Python 3.10+
- FFmpeg installed and in PATH
- yt-dlp
- moviepy
- python-telegram-bot
- APScheduler
- Mistral AI API key
- YouTube Data API v3 key + OAuth2 credentials

---

## 📦 Installation

### 1. Clone the repository

```bash
git clone https://github.com/yourusername/vaultcut.git
cd vaultcut
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure settings

```bash
cp config/settings.example.json config/settings.json
```

Edit config/settings.json and fill in your API keys:

```json
{
  "telegram": {
    "bot_token": "YOUR_BOT_TOKEN",
    "your_chat_id": "YOUR_CHAT_ID"
  },
  "youtube": {
    "api_key": "YOUR_YOUTUBE_API_KEY"
  },
  "mistral": {
    "api_key": "YOUR_MISTRAL_API_KEY"
  }
}
```

### 4. Set up YouTube OAuth

Place your client_secret.json in config/credentials/.
On first run, authenticate via the browser prompt.

### 5. Initialize the database

```bash
python -c "from src.database.init_db import init_db; init_db()"
```

### 6. Run VAULTCUT

```bash
python main.py
```

---

## 🤖 Telegram Bot Commands

| Command   | Description                  |
|-----------|------------------------------|
| /status   | Full real-time dashboard     |

### Clip Approval Flow

When a clip is ready, the bot sends:

- Video preview (inline, mobile optimized)
- Title, score, channel, scheduled time
- APPROVE — schedules for upload
- REJECT — discards the clip
- EDIT TITLE — lets you change the title before approving

---

## 🗄 Database Schema

| Table                | Description                          |
|----------------------|--------------------------------------|
| downloaded_videos    | All videos tracked and downloaded    |
| clips                | All clip candidates and their status |
| upload_schedule      | Approved clips queued for upload     |
| monitored_channels   | Channels being tracked               |
| trend_history        | Trend data over time                 |
| upload_quota         | YouTube API quota tracking           |
| telegram_messages    | Notification log                     |
| system_logs          | System event log                     |

---

## 📊 Clip Pipeline — Status Flow

```
pending_clip
    ↓  Clipper runs every 15 minutes
ready_to_upload
    ↓  Metadata runs every 30 minutes
pending_approval
    ↓  You press APPROVE on Telegram
approved
    ↓  Uploader runs every 10 minutes
uploaded
```

---

## 🧠 AI Stack

| Component               | Model / Tool        |
|-------------------------|---------------------|
| Viral moment detection  | Mistral AI          |
| Title generation        | Rule-based + Mistral|
| Description generation  | Template + AI       |
| Hashtag generation      | Category-based      |

---

## ⚠️ Disclaimer

This tool is intended for creating original short-form content from legally obtained sources.
Always ensure you have the right to use, transform, and republish any content you process.
Respect YouTube Terms of Service and copyright laws in your jurisdiction.

---

## 📄 License

MIT License — see LICENSE for details.

---

Built with Python, FFmpeg, Mistral AI, and python-telegram-bot.
