# Deployment Guide — Railway + BYPASSBOSS.COM

## 1. Deploy on Railway

### One-click
[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new)

### Manual steps

```bash
# Install Railway CLI
npm i -g @railway/cli

# Login
railway login

# Create project
railway init

# Link repo or push files
railway up

# Set environment variables
railway variables set BOT_TOKEN=123456:ABC...
railway variables set ADMIN_IDS=123456789
railway variables set API_SECRET=your_strong_secret_here
railway variables set APPX_EMAIL=you@email.com
railway variables set APPX_PASSWORD=yourpassword
railway variables set DRM_KEYS=kid1:key1,kid2:key2

# Deploy
railway up
```

---

## 2. Custom Domain — BYPASSBOSS.COM (CNAME)

### Step 1 — Add custom domain in Railway

1. Open your Railway project
2. Click on the **web** service
3. Go to **Settings → Networking → Custom Domain**
4. Enter your domain: `bypassboss.com` or `api.bypassboss.com`
5. Copy the **CNAME target** shown (e.g. `xxxx.up.railway.app`)

### Step 2 — Set DNS CNAME record

At your DNS provider (Cloudflare / Namecheap / GoDaddy):

| Type  | Name          | Value                   | TTL |
|-------|---------------|-------------------------|-----|
| CNAME | @             | xxxx.up.railway.app     | 300 |
| CNAME | www           | xxxx.up.railway.app     | 300 |
| CNAME | api           | xxxx.up.railway.app     | 300 |

> If using Cloudflare: set proxy to **DNS-only (grey cloud)** for Railway HTTPS to work.

### Step 3 — Update .env

```env
API_DOMAIN=https://bypassboss.com
```

### Step 4 — SSL

Railway automatically provisions TLS (Let's Encrypt) for custom domains.
No extra configuration needed.

---

## 3. Environment Variables (Railway)

Set all of these in Railway Dashboard → Variables:

```
BOT_TOKEN          = your telegram bot token
ADMIN_IDS          = comma-separated telegram user IDs
API_SECRET         = strong random string for web API auth
API_PORT           = 8080
API_DOMAIN         = https://bypassboss.com

APPX_EMAIL         = appx account email
APPX_PASSWORD      = appx account password
APPX_COOKIE        = (optional) raw cookie string

DRM_KEYS           = kid1:key1,kid2:key2

DB_PATH            = /data/appx_bot.db   ← use Railway volume
TEMP_DIR           = /tmp/appxbot
LOG_DIR            = /data/logs

WELCOME_IMAGE_ENABLED = true
```

---

## 4. Railway Volume (Persistent DB)

By default Railway uses ephemeral storage. Attach a volume for persistent SQLite:

1. Railway Dashboard → your service → **Volumes**
2. Create volume mounted at `/data`
3. Set `DB_PATH=/data/appx_bot.db`
4. Set `LOG_DIR=/data/logs`

---

## 5. Bypass API Usage

Your bot exposes its own bypass resolver at:

```
POST https://bypassboss.com/api/bypass
X-API-Secret: your_api_secret

{
  "url": "https://static-db-v2.appx.co.in/paid_course4/file.pdf?URLPrefix=...&Signature=...",
  "cookie": "token=eyJhbGciOi...",
  "drm_keys": {}
}
```

Response:
```json
{
  "ok": true,
  "url": "https://static-db-v2.appx.co.in/paid_course4/file.pdf",
  "original": "https://...",
  "strategy": "decoded-prefix",
  "kind": "appx",
  "headers": {
    "User-Agent": "...",
    "Referer": "https://appx.co.in/"
  }
}
```

---

## 6. Health Check

Railway uses `GET /health` for health monitoring.
It returns the dashboard HTML with live stats.

---

## 7. Docker (Self-hosted alternative)

```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y ffmpeg libcairo2-dev pkg-config && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 8080
CMD ["python", "main.py"]
```

```bash
docker build -t bypassboss .
docker run -d \
  --env-file .env \
  -p 8080:8080 \
  -v $(pwd)/data:/data \
  --restart unless-stopped \
  --name bypassboss \
  bypassboss
```

---

## 8. Systemd (VPS)

```ini
[Unit]
Description=BypassBoss Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/appx-uploader-bot
ExecStart=/home/ubuntu/appx-uploader-bot/.venv/bin/python main.py
Restart=always
RestartSec=10
EnvironmentFile=/home/ubuntu/appx-uploader-bot/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now bypassboss
sudo journalctl -u bypassboss -f
```
