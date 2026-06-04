# AppX Uploader Bot v3.3

A Telegram bot that bypasses AppX CDN/DRM protection, downloads files (PDFs, videos, images), and forwards them — along with the resolved bypass link — to one or more target Telegram channels.

---

## Quick Start

```bash
# 1. Extract the zip / clone the folder
# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Copy the example env file and fill in your values
cp .env.example .env
nano .env          # or open in any text editor

# 4. Run the bot
python main.py
```

---

## Environment Variables (`.env` file)

Create a file named `.env` in the bot root folder. Every line is `KEY=value`.
Lines starting with `#` are comments and are ignored.

### How to create / edit `.env`

**On Linux / Mac / VPS (terminal):**
```bash
cp .env.example .env
nano .env
# Save: Ctrl+O → Enter → Ctrl+X
```

**On Windows:**
```
copy .env.example .env
notepad .env
```

**On Replit:**
Open `bot-extracted/appx-uploader-bot/.env` in the file tree and edit directly.

---

## All Variables — Full Reference

### 🔴 Required

| Variable | Example | How to get it |
|----------|---------|---------------|
| `BOT_TOKEN` | `123456:ABCabc...` | Message [@BotFather](https://t.me/BotFather) on Telegram → `/newbot` → follow prompts → copy the token |
| `ADMIN_IDS` | `8525952693` | Your Telegram numeric user ID. Message [@userinfobot](https://t.me/userinfobot) — it replies with your ID instantly. Multiple admins: comma-separated `123,456,789` |

### 📢 Channel Forwarding — `CHANNEL_ID`

The bot sends each file's **bypass link** + the **downloaded file** to every channel listed here — independently and sequentially per file.

```
# Single public channel (@username):
CHANNEL_ID=@mychannel

# Single private channel (numeric ID starting with -100):
CHANNEL_ID=-1003505154626

# Multiple channels — comma-separated, no spaces:
CHANNEL_ID=@channel1,@channel2,-1003505154626

# Mix of public and private:
CHANNEL_ID=@pubchannel,-100123456789
```

**How to get a private channel's numeric ID:**
1. Add [@JsonDumpBot](https://t.me/JsonDumpBot) as admin in your channel
2. Forward any message from your channel to @JsonDumpBot
3. Find `"id": -100XXXXXXXXX` in the reply — that full number (with the minus sign) is your channel ID

**How to give the bot permission to post in a channel:**
1. Open your Telegram channel → Settings → Administrators
2. Add your bot as an admin
3. Enable the **"Post Messages"** permission → Save

### 🔑 AppX Credentials (for CDN bypass)

| Variable | Description |
|----------|-------------|
| `APPX_EMAIL` | AppX account email — used to auto-refresh the CDN cookie |
| `APPX_PASSWORD` | AppX account password |
| `APPX_COOKIE` | Paste a raw cookie string directly (alternative to email/password). To get it: open AppX in browser → F12 → Network tab → copy the `Cookie:` request header value |

### 🛡️ DRM Keys

```
# Format: kid:key pairs, comma-separated
DRM_KEYS=abc123def456:789abcdef012,aabbccdd1122:33445566778899aa
```

Both `kid` and `key` are hex strings (from Widevine/ClearKey). You can also add keys at runtime via the `/setkey` admin command.

### 🌐 Network

| Variable | Default | Description |
|----------|---------|-------------|
| `HTTP_PROXY` | *(empty)* | Proxy for all downloads. Example: `http://user:pass@proxy.host:8080` |
| `YTDLP_COOKIES_FILE` | `cookies.txt` | Netscape-format cookies file for yt-dlp stream downloads |

### 🖥️ Web API Dashboard

| Variable | Default | Description |
|----------|---------|-------------|
| `API_ENABLED` | `true` | Set `false` to disable the REST dashboard |
| `API_PORT` | `8080` | Port for the web dashboard (`http://your-server:8080/`) |
| `API_SECRET` | *(change this!)* | Secret key — include header `X-API-Secret: <value>` in all API requests |

### 📁 Storage Paths

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_PATH` | `appx_bot.db` | SQLite database file |
| `TEMP_DIR` | `temp` | Temporary download folder (auto-created) |

### ⬇️ Download Limits

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_RETRIES` | `5` | How many times to retry a failed download |
| `DOWNLOAD_TIMEOUT` | `600` | Max seconds for a single download (10 min) |
| `MAX_FILE_SIZE_MB` | `4000` | Max file size in MB (Telegram limit ≈ 4 GB) |

### 🔒 Access Control

| Variable | Default | Description |
|----------|---------|-------------|
| `ADMIN_ONLY_MODE` | `false` | `true` = only admins can use the bot |
| `REQUIRE_JOIN_CHANNEL` | *(empty)* | Force users to join a channel first. Value: channel username without @ |
| `MAX_JOBS_PER_USER` | `1` | Max simultaneous download jobs per user |
| `MAINTENANCE_MODE` | `false` | `true` = bot shows maintenance message to non-admins |

### 🎨 Appearance

| Variable | Default | Description |
|----------|---------|-------------|
| `BOT_NAME` | `AppX Uploader Bot` | Name shown in messages and welcome card |
| `BOT_THEME_COLOR` | `#6C63FF` | Primary color for welcome card (hex) |
| `BOT_ACCENT_COLOR` | `#FF6584` | Accent color for welcome card (hex) |
| `WELCOME_IMAGE_ENABLED` | `true` | Generate welcome card image on `/start` |

---

## Admin Commands

After setting your `ADMIN_IDS`, these commands become available:

| Command | Description |
|---------|-------------|
| `/admin` | Open admin panel |
| `/stats` | Full bot statistics |
| `/users` | List all users |
| `/ban <id>` | Ban a user |
| `/unban <id>` | Unban a user |
| `/addadmin <id>` | Grant admin rights |
| `/removeadmin <id>` | Remove admin rights |
| `/jobs [status]` | List jobs (filter: running/completed/failed) |
| `/killjob <prefix>` | Cancel a job by ID prefix |
| `/killall` | Cancel all running jobs |
| `/monitor` | Live view of running jobs |
| `/announce <msg>` | Send message to all target channels |
| `/broadcast <msg>` | Send message to all bot users |
| `/setkey <kid> <key>` | Add a DRM key |
| `/delkey <kid>` | Remove a DRM key |
| `/listkeys` | Show all stored DRM keys |
| `/setcookie <cookie>` | Save a CDN cookie |
| `/getcookie` | Show stored cookies |
| `/maintenance on/off` | Toggle maintenance mode |
| `/userinfo <id>` | Detailed user info |
| `/searchuser <q>` | Search users by name/username |
| `/topusers` | Top 10 users by files downloaded |
| `/alllogs` | Recent log entries |
| `/errorlogs` | Error-only logs |
| `/clearlogs` | Clear log entries |
| `/exportdb` | Export database as JSON file |
| `/setconfig <k> <v>` | Set a runtime config value |
| `/getconfig` | Show all config values |

---

## How the Bot Works

For each URL in a submitted `.txt` file, the bot does the following **independently and sequentially**:

1. **Resolves / bypasses** the URL (CDN key bypass, URLPrefix decode, Widevine/ClearKey DRM)
2. **Sends the bypass link** as a text message to every channel in `CHANNEL_ID`
3. **Downloads** the actual file to a temp folder
4. **Uploads** the file to the user's Telegram chat
5. **Forwards** the downloaded file to every channel in `CHANNEL_ID`
6. **Cleans up** the temp file

Each file is a completely independent operation — if one fails, the rest continue.

### TXT File Format

```
# Lines starting with # are ignored

# Title + URL on the same line (|| separator):
Lecture 1 - Introduction || https://cdn.appx.co.in/file.pdf

# Title on one line, URL on the next:
Lecture 2 - Advanced Topics
https://cdn.appx.co.in/video.mp4

# URL only (filename is used as title):
https://cdn.appx.co.in/notes.pdf
```

---

## Running on a VPS / Server

### Option 1 — screen (simple)
```bash
sudo apt install screen
screen -S appxbot
cd /opt/appx-uploader-bot
python main.py
# Detach: Ctrl+A, then D
# Re-attach: screen -r appxbot
```

### Option 2 — systemd (recommended, auto-restart on crash)

Create `/etc/systemd/system/appxbot.service`:
```ini
[Unit]
Description=AppX Uploader Bot
After=network.target

[Service]
WorkingDirectory=/opt/appx-uploader-bot
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=5
EnvironmentFile=/opt/appx-uploader-bot/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable appxbot
sudo systemctl start appxbot
sudo systemctl status appxbot   # check it's running
sudo journalctl -u appxbot -f   # follow logs
```

### Option 3 — Replit
The bot runs automatically via the "Telegram Bot" workflow. Edit `.env` in the file tree, then restart the workflow.

---

## Requirements

- Python 3.10 or newer
- All Python packages listed in `requirements.txt`
- Optional system package for best-quality welcome cards: `libcairo2-dev` (falls back to Pillow automatically if not installed)

```bash
# Ubuntu / Debian (optional, for cairosvg)
sudo apt-get install libcairo2-dev pkg-config python3-dev
pip install cairosvg
```

---

## Troubleshooting

**Bot doesn't respond to messages:**
- Verify `BOT_TOKEN` is correct (no spaces, no quotes)
- Make sure `python main.py` is running without errors

**Admin commands say "Admin only":**
- Your numeric Telegram ID must be in `ADMIN_IDS`
- Get your ID: message [@userinfobot](https://t.me/userinfobot)
- Restart the bot after editing `.env`

**Channel not receiving messages/files:**
- Bot must be an admin in the channel with "Post Messages" permission
- Private channel: use the full numeric ID starting with `-100`
- Public channel: use `@username` format

**Download keeps failing / retrying:**
- Add a valid `APPX_COOKIE` or `APPX_EMAIL` + `APPX_PASSWORD`
- Use `/setkey` to add DRM keys for encrypted content
- Check `HTTP_PROXY` if the server is geo-blocked

**Web API dashboard not loading:**
- Open `http://your-server-ip:8080/` in a browser
- For protected endpoints, add the header: `X-API-Secret: <your API_SECRET value>`
