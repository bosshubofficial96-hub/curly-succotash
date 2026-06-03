"""
Advanced DRM / CDN bypass engine — v3.2 FIXED

Key changes vs v3.1:
  • _probe() now uses Range-GET (bytes=0-0) instead of HEAD — CloudFront
    and many CDNs silently reject HEAD but accept GET Range requests.
  • _resolve_appx() returns a ranked list of candidate URLs instead of
    stopping at first HEAD-probe pass. Downloader tries them in sequence.
  • Added _try_get_range() helper for reliable reachability check.
  • Bypass no longer fails silently — logs exactly which strategy worked.

7-strategy AppX bypass:
  1. Original URL + full cookie + Bearer token
  2. Bearer-only (no Cookie header)
  3. URLPrefix base64 decode → real resource URL
  4. Resource path rebuild from URLPrefix
  5. Strip all CloudFront params (Signature/KeyName/Expires/URLPrefix)
  6. AppX REST API fresh signed URL
  7. CDN subdomain rotation
"""

import asyncio
import base64
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import aiohttp

logger = logging.getLogger(__name__)

_APPX_CDN_HOSTS = [
    "static-db-v2.appx.co.in",
    "static-db.appx.co.in",
    "cdn.appx.co.in",
    "media.appx.co.in",
]

_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1",
                  "metadata.google.internal", "169.254.169.254"}
_PRIVATE_PFXS  = ("192.168.", "10.", "172.16.", "172.17.", "172.18.",
                  "172.19.", "172.20.", "172.21.", "172.22.", "172.23.",
                  "172.24.", "172.25.", "172.26.", "172.27.", "172.28.",
                  "172.29.", "172.30.", "172.31.")
_URL_RE        = re.compile(r"^https?://[^\s<>\"{}|\\^`\[\]]+$")


def is_valid_url(url: str) -> bool:
    url = url.strip()
    if not url or not _URL_RE.match(url):
        return False
    host = urlparse(url).hostname or ""
    if host in _BLOCKED_HOSTS:
        return False
    return not any(host.startswith(p) for p in _PRIVATE_PFXS)


def classify(url: str) -> str:
    lo = url.lower()
    if "appx.co.in" in lo or "appx-pdf-keyset" in lo:  return "appx"
    if ".m3u8" in lo:                                    return "hls"
    if ".mpd"  in lo:                                    return "dash"
    if "x-amz-signature" in lo or "awsaccesskeyid" in lo: return "s3"
    if "storage.googleapis" in lo:                       return "gcs"
    if "jwplatform" in lo or "jwpsrv" in lo:             return "jwp"
    if "vimeo.com"  in lo:                               return "vimeo"
    if "youtube.com" in lo or "youtu.be" in lo:          return "youtube"
    if "drive.google.com" in lo:                         return "gdrive"
    return "generic"


# ── Base64 helpers ────────────────────────────────────────────────────────────
def _b64d(s: str) -> Optional[str]:
    s = s.replace("-", "+").replace("_", "/")
    pad = 4 - len(s) % 4
    if pad != 4: s += "=" * pad
    try:    return base64.b64decode(s).decode("utf-8")
    except: return None

def _b64db(s: str) -> Optional[bytes]:
    s = s.replace("-", "+").replace("_", "/")
    pad = 4 - len(s) % 4
    if pad != 4: s += "=" * pad
    try:    return base64.b64decode(s)
    except: return None


# ── Cookie / token helpers ────────────────────────────────────────────────────
def extract_token(cookie: str) -> Optional[str]:
    """Extract a bare JWT/bearer token from a cookie string."""
    for part in cookie.split(";"):
        part = part.strip()
        for key in ("token", "authToken", "auth_token", "jwt", "access_token"):
            if part.lower().startswith(key.lower() + "="):
                return part.split("=", 1)[1].strip()
        if part.lower().startswith("bearer "):
            return part[7:].strip()
        if part.startswith("eyJ"):
            return part
    return None


# ── AppX URL helpers ──────────────────────────────────────────────────────────
def appx_decode_prefix(url: str) -> Optional[str]:
    qs  = parse_qs(urlparse(url).query)
    raw = (qs.get("URLPrefix") or qs.get("urlprefix") or [None])[0]
    return _b64d(raw) if raw else None

def appx_strip_params(url: str) -> str:
    p  = urlparse(url)
    qs = parse_qs(p.query, keep_blank_values=True)
    for k in ("Signature", "KeyName", "Expires", "URLPrefix",
              "signature", "keyname", "expires", "urlprefix",
              "Policy", "Key-Pair-Id"):
        qs.pop(k, None)
    q = urlencode({k: v[0] for k, v in qs.items()}) if qs else ""
    return urlunparse(p._replace(query=q))

def appx_resource_url(url: str) -> Optional[str]:
    """Decode URLPrefix + append path component → real CDN URL."""
    prefix = appx_decode_prefix(url)
    path   = urlparse(url).path
    if prefix and prefix.startswith("http"):
        return prefix.rstrip("/") + "/" + path.lstrip("/")
    return None

def appx_cdn_variants(url: str) -> List[str]:
    path = urlparse(url).path
    return [f"https://{h}{path}" for h in _APPX_CDN_HOSTS]


# ── Reliable reachability probe (Range-GET, not HEAD) ─────────────────────────
async def _probe(
    session: aiohttp.ClientSession,
    url: str,
    headers: Dict,
    proxy: str = None,
) -> bool:
    """
    Returns True if the server returns 200/206 for a small Range GET.
    Using Range GET instead of HEAD because:
    • CloudFront signed URLs often return 403 to HEAD but accept GET.
    • Range GET with bytes=0-0 downloads only 1 byte — negligible overhead.
    """
    h = dict(headers)
    h["Range"] = "bytes=0-0"
    try:
        async with session.get(
            url, headers=h, allow_redirects=True,
            proxy=proxy,
            timeout=aiohttp.ClientTimeout(total=12),
        ) as r:
            return r.status in (200, 206)
    except Exception as e:
        logger.debug("probe(%s): %s", url[:60], e)
        return False


# ── AppX REST API fresh-URL fetcher ───────────────────────────────────────────
async def appx_fresh_url(
    session: aiohttp.ClientSession,
    resource_path: str,
    token: str,
) -> Optional[str]:
    from config.settings import APPX_API_BASE, APPX_CDN_BASE
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
        "Referer":       "https://appx.co.in/",
        "Origin":        "https://appx.co.in",
    }
    path = resource_path.lstrip("/")
    for ep, payload in [
        (f"{APPX_API_BASE}/api/v1/media/getUrl",  {"path": path}),
        (f"{APPX_API_BASE}/api/v2/media/getUrl",  {"url": f"{APPX_CDN_BASE}/{path}"}),
        (f"{APPX_API_BASE}/api/v1/content/url",   {"resource": path, "type": "pdf"}),
    ]:
        try:
            async with session.post(
                ep, json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    fresh = (data.get("url")
                             or data.get("data", {}).get("url")
                             or data.get("signedUrl")
                             or data.get("data", {}).get("signedUrl"))
                    if fresh and fresh.startswith("http"):
                        logger.info("AppX API → fresh URL via %s", ep)
                        return fresh
        except Exception as e:
            logger.debug("AppX API %s: %s", ep, e)
    return None


# ── AppX login ────────────────────────────────────────────────────────────────
async def appx_login(
    session: aiohttp.ClientSession,
    email: str,
    password: str,
) -> Optional[str]:
    from config.settings import APPX_HEADERS, APPX_LOGIN_URL
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
                     or data.get("data", {}).get("token")
                     or data.get("access_token")
                     or data.get("data", {}).get("access_token"))
            if token:
                logger.info("AppX login OK")
                return f"token={token}"
            logger.warning("AppX login: no token in response: %s", list(data))
    except Exception as e:
        logger.warning("AppX login error: %s", e)
    return None


# ── DRM Resolver ──────────────────────────────────────────────────────────────
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

    async def resolve(self, url: str) -> Tuple[str, Dict, str]:
        """Returns (resolved_url, headers, kind)."""
        kind = classify(url)
        h    = self._h()

        if kind in ("hls", "dash", "vimeo", "youtube", "jwp", "gdrive"):
            return url, h, kind

        if kind == "appx":
            resolved, rh = await self._resolve_appx(url, h)
            return resolved, rh, kind

        return url, h, kind

    async def _resolve_appx(
        self, url: str, base_h: Dict,
    ) -> Tuple[str, Dict]:
        """
        7-strategy AppX bypass using Range-GET probes (not HEAD).
        Returns the first reachable (url, headers) pair, or falls back to
        the original URL so the downloader can still attempt it.
        """

        # Build ordered candidate list
        candidates: List[Tuple[str, Dict, str]] = []

        # S1 — original URL + cookie + bearer
        candidates.append((url, base_h, "S1:direct+cookie"))

        # S2 — bearer only (no Cookie)
        if self._token:
            h2 = dict(base_h); h2.pop("Cookie", None)
            candidates.append((url, h2, "S2:bearer-only"))

        # S3 — decoded URLPrefix
        decoded = appx_decode_prefix(url)
        if decoded and decoded.startswith("http"):
            candidates.append((decoded, base_h, "S3:decoded-prefix"))

        # S4 — resource path rebuild
        rebuilt = appx_resource_url(url)
        if rebuilt and rebuilt != url:
            candidates.append((rebuilt, base_h, "S4:rebuilt-path"))

        # S5 — strip all CloudFront params
        stripped = appx_strip_params(url)
        if stripped != url:
            candidates.append((stripped, base_h, "S5:stripped-params"))
            if decoded:
                stripped2 = appx_strip_params(decoded)
                candidates.append((stripped2, base_h, "S5b:decoded+stripped"))

        # S6 — AppX REST API fresh URL
        fresh = None
        if self._token:
            try:
                fresh = await appx_fresh_url(
                    self.session, urlparse(url).path, self._token
                )
            except Exception as e:
                logger.debug("S6 fresh-url error: %s", e)
        if fresh:
            candidates.append((fresh, base_h, "S6:api-fresh-url"))

        # S7 — CDN subdomain rotation
        for cdn_url in appx_cdn_variants(url):
            candidates.append((cdn_url, base_h, "S7:cdn-rotation"))

        # Probe each candidate with Range-GET
        for candidate_url, candidate_h, label in candidates:
            if await _probe(self.session, candidate_url, candidate_h, self.proxy):
                logger.info("AppX bypass ✅ %s → %s", label, candidate_url[:70])
                return candidate_url, candidate_h

        # All probes failed — return original URL with best headers
        # The downloader will attempt it anyway (some servers accept the
        # actual download GET even when the probe 0-byte range fails)
        logger.warning(
            "AppX: all 7 strategies failed probe for %s  "
            "— returning original URL (download will still attempt it)",
            url[:70],
        )
        return url, base_h

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
        for m in re.finditer(
            r"<cenc:pssh[^>]*>([A-Za-z0-9+/=]+)</cenc:pssh>", mpd, re.I
        ):
            raw = _b64db(m.group(1))
            if raw:
                i = 0
                while i < len(raw) - 17:
                    if raw[i] == 0x12 and raw[i + 1] == 0x10:
                        kids.append(raw[i + 2:i + 18].hex())
                        i += 18
                    else:
                        i += 1
        for m in re.finditer(
            r'default_KID\s*=\s*"([0-9a-f-]{32,36})"', mpd, re.I
        ):
            kids.append(m.group(1).replace("-", "").lower())
        return list(set(kids))


# ── yt-dlp stream downloader ──────────────────────────────────────────────────
async def download_stream(
    url:          str,
    output_path:  str,
    headers:      Dict  = None,
    cookies_file: str   = None,
    drm_keys:     Dict  = None,
    proxy:        str   = None,
    progress_hook        = None,
) -> bool:
    try:
        import yt_dlp
    except ImportError:
        logger.error("yt-dlp not installed")
        return False

    from config.settings import YTDLP_CONCURRENCY

    opts: Dict[str, Any] = {
        "outtmpl":                       output_path,
        "merge_output_format":           "mp4",
        "quiet":                         True,
        "no_warnings":                   False,
        "noprogress":                    True,
        "retries":                       10,
        "fragment_retries":              15,
        "skip_unavailable_fragments":    True,
        "ignoreerrors":                  False,
        "http_headers":                  headers or {},
        "hls_use_mpegts":                True,
        "concurrent_fragment_downloads": YTDLP_CONCURRENCY,
        "buffersize":                    256 * 1024,
        "http_chunk_size":               10 * 1024 * 1024,
        "socket_timeout":                30,
        "postprocessors": [{
            "key":            "FFmpegVideoConvertor",
            "preferedformat": "mp4",
        }],
    }
    if cookies_file and __import__("os").path.isfile(cookies_file):
        opts["cookiefile"] = cookies_file
    if proxy:
        opts["proxy"] = proxy
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


async def merged_drm_keys(db) -> Dict[str, str]:
    from config.settings import DRM_KEYS
    keys = dict(DRM_KEYS)
    try:
        db_keys = await db.get_drm_keys()
        keys.update(db_keys)
    except Exception:
        pass
    return keys
