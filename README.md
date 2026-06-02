# AppX Uploader Bot 🤖

A production-ready Telegram bot that accepts `.txt` files containing one URL per line, downloads each file (with DRM bypass support for AppX/appx.co.in signed URLs), and sends them back to the user.

---

## Features

- ✅ AppX signed URL resolution (CDN key, URLPrefix decode)
- ✅ Encrypted PDF decryption (pikepdf)
- ✅ HLS / DASH stream download (yt-dlp)
- ✅ Sequential queue with persistent SQLite storage
- ✅ Resume from last checkpoint after restart
- ✅ Per-item retry with exponential back-off
- ✅ Live progress messages with progress bar + ETA
- ✅ Admin panel: ban/unban, broadcast, stats, job monitoring
- ✅ Rate limiting and spam protection
- ✅ Detailed rotating log files

---

## Project Structure

```
appx-uploader-bot/
├── main.py                   # Entry point
├── requirements.txt
├── .env.example              # Config template
├── setup.sh                  # One-click setup
│
├── config/
│   └── settings.py           # All config from .env
│
├── database/
│   └── db.py                 # SQLite ORM (aiosqlite)
│
├── bot/
│   ├── handlers.py           # User command & document handlers
│   ├── admin.py              # Admin-only commands
│   ├── queue_manager.py      # Job orchestration
│   ├── downloader.py         # Async HTTP downloader + retry
│   ├── drm.py                # DRM / signed-URL resolver
│   ├── progress.py           # Progress message builder
│   └── utils.py              # Logging setup
│
├── logs/                     # Rotating log files (auto-created)
└── temp/                     # Temporary download storage (auto-created)
```

---

## Setup

### 1. Clone / unzip

```bash
cd appx-uploader-bot
```

### 2. Run setup script (recommended)

```bash
chmod +x setup.sh
./setup.sh
```

This will:
- Create a Python virtual environment
- Install all dependencies
- Create `.env` from `.env.example`

### 3. Configure `.env`

```env
BOT_TOKEN=your_bot_token_here
ADMIN_IDS=123456789,987654321
```

Get your token from [@BotFather](https://t.me/BotFather).  
Get your user ID from [@userinfobot](https://t.me/userinfobot).

### 4. Install system dependencies (optional but recommended)

```bash
# Ubuntu / Debian
sudo apt install ffmpeg

# macOS
brew install ffmpeg
```

`ffmpeg` is required by yt-dlp to merge HLS/DASH stream segments into a single file.

### 5. Run

```bash
source .venv/bin/activate
python main.py
```

---

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message |
| `/help` | Full instructions |
| `/status` | Live progress of your active job |
| `/cancel` | Pause current job |
| `/resume` | Resume last paused job |
| `/logs` | View your recent logs |
| `/stop` | Alias for `/cancel` |

### Admin Commands

| Command | Description |
|---------|-------------|
| `/admin` | Admin panel overview |
| `/stats` | Bot statistics |
| `/users` | List all users |
| `/ban <user_id>` | Ban a user |
| `/unban <user_id>` | Unban a user |
| `/addadmin <user_id>` | Grant admin rights |
| `/jobs` | List recent jobs |
| `/killjob <job_id>` | Force-cancel a job |
| `/alllogs` | Global log viewer |
| `/broadcast <msg>` | Send message to all users |

---

## How to Use

1. Send the bot a `.txt` file containing one URL per line
2. Enter the line number to start from (useful for resuming)
3. The bot processes each link in order, downloading and sending the file
4. Use `/status` to track progress, `/cancel` to pause, `/resume` to continue

### Example `.txt` file

```
https://static-db-v2.appx.co.in/paid_course4/encrypted_2025-09-19-0_4698146220534657.pdf?URLPrefix=...
https://static-db-v2.appx.co.in/paid_course4/encrypted_2025-09-25-0_9164702992637684.pdf?URLPrefix=...
https://example.com/video.mp4
https://example.com/document.pdf
```

---

## DRM Bypass Strategies

For **AppX signed URLs** the bot attempts these steps in order:

1. **Direct signed URL** — try the URL as-is (may still be within `Expires` window)
2. **URLPrefix decode** — base64-decode the `URLPrefix` query param to get the real resource URL
3. **Signature strip** — remove `Signature`, `KeyName`, `Expires` and try the base path
4. **Fallback** — use original URL and let the server decide

For **encrypted PDFs** — [pikepdf](https://pikepdf.readthedocs.io/) is used to remove the password/encryption layer.

For **HLS / DASH streams** — [yt-dlp](https://github.com/yt-dlp/yt-dlp) handles manifest parsing and segment merging.

---

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `BOT_TOKEN` | — | Telegram bot token (required) |
| `ADMIN_IDS` | — | Comma-separated admin user IDs |
| `DB_PATH` | `appx_bot.db` | SQLite database path |
| `TEMP_DIR` | `temp` | Temporary file directory |
| `LOG_DIR` | `logs` | Log file directory |
| `MAX_RETRIES` | `3` | Download retry attempts |
| `RETRY_DELAY` | `5` | Seconds between retries |
| `DOWNLOAD_TIMEOUT` | `300` | HTTP timeout in seconds |
| `MAX_FILE_SIZE_MB` | `2000` | Max file size to download |
| `CHUNK_SIZE` | `1048576` | Download chunk size (bytes) |
| `RATE_LIMIT_CALLS` | `10` | Max commands per window |
| `RATE_LIMIT_PERIOD` | `60` | Rate limit window (seconds) |
| `ADMIN_ONLY_MODE` | `false` | Restrict to admins only |

---

## Deployment

### Systemd (Linux server)

Create `/etc/systemd/system/appx-bot.service`:

```ini
[Unit]
Description=AppX Uploader Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/appx-uploader-bot
ExecStart=/home/ubuntu/appx-uploader-bot/.venv/bin/python main.py
Restart=on-failure
RestartSec=10
EnvironmentFile=/home/ubuntu/appx-uploader-bot/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable appx-bot
sudo systemctl start appx-bot
sudo journalctl -u appx-bot -f
```

### Screen / tmux (quick)

```bash
screen -S appx-bot
source .venv/bin/activate
python main.py
# Ctrl+A then D to detach
```

### Docker

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "main.py"]
```

```bash
docker build -t appx-bot .
docker run -d --env-file .env --name appx-bot appx-bot
```

---

## Logs

Log files are written to the `logs/` directory:

- `bot.log` — all activity (rotates at 10 MB, keeps 5 backups)
- `errors.log` — errors only (rotates at 5 MB, keeps 3 backups)

---

## License

MIT
