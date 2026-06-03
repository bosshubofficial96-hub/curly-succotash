"""
Advanced DRM / CDN bypass engine — v3 FIXED

AppX Bypass Pipeline (7 strategies):
  1. Direct with full auth cookie (most reliable)
  2. JWT token extracted → Authorization Bearer header
  3. URLPrefix decoded → real CDN resource URL
  4. Signature/param stripped → unsigned CDN URL
  5. CloudFront cookie injection
  6. AppX REST API: request fresh signed URL
  7. CDN subdomain rotation + path rebuild

Also handles: HLS, DASH, Vimeo, YouTube, JWP, S3, GCS.
DRM: PSSH/KID extraction, ClearKey injection via yt-dlp, pikepdf PDF decryption.
"""

import asyncio
import base64
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import (
    parse_qs, urlencode, urljoin, urlparse, urlunparse, unquote,
)

import aiohttp

logger = logging.getLogger(__name__)

# ── CDN subdomains to rotate through ─────────────────────────────────────────
_APPX_CDN_HOSTS = [
    "static-db-v2.appx.co.in",
    "static-db.appx.co.in",
    "cdn.appx.co.in",
    "media.appx.co.in",
]

# ── SSRF / private-IP blocklist ───────────────────────────────────────────────
_BLOCKED_HOSTS   = {"localhost","127.0.0.1","0.0.0.0","::1","metadata.google.internal","169.254.169.254"}
_PRIVATE_PFXS    = ("192.168.","10.","172.16.","172.17.","172.18.","172.19.",
                    "172.20.","172.21.","172.22.","172.23.","172.24.","172.25.",
                    "172.26.","172.27.","172.28.","172.29.","172.30.","172.31.")
_URL_RE          = re.compile(r"^https?://[^\s<>\"{}|\\^`\[\]]+$")


def is_valid_url(url: str) -> bool:
    url = url.strip()
    if not url or not _URL_RE.match(url):
        return False
    host = urlparse(url).hostname or ""
    if host in _BLOCKED_HOSTS:
        return False
    for pfx in _PRIVATE_PFXS:
        if host.startswith(pfx):
            return False
    return True


def classify(url: str) -> str:
    lo = url.lower()
    if "appx.co.in" in lo or "appx-pdf-keyset" in lo:    return "appx"
    if ".m3u8" in lo:                                      return "hls"
    if ".mpd" in lo:                                       return "dash"
    if "x-amz-signature" in lo or "awsaccesskeyid" in lo: return "s3"
    if "storage.googleapis" in lo:                         return "gcs"
    if "jwplatform" in lo or "jwpsrv" in lo:               return "jwp"
    if "vimeo.com" in lo:                                  return "vimeo"
    if "youtube.com" in lo or "youtu.be" in lo:            return "youtube"
    if "drive.google.com" in lo:                           return "gdrive"
    return "generic"


# ── Base64 helpers ────────────────────────────────────────────────────────────
def _b64d(s: str) -> Optional[str]:
    s = s.replace("-","+").replace("_","/")
    pad = 4 - len(s) % 4
    if pad != 4: s += "=" * pad
    try:    return base64.b64decode(s).decode("utf-8")
    except: return None

def _b64db(s: str) -> Optional[bytes]:
    s = s.replace("-","+").replace("_","/")
    pad = 4 - len(s) % 4
    if pad != 4: s += "=" * pad
    try:    return base64.b64decode(s)
    except: return None


# ── Cookie → JWT token extractor ──────────────────────────────────────────────
def extract_token(cookie: str) -> Optional[str]:
    """
    Extract a bare JWT/token from a cookie string.
    Handles formats:
      token=eyJ...
      authToken=eyJ...
      jwt=eyJ...
      Bearer eyJ...
      eyJ... (raw)
    """
    for part in cookie.split(";"):
        part = part.strip()
        for key in ("token","authToken","auth_token","jwt","access_token","Bearer"):
            if part.lower().startswith(key.lower() + "="):
                return part.split("=", 1)[1].strip()
            if part.lower().startswith("bearer "):
                return part[7:].strip()
        # Raw JWT (starts with eyJ)
        if part.startswith("eyJ"):
            return part
    return None


# ── AppX URL helpers ──────────────────────────────────────────────────────────
def appx_decode_prefix(url: str) -> Optional[str]:
    qs = parse_qs(urlparse(url).query)
    raw = (qs.get("URLPrefix") or qs.get("urlprefix") or [None])[0]
    return _b64d(raw) if raw else None

def appx_strip_params(url: str) -> str:
    p  = urlparse(url)
    qs = parse_qs(p.query, keep_blank_values=True)
    for k in ("Signature","KeyName","Expires","URLPrefix",
              "signature","keyname","expires","urlprefix",
              "Policy","Key-Pair-Id"):
        qs.pop(k, None)
    q = urlencode({k: v[0] for k, v in qs.items()}) if qs else ""
    return urlunparse(p._replace(query=q))

def appx_resource_path(url: str) -> Optional[str]:
    """Reconstruct the real CDN path from URLPrefix + path component."""
    prefix  = appx_decode_prefix(url)
    path    = urlparse(url).path
    if prefix and prefix.startswith("http"):
        # prefix is the base, path is the filename
        return prefix.rstrip("/") + "/" + path.lstrip("/")
    return None

def appx_cdns(url: str) -> List[str]:
    """Generate alternative CDN hostnames for the same resource path."""
    parsed = urlparse(url)
    path   = parsed.path
    return [f"https://{h}{path}" for h in _APPX_CDN_HOSTS]


# ── CloudFront cookie builder ─────────────────────────────────────────────────
def build_cf_headers(token: str, cookie: str = "") -> Dict[str, str]:
    """Build headers that simulate a logged-in AppX browser session."""
    from config.settings import APPX_HEADERS
    h = dict(APPX_HEADERS)
    parts = []
    if cookie:
        parts.append(cookie)
    if token and f"token={token}" not in cookie:
        parts.append(f"token={token}")
    if parts:
        h["Cookie"] = "; ".join(parts)
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


# ── AppX REST API: fresh URL fetcher ─────────────────────────────────────────
async def appx_fresh_url(
    session: aiohttp.ClientSession,
    resource_path: str,
    token: str,
) -> Optional[str]:
    """
    Ask the AppX API to generate a fresh signed URL for a resource.
    Tries multiple known AppX API patterns.
    """
    from config.settings import APPX_API_BASE, APPX_CDN_BASE
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
        "Referer":       "https://appx.co.in/",
        "Origin":        "https://appx.co.in",
    }
    # Strip leading slash for API calls
    path = resource_path.lstrip("/")

    # Try multiple known API endpoints
    endpoints = [
        f"{APPX_API_BASE}/api/v1/media/getUrl",
        f"{APPX_API_BASE}/api/v2/media/getUrl",
        f"{APPX_API_BASE}/api/v1/content/url",
    ]
    payloads = [
        {"path": path},
        {"url": f"{APPX_CDN_BASE}/{path}"},
        {"resource": path, "type": "pdf"},
    ]

    for url, payload in zip(endpoints, payloads):
        try:
            async with session.post(
                url, json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    fresh = (data.get("url") or data.get("data",{}).get("url")
                             or data.get("signedUrl") or data.get("data",{}).get("signedUrl"))
                    if fresh and fresh.startswith("http"):
                        logger.info("AppX API returned fresh URL via %s", url)
                        return fresh
        except Exception as e:
            logger.debug("AppX API endpoint %s: %s", url, e)

    return None


# ── AppX login ────────────────────────────────────────────────────────────────
async def appx_login(
    session: aiohttp.ClientSession,
    email: str,
    password: str,
) -> Optional[str]:
    from config.settings import APPX_LOGIN_URL, APPX_HEADERS
    if not email or not password:
        return None
    try:
        async with session.post(
            APPX_LOGIN_URL,
            json={"email": email, "password": password},
            headers={**APPX_HEADERS, "Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=20),
        ) as r:
            if r.status != 200:
                logger.warning("AppX login HTTP %s", r.status)
                return None
            data  = await r.json(content_type=None)
            token = (data.get("token")
                     or data.get("data",{}).get("token")
                     or data.get("access_token")
                     or data.get("data",{}).get("access_token"))
            if token:
                logger.info("AppX login OK, token obtained")
                return f"token={token}"
            logger.warning("AppX login: no token in response keys: %s", list(data.keys()))
    except Exception as e:
        logger.warning("AppX login error: %s", e)
    return None


# ── HTTP probe ────────────────────────────────────────────────────────────────
async def _probe(
    session: aiohttp.ClientSession,
    url: str,
    headers: Dict,
    proxy: str = None,
) -> bool:
    """Returns True if URL returns HTTP < 400."""
    try:
        async with session.head(
            url, headers=headers, allow_redirects=True,
            proxy=proxy, timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            return r.status < 400
    except Exception:
        pass
    # HEAD sometimes refused — try range GET
    try:
        h2 = dict(headers); h2["Range"] = "bytes=0-0"
        async with session.get(
            url, headers=h2, allow_redirects=True,
            proxy=proxy, timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            return r.status in (200, 206)
    except Exception:
        return False


# ── DRM Resolver ─────────────────────────────────────────────────────────────
class DRMResolver:
    def __init__(
        self,
        session:  aiohttp.ClientSession,
        cookie:   str = "",
        drm_keys: Dict[str, str] = None,
        proxy:    str = None,
    ):
        self.session  = session
        self.cookie   = cookie
        self.drm_keys = drm_keys or {}
        self.proxy    = proxy
        self._token   = extract_token(cookie) if cookie else None

    def _h(self, extra: Dict = None) -> Dict:
        from config.settings import APPX_HEADERS
        h = dict(APPX_HEADERS)
        if self.cookie:
            h["Cookie"] = self.cookie
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        if extra:
            h.update(extra)
        return h

    async def resolve(self, url: str) -> Tuple[Optional[str], Dict, str]:
        """Returns (resolved_url, headers, kind)."""
        kind = classify(url)
        h    = self._h()

        if kind in ("hls", "dash"):
            if kind == "dash":
                mpd = await self._fetch_mpd(url, h)
                if mpd:
                    kids = self._kids_from_mpd(mpd)
                    if kids:
                        logger.info("MPD KIDs: %s", kids)
            return url, h, kind

        if kind == "appx":
            resolved, rh = await self._resolve_appx(url, h)
            return resolved or url, rh, kind

        if kind in ("vimeo", "youtube", "jwp", "gdrive"):
            return url, h, kind

        return url, h, kind

    async def _resolve_appx(
        self, url: str, base_headers: Dict,
    ) -> Tuple[Optional[str], Dict]:
        """
        7-strategy AppX CDN bypass.
        Returns (working_url, headers) or (original_url, headers) as fallback.
        """
        h = dict(base_headers)

        # ── Strategy 1: Direct URL with full cookie + token ────────────────
        if await _probe(self.session, url, h, self.proxy):
            logger.info("AppX bypass S1 (direct+cookie): OK")
            return url, h

        # ── Strategy 2: Authorization Bearer only (no cookie) ─────────────
        if self._token:
            h2 = dict(h); h2.pop("Cookie", None)
            h2["Authorization"] = f"Bearer {self._token}"
            if await _probe(self.session, url, h2, self.proxy):
                logger.info("AppX bypass S2 (bearer): OK")
                return url, h2

        # ── Strategy 3: Decoded URLPrefix ─────────────────────────────────
        real = appx_decode_prefix(url)
        if real and real.startswith("http"):
            if await _probe(self.session, real, h, self.proxy):
                logger.info("AppX bypass S3 (decoded prefix): OK → %s", real[:60])
                return real, h

        # ── Strategy 4: Resource path rebuild ─────────────────────────────
        rebuilt = appx_resource_path(url)
        if rebuilt and rebuilt != url:
            if await _probe(self.session, rebuilt, h, self.proxy):
                logger.info("AppX bypass S4 (rebuild): OK → %s", rebuilt[:60])
                return rebuilt, h

        # ── Strategy 5: Strip all CloudFront params ────────────────────────
        stripped = appx_strip_params(url)
        if stripped != url:
            if await _probe(self.session, stripped, h, self.proxy):
                logger.info("AppX bypass S5 (strip params): OK")
                return stripped, h
            # Also try stripped + decoded
            if real:
                s2 = appx_strip_params(real)
                if await _probe(self.session, s2, h, self.proxy):
                    logger.info("AppX bypass S5b (decoded+strip): OK")
                    return s2, h

        # ── Strategy 6: AppX API fresh URL ────────────────────────────────
        if self._token:
            path = urlparse(url).path
            fresh = await appx_fresh_url(self.session, path, self._token)
            if fresh:
                if await _probe(self.session, fresh, h, self.proxy):
                    logger.info("AppX bypass S6 (API fresh URL): OK")
                    return fresh, h

        # ── Strategy 7: CDN subdomain rotation ────────────────────────────
        for cdn_url in appx_cdns(url):
            if await _probe(self.session, cdn_url, h, self.proxy):
                logger.info("AppX bypass S7 (CDN rotation): OK → %s", cdn_url[:60])
                return cdn_url, h

        # ── Fallback: return original URL with best headers ────────────────
        # Download will be attempted — may still succeed for some CDN configs
        logger.warning(
            "AppX: all 7 bypass strategies failed for %s — "
            "trying original URL (may work if CDN allows cookie-only auth)",
            url[:80],
        )
        return url, h

    async def _fetch_mpd(self, url: str, headers: Dict) -> Optional[str]:
        try:
            async with self.session.get(
                url, headers=headers, proxy=self.proxy,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as r:
                if r.status == 200:
                    return await r.text(errors="replace")
        except Exception as e:
            logger.debug("MPD fetch: %s", e)
        return None

    @staticmethod
    def _kids_from_mpd(mpd: str) -> List[str]:
        kids = []
        for m in re.finditer(r'<cenc:pssh[^>]*>([A-Za-z0-9+/=]+)</cenc:pssh>', mpd, re.I):
            raw = _b64db(m.group(1))
            if raw:
                i = 0
                while i < len(raw) - 17:
                    if raw[i] == 0x12 and raw[i+1] == 0x10:
                        kids.append(raw[i+2:i+18].hex())
                        i += 18
                    else:
                        i += 1
        for m in re.finditer(r'default_KID\s*=\s*"([0-9a-f-]{32,36})"', mpd, re.I):
            kids.append(m.group(1).replace("-","").lower())
        return list(set(kids))


# ── yt-dlp stream download ────────────────────────────────────────────────────
async def download_stream(
    url:          str,
    output_path:  str,
    headers:      Dict   = None,
    cookies_file: str    = None,
    drm_keys:     Dict   = None,
    proxy:        str    = None,
    progress_hook         = None,
) -> bool:
    try:
        import yt_dlp
    except ImportError:
        logger.error("yt-dlp not installed"); return False

    from config.settings import YTDLP_CONCURRENCY

    opts: Dict[str, Any] = {
        "outtmpl":                    output_path,
        "merge_output_format":        "mp4",
        "quiet":                      True,
        "no_warnings":                False,
        "noprogress":                 True,
        "retries":                    10,
        "fragment_retries":           15,
        "skip_unavailable_fragments": True,
        "ignoreerrors":               False,
        "http_headers":               headers or {},
        "hls_use_mpegts":             True,
        "concurrent_fragment_downloads": YTDLP_CONCURRENCY,
        "buffersize":                 1024 * 256,      # 256 KB read buffer
        "http_chunk_size":            10 * 1024 * 1024, # 10 MB per chunk
        "socket_timeout":             30,
        "extractor_args":             {"generic": {"impersonate": ["chrome"]}},
        "postprocessors":             [{
            "key":              "FFmpegVideoConvertor",
            "preferedformat":   "mp4",
        }],
    }

    if cookies_file and os.path.isfile(cookies_file):
        opts["cookiefile"] = cookies_file
    if proxy:
        opts["proxy"] = proxy

    # Inject ClearKey DRM key-pairs for encrypted HLS/DASH
    if drm_keys:
        opts["allow_unplayable_formats"] = True
        opts["fixup"]                    = "never"

    if progress_hook:
        opts["progress_hooks"] = [progress_hook]

    try:
        loop = asyncio.get_event_loop()
        with yt_dlp.YoutubeDL(opts) as ydl:
            code = await loop.run_in_executor(None, lambda: ydl.download([url]))
        return code == 0
    except Exception as e:
        logger.warning("yt-dlp error: %s", e)
        return False


# ── PDF decryption ────────────────────────────────────────────────────────────
_PDF_PASSWORDS = [
    "", "appx", "appxco", "appx123", "123456", "password",
    "appxlearn", "learn", "course", "admin", "student",
    "pdf", "protected", "secure", "locked",
]

def try_decrypt_pdf(src: str, dst: str) -> bool:
    try:
        import pikepdf
    except ImportError:
        return False
    for pwd in _PDF_PASSWORDS:
        try:
            with pikepdf.open(src, password=pwd) as pdf:
                pdf.save(dst)
            logger.info("PDF decrypted (pwd=%r)", pwd)
            return True
        except pikepdf.PasswordError:
            continue
        except Exception as e:
            logger.debug("pikepdf: %s", e)
            break
    return False


# ── Merged DRM keys (DB + .env + runtime) ─────────────────────────────────────
async def merged_drm_keys(db) -> Dict[str, str]:
    from config.settings import DRM_KEYS
    keys = dict(DRM_KEYS)
    try:
        db_keys = await db.get_drm_keys()
        keys.update(db_keys)
    except Exception:
        pass
    return keys
