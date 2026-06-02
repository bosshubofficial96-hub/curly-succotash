# 𝗔𝗣𝗣𝗫 𝗨𝗣𝗟𝗢𝗔𝗗𝗘𝗥 𝗕𝗢𝗧 v3 🤖

A fully advanced, production-ready Telegram bot that bypasses AppX DRM,
downloads protected course content, and sends every file back with
live inline progress controls — **completely rebuilt from scratch**.

---

## ✨ What's New in v3

| Feature | Details |
|---------|---------|
| **SVG Welcome Card** | Styled welcome/profile image auto-generated for each user on `/start` |
| **30+ User Commands** | Full command suite with inline auto-menus |
| **30+ Admin Commands** | Complete admin control with inline panel |
| **Web API Panel** | aiohttp REST server + beautiful HTML dashboard at `/` |
| **Advanced DRM** | PSSH extraction, Widevine L3 KID parsing, multi-strategy AppX bypass |
| **Per-user Stats** | Jobs, files, data sent — tracked in DB and shown on profile card |
| **Rate Limiting** | Per-user sliding-window rate limiting |
| **Domain Config** | API_DOMAIN for public proxy, API_SECRET for auth |
| **Maintenance Mode** | Toggleable live without restart |

---

## 🖼️ Welcome Card

When a user sends `/start` or `/profile`, the bot generates a **personalized card** that shows:
- 👤 User name + initial avatar (coloured circle)
- 🆔 User ID + join date
- 📊 Stats: total jobs, files sent, data volume
- 🛡️ Admin badge if applicable

The card is generated as **SVG → PNG** (cairosvg if available, Pillow fallback).
The raw SVG source can be downloaded with `/svg`.

---

## 📁 Project Structure

```
appx-uploader-bot/
├── main.py                   ← Entry point (bot + API)
├── api_server.py             ← aiohttp web management API
├── requirements.txt
├── .env.example              ← Copy → .env
├── setup.sh                  ← One-click setup
│
├── config/
│   └── settings.py           ← All settings (from .env)
│
├── database/
│   └── db.py                 ← Full async SQLite (10 tables)
│
├── bot/
│   ├── fonts.py              ← Unicode text styling helpers
│   ├── keyboards.py          ← All InlineKeyboardMarkup layouts
│   ├── image_gen.py          ← SVG welcome card + PNG generator
│   ├── drm.py                ← AppX + DRM bypass pipeline
│   ├── downloader.py         ← Retry HTTP downloader
│   ├── progress.py           ← Animated progress bar renderer
│   ├── queue_manager.py      ← Async job engine (pause/resume/cancel)
│   ├── handlers.py           ← 30+ user commands + callbacks
│   ├── admin.py              ← 30+ admin commands
│   └── utils.py              ← Rotating log setup
│
├── assets/                   ← Welcome card SVGs saved here
├── logs/                     ← bot.log + errors.log
└── temp/                     ← Temp downloads (auto-cleaned)
```

---

## 🚀 Quick Start

```bash
chmod +x setup.sh
./setup.sh
# Edit .env (set BOT_TOKEN, ADMIN_IDS)
source .venv/bin/activate
python main.py
```

---

## ⌨️ User Commands (30+)

| Command | Description |
|---------|-------------|
| `/start` | Welcome screen + profile card |
| `/help` | Full help guide |
| `/status` | Live job progress |
| `/cancel` / `/stop` | Pause current job |
| `/resume` | Resume paused job |
| `/logs` | Your activity logs |
| `/errors` | Error-only logs |
| `/clear` | Clear your logs |
| `/history` | Past 10 jobs |
| `/profile` | Your profile card (PNG) |
| `/mystats` | Download statistics |
| `/settings` | Configuration menu |
| `/login` | AppX email+password login |
| `/cookie` | Set raw AppX cookie |
| `/keys` | Add DRM KID:KEY pairs |
| `/proxy` | Set HTTP proxy |
| `/notify` | Toggle notifications |
| `/language` | Change language |
| `/check <url>` | Validate a URL + type |
| `/info <url>` | URL information |
| `/ping` | Bot latency |
| `/about` | About + global stats |
| `/version` | Version information |
| `/support` | Get support |
| `/feedback` | Send feedback to admins |
| `/export` | Export failed links (.txt) |
| `/quota` | Your usage quota |
| `/svg` | Download your profile card SVG |

---

## 🛡️ Admin Commands (30+)

| Command | Description |
|---------|-------------|
| `/admin` | Inline admin panel |
| `/stats` | Global statistics |
| `/users` | All user list |
| `/ban <id>` | Ban a user |
| `/unban <id>` | Unban a user |
| `/addadmin <id>` | Grant admin |
| `/removeadmin <id>` | Revoke admin |
| `/whitelist <id>` | Whitelist user |
| `/unwhitelist <id>` | Remove from whitelist |
| `/userinfo <id>` | Detailed user info |
| `/searchuser <q>` | Search users |
| `/topusers` | Top 10 by files |
| `/note <id> <text>` | Add note to user |
| `/jobs [status]` | All jobs |
| `/killjob <id>` | Cancel a job |
| `/killall` | Cancel all jobs |
| `/broadcast <msg>` | Send to all users |
| `/announce <msg>` | Post to channel |
| `/setkey <kid> <key>` | Add DRM key |
| `/delkey <kid>` | Delete DRM key |
| `/listkeys` | List all DRM keys |
| `/setcookie <str>` | Set AppX cookie |
| `/getcookie` | View saved cookies |
| `/alllogs` | Global log viewer |
| `/errorlogs` | Error log viewer |
| `/clearlogs [uid]` | Clear logs |
| `/maintenance [on/off]` | Toggle maintenance |
| `/setconfig <k> <v>` | Set config value |
| `/getconfig [key]` | Get config value |
| `/exportdb` | Export DB to JSON |
| `/monitor` | Live running jobs |
| `/reload` | Reload DB settings |
| `/debug` | Debug information |

---

## 🌐 Web API Panel

When `API_ENABLED=true`, the bot runs an aiohttp server on `API_PORT` (default 8080).

- Open `http://YOUR_SERVER:8080/` for the live HTML dashboard
- All `/api/*` endpoints require `X-API-Secret: YOUR_SECRET` header

### Key endpoints

```
GET  /api/stats              — Statistics
GET  /api/users              — User list
POST /api/ban                — {"user_id": 123}
GET  /api/jobs               — All jobs
POST /api/killjob            — {"job_id": "abc"}
GET  /api/drm                — DRM key list
POST /api/drm                — {"kid":"…","key":"…"}
DELETE /api/drm/{kid}        — Delete key
POST /api/cookies            — {"cookie":"…"}
POST /api/broadcast          — {"message":"…"}
POST /api/config             — {"key":"…","value":"…"}
POST /api/maintenance        — {"enable": true/false}
```

---

## 🔓 DRM Bypass Pipeline

```
URL received
    │
    ├─ AppX CDN (appx.co.in)?
    │     Strategy 1 → Direct signed URL (HEAD probe)
    │     Strategy 2 → Decode URLPrefix (base64) → real resource URL
    │     Strategy 3 → Strip Signature/KeyName/Expires
    │     Strategy 4 → Decoded + stripped
    │     Strategy 5 → CDN base rebuild
    │
    ├─ HLS (.m3u8) / DASH (.mpd)?
    │     → yt-dlp + ClearKey/Widevine DRM keys + ffmpeg merge
    │     → MPD PSSH extraction → WV KID parsing
    │
    ├─ Encrypted PDF?
    │     → pikepdf: try "" + 10 common passwords
    │
    └─ Generic HTTPS
          → Direct download with AppX browser headers + retry
```

---

## ⚙️ `.env` Reference

See `.env.example` for the full list. Key settings:

```env
BOT_TOKEN=                    # Required
ADMIN_IDS=                    # Required — comma-separated Telegram IDs

APPX_EMAIL=                   # AppX login
APPX_PASSWORD=
APPX_COOKIE=                  # Or paste cookie directly

DRM_KEYS=kid1:key1,kid2:key2  # Widevine / ClearKey pairs

API_SECRET=change_me          # Web API auth secret
API_PORT=8080                 # Web panel port

WELCOME_IMAGE_ENABLED=true    # SVG welcome card
BOT_THEME_COLOR=#6C63FF       # Card primary colour
BOT_ACCENT_COLOR=#FF6584      # Card accent colour
```

---

## 🖥️ Deploy with systemd

```ini
[Unit]
Description=AppX Uploader Bot v3
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
sudo systemctl enable --now appx-bot
sudo journalctl -u appx-bot -f
```

---

## 🐳 Docker

```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y ffmpeg libcairo2 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "main.py"]
```

```bash
docker build -t appx-bot .
docker run -d --env-file .env -p 8080:8080 --restart unless-stopped appx-bot
```

---

## 📄 License

MIT — free to use, modify, and deploy.
