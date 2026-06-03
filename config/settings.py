import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────────────────────────
BOT_TOKEN         = os.getenv("BOT_TOKEN", "8614102555:AAHy0mMiBDF0CYcHtDGpfNQW4nIIe1J5-Uc")
ADMIN_IDS         = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "8525952693").split(",") if x.strip().isdigit()]
BOT_NAME          = os.getenv("BOT_NAME", "AppX Uploader Bot")
BOT_USERNAME      = os.getenv("BOT_USERNAME", "appx_uploader_bot")
SUPPORT_USERNAME  = os.getenv("SUPPORT_USERNAME", "@funnytamilan")
CHANNEL_ID        = os.getenv("CHANNEL_ID", "-1003505154626")

# ── AppX credentials ──────────────────────────────────────────────────────────
APPX_EMAIL        = os.getenv("APPX_EMAIL", "")
APPX_PASSWORD     = os.getenv("APPX_PASSWORD", "")
APPX_COOKIE       = os.getenv("APPX_COOKIE", "")
APPX_LOGIN_URL    = "https://api.appx.co.in/api/v1/user/login"
APPX_BASE_URL     = "https://appx.co.in"
APPX_CDN_BASE     = "https://static-db-v2.appx.co.in"
APPX_API_BASE     = "https://api.appx.co.in"

# ── DRM keys ─────────────────────────────────────────────────────────────────
_drm_raw = os.getenv("DRM_KEYS", "")
DRM_KEYS: dict = {}
for _p in _drm_raw.split(","):
    _p = _p.strip()
    if ":" in _p:
        _k, _v = _p.split(":", 1)
        DRM_KEYS[_k.strip().lower()] = _v.strip().lower()

# ── yt-dlp ────────────────────────────────────────────────────────────────────
YTDLP_COOKIES_FILE  = os.getenv("YTDLP_COOKIES_FILE", "cookies.txt")
YTDLP_EXTRA_ARGS    = os.getenv("YTDLP_EXTRA_ARGS", "")
YTDLP_CONCURRENCY   = int(os.getenv("YTDLP_CONCURRENCY", "8"))

# ── Network ───────────────────────────────────────────────────────────────────
HTTP_PROXY        = os.getenv("HTTP_PROXY", "") or None
USER_AGENT        = os.getenv("USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# ── Web API server ────────────────────────────────────────────────────────────
API_HOST          = os.getenv("API_HOST", "0.0.0.0")
API_PORT          = int(os.getenv("API_PORT", "8080"))
API_SECRET        = os.getenv("API_SECRET", "change_me_in_production")
API_DOMAIN        = os.getenv("API_DOMAIN", "https://apixapp.up.Railway.app")
API_ENABLED       = os.getenv("API_ENABLED", "true").lower() == "true"

# Bypass API (own resolution service)
BYPASS_API_URL    = os.getenv("BYPASS_API_URL", "")   # e.g. https://bypassboss.com/api/resolve
BYPASS_API_KEY    = os.getenv("BYPASS_API_KEY", "")

# ── Paths ─────────────────────────────────────────────────────────────────────
DB_PATH           = os.getenv("DB_PATH", "appx_bot.db")
TEMP_DIR          = os.getenv("TEMP_DIR", "temp")
LOG_DIR           = os.getenv("LOG_DIR", "logs")
ASSETS_DIR        = os.getenv("ASSETS_DIR", "assets")

# ── Download speed settings ───────────────────────────────────────────────────
MAX_RETRIES          = int(os.getenv("MAX_RETRIES", "5"))
RETRY_DELAY          = int(os.getenv("RETRY_DELAY", "3"))
DOWNLOAD_TIMEOUT     = int(os.getenv("DOWNLOAD_TIMEOUT", "900"))
MAX_FILE_SIZE_MB     = int(os.getenv("MAX_FILE_SIZE_MB", "4096"))
CHUNK_SIZE           = int(os.getenv("CHUNK_SIZE", str(8 * 1024 * 1024)))   # 8 MB
MAX_CONNECTIONS      = int(os.getenv("MAX_CONNECTIONS", "16"))
MAX_CONNECTIONS_PER_HOST = int(os.getenv("MAX_CONNECTIONS_PER_HOST", "8"))
CONNECT_TIMEOUT      = int(os.getenv("CONNECT_TIMEOUT", "20"))
READ_TIMEOUT         = int(os.getenv("READ_TIMEOUT", "300"))

# ── Rate limiting ─────────────────────────────────────────────────────────────
RATE_LIMIT_CALLS  = int(os.getenv("RATE_LIMIT_CALLS", "25"))
RATE_LIMIT_PERIOD = int(os.getenv("RATE_LIMIT_PERIOD", "60"))

# ── Access control ────────────────────────────────────────────────────────────
ADMIN_ONLY_MODE       = os.getenv("ADMIN_ONLY_MODE", "false").lower() == "true"
REQUIRE_JOIN_CHANNEL  = os.getenv("REQUIRE_JOIN_CHANNEL", "").strip().lstrip("@") or None
MAX_JOBS_PER_USER     = int(os.getenv("MAX_JOBS_PER_USER", "1"))
MAINTENANCE_MODE      = os.getenv("MAINTENANCE_MODE", "false").lower() == "true"

# ── UI / image ────────────────────────────────────────────────────────────────
WELCOME_IMAGE_ENABLED = os.getenv("WELCOME_IMAGE_ENABLED", "true").lower() == "true"
BOT_THEME_COLOR       = os.getenv("BOT_THEME_COLOR", "#6C63FF")
BOT_ACCENT_COLOR      = os.getenv("BOT_ACCENT_COLOR", "#FF6584")
BOT_LOGO_EMOJI        = os.getenv("BOT_LOGO_EMOJI", "🤖")

# ── AppX CDN headers (simulates browser on appx.co.in) ───────────────────────
APPX_HEADERS = {
    "User-Agent":         USER_AGENT,
    "Accept":             "*/*",
    "Accept-Language":    "en-US,en;q=0.9,hi;q=0.8",
    "Accept-Encoding":    "gzip, deflate, br",
    "Connection":         "keep-alive",
    "Referer":            "https://appx.co.in/",
    "Origin":             "https://appx.co.in",
    "sec-ch-ua":          '"Chromium";v="124","Google Chrome";v="124"',
    "sec-ch-ua-mobile":   "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest":     "empty",
    "Sec-Fetch-Mode":     "cors",
    "Sec-Fetch-Site":     "same-site",
    "DNT":                "1",
    "Pragma":             "no-cache",
    "Cache-Control":      "no-cache",
}
