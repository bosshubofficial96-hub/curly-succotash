"""
DRM / CDN bypass engine — v4.3

ROOT CAUSE FIX:
  classify() now returns "appx_v2" for static-trans-v2.appx.co.in URLs.
  The downloader routes "appx_v2" through yt-dlp (with Bearer-token headers)
  instead of plain HTTP GET — the ONLY reliable way to download AppX V2 content.

Why "Open in browser" never works for V2:
  • CDN requires Authorization: Bearer <TOKEN> on every request.
  • Chrome cannot send custom auth headers when following a Telegram link.
  • There is NO publicly-accessible URL for AppX V2 encrypted video.
  • Only the bot (with the user's Bearer token) can download V2 content.

Standard AppX bypass (PDFs / media on static-db-v2):
  S1  original URL + Cookie + Bearer
  S2  Bearer-only
  S3  URLPrefix decoded → directory prefix
  S4  decoded prefix + filename-only (path-doubling fixed)
  S5  CloudFront params stripped
  S6  AppX REST API fresh signed URL  ─┐ concurrent
  S10 V2 stream/resolve API           ─┤
  S12 V2 content-ID API               ─┘
  S7  V1 CDN subdomain rotation
  S8  token as query param
  S9  X-Auth-Token headers + V2 CDN hosts
  S11 deduplicated path
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

_APPX_V2_CDN_HOSTS = [
    "static-trans-v2.appx.co.in",
    "appxcdn.appx.co.in",
    "d1bsb8xfl4oazp.cloudfront.net",
    "appx-pdf-keyset.s3.ap-south-1.amazonaws.com",
    "appxcontent.s3.ap-south-1.amazonaws.com",
    "appxlectures.s3.ap-south-1.amazonaws.com",
]

_BLOCKED_HOSTS = {
    "localhost", "127.0.0.1", "0.0.0.0", "::1",
    "metadata.google.internal", "169.254.169.254",
}
_PRIVATE_PFXS = (
    "192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19.",
    "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.",
    "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.",
)
_URL_RE = re.compile(r"^https?://[^\s<>\"{}|\\^`\[\]]+$")


def is_valid_url(url: str) -> bool:
    url = url.strip()
    if not url or not _URL_RE.match(url):
        return False
    host = urlparse(url).hostname or ""
    if host in _BLOCKED_HOSTS:
        return False
    return not any(host.startswith(p) for p in _PRIVATE_PFXS)


def classify(url: str) -> str:
    """
    Classify URL into a download-strategy category.

    "appx_v2"  — AppX V2 encrypted video (static-trans-v2.appx.co.in)
                 → MUST use yt-dlp + Bearer token. No browser-link possible.
    "appx"     — AppX PDF/media (static-db-v2, etc.)
                 → 12-strategy bypass then plain HTTP download.
    """
    lo = url.lower()
    if "static-trans-v2.appx.co.in" in lo:   # V2 video CDN — check FIRST
        return "appx_v2"
    if "appx.co.in" in lo or "appx-pdf-keyset" in lo:
        return "appx"
    if ".m3u8" in lo:       return "hls"
    if ".mpd"  in lo:       return "dash"
    if "x-amz-signature" in lo or "awsaccesskeyid" in lo: return "s3"
    if "storage.googleapis" in lo:            return "gcs"
    if "jwplatform" in lo or "jwpsrv" in lo:  return "jwp"
    if "vimeo.com" in lo:                     return "vimeo"
    if "youtube.com" in lo or "youtu.be" in lo: return "youtube"
    if "drive.google.com" in lo:              return "gdrive"
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
    for k in ("Signature","KeyName","Expires","URLPrefix",
              "signature","keyname","expires","urlprefix","Policy","Key-Pair-Id"):
        qs.pop(k, None)
    q = urlencode({k: v[0] for k, v in qs.items()}) if qs else ""
    return urlunparse(p._replace(query=q))

def appx_resource_url(url: str) -> Optional[str]:
    """Decode URLPrefix → append ONLY filename (fixes path-doubling bug)."""
    prefix = appx_decode_prefix(url)
    if not prefix or not prefix.startswith("http"):
        return None
    path     = urlparse(url).path
    filename = path.rstrip("/").rsplit("/", 1)[-1]
    if not filename:
        return None
    return prefix.rstrip("/") + "/" + filename

def appx_dedup_path(url: str) -> Optional[str]:
    """Remove /file.mkv/file.mkv doubled-suffix."""
    p     = urlparse(url)
    parts = p.path.rstrip("/").split("/")
    if len(parts) >= 2 and parts[-1] and parts[-1] == parts[-2]:
        fixed = urlunparse(p._replace(path="/".join(parts[:-1])))
        return fixed if fixed != url else None
    return None

def appx_cdn_variants(url: str) -> List[str]:
    path = urlparse(url).path
    return [f"https://{h}{path}" for h in _APPX_CDN_HOSTS]

def appx_v2_cdn_variants(url: str) -> List[str]:
    path = urlparse(url).path
    return [f"https://{h}{path}" for h in _APPX_V2_CDN_HOSTS]

def _inject_token_param(url: str, token: str) -> List[str]:
    p  = urlparse(url)
    qs = parse_qs(p.query, keep_blank_values=True)
    results = []
    for param in ("token", "auth", "access_token", "jwt", "t"):
        q = {k: v[0] for k, v in qs.items()}
        q[param] = token
        results.append(urlunparse(p._replace(query=urlencode(q))))
    return results


# ── AppX API resolvers ────────────────────────────────────────────────────────
async def appx_fresh_url(session, resource_path: str, token: str) -> Optional[str]:
    from config.settings import APPX_API_BASE, APPX_CDN_BASE
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
               "Referer": "https://appx.co.in/", "Origin": "https://appx.co.in"}
    path = resource_path.lstrip("/")
    for ep, payload in [
        (f"{APPX_API_BASE}/api/v1/media/getUrl",  {"path": path}),
        (f"{APPX_API_BASE}/api/v2/media/getUrl",  {"url": f"{APPX_CDN_BASE}/{path}"}),
        (f"{APPX_API_BASE}/api/v1/content/url",   {"resource": path, "type": "pdf"}),
    ]:
        try:
            async with session.post(ep, json=payload, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status == 200:
                    data  = await r.json(content_type=None)
                    fresh = (data.get("url") or data.get("data",{}).get("url")
                             or data.get("signedUrl") or data.get("data",{}).get("signedUrl"))
                    if fresh and fresh.startswith("http"):
                        logger.info("S6 fresh URL via %s", ep)
                        return fresh
        except Exception as e:
            logger.debug("S6 %s: %s", ep, e)
    return None

async def _appx_v2_stream_url(session, url: str, token: str) -> Optional[str]:
    from config.settings import APPX_API_BASE
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
               "Referer": "https://appx.co.in/", "Origin": "https://appx.co.in"}
    for ep, payload in [
        (f"{APPX_API_BASE}/api/v2/content/stream",  {"url": url}),
        (f"{APPX_API_BASE}/api/v2/media/resolve",   {"resourceUrl": url}),
        (f"{APPX_API_BASE}/api/v2/content/resolve", {"url": url, "type": "auto"}),
    ]:
        try:
            async with session.post(ep, json=payload, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=18)) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    res  = (data.get("url") or data.get("downloadUrl") or data.get("streamUrl")
                            or data.get("data",{}).get("url") or data.get("data",{}).get("downloadUrl"))
                    if res and res.startswith("http"):
                        logger.info("S10 V2 stream ✅ %s", ep)
                        return res
        except Exception as e:
            logger.debug("S10 %s: %s", ep, e)
    return None

async def _appx_v2_content_bypass(session, url: str, token: str) -> Optional[str]:
    try:
        from bot.v2_bypass import try_appx_v2_api
        return await try_appx_v2_api(session, url, token)
    except Exception as e:
        logger.debug("S12: %s", e)
        return None


# ── AppX login ────────────────────────────────────────────────────────────────
async def appx_login(session, email: str, password: str) -> Optional[str]:
    from config.settings import APPX_HEADERS, APPX_LOGIN_URL
    if not email or not password:
        return None
    try:
        async with session.post(
            APPX_LOGIN_URL, json={"email": email, "password": password},
            headers={**APPX_HEADERS, "Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=20),
        ) as r:
            if r.status != 200:
                logger.warning("AppX login HTTP %s", r.status)
                return None
            data  = await r.json(content_type=None)
            token = (data.get("token") or data.get("data",{}).get("token")
                     or data.get("access_token") or data.get("data",{}).get("access_token"))
            if token:
                logger.info("AppX login OK")
                return f"token={token}"
            logger.warning("AppX login: no token in %s", list(data))
    except Exception as e:
        logger.warning("AppX login error: %s", e)
    return None


# ── Probe helpers ─────────────────────────────────────────────────────────────
async def _probe(session, url: str, headers: Dict, proxy: str = None) -> bool:
    timeout = aiohttp.ClientTimeout(total=8)
    try:
        async with session.get(url, headers={**headers, "Range": "bytes=0-0"},
                               allow_redirects=True, proxy=proxy, timeout=timeout) as r:
            if r.status in (200, 206): return True
            if r.status not in (400, 403, 405, 416): return False
    except Exception as e:
        logger.debug("probe-range(%s): %s", url[:55], e)
        return False
    try:
        async with session.get(url, headers=headers, allow_redirects=True,
                               proxy=proxy, timeout=timeout) as r:
            if r.status == 200:
                await r.content.read(1)
                return True
    except Exception as e:
        logger.debug("probe-plain(%s): %s", url[:55], e)
    return False

async def _probe_batch(session, candidates: List[Tuple[str, Dict, str]],
                       proxy: str = None, batch_size: int = 5) -> Optional[Tuple]:
    for i in range(0, len(candidates), batch_size):
        batch = candidates[i: i + batch_size]
        tasks = [asyncio.create_task(_probe(session, u, h, proxy)) for u, h, _ in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for (u, h, label), ok in zip(batch, results):
            if ok is True:
                return (u, h, label)
    return None


# ── DRM Resolver ──────────────────────────────────────────────────────────────
class DRMResolver:
    def __init__(self, session, cookie: str = "", drm_keys: Dict = None, proxy: str = None):
        self.session  = session
        self.cookie   = cookie
        self.drm_keys = drm_keys or {}
        self.proxy    = proxy
        self._token   = extract_token(cookie) if cookie else None

    def _h(self, extra: Dict = None) -> Dict:
        from config.settings import APPX_HEADERS
        h = dict(APPX_HEADERS)
        if self.cookie:   h["Cookie"]        = self.cookie
        if self._token:   h["Authorization"] = f"Bearer {self._token}"
        if extra:         h.update(extra)
        return h

    async def resolve(self, url: str) -> Tuple[str, Dict, str]:
        kind = classify(url)
        h    = self._h()

        if kind == "appx_v2":
            resolved, rh = await self._resolve_appx_v2(url, h)
            return resolved, rh, kind

        if kind == "appx":
            resolved, rh = await self._resolve_appx(url, h)
            return resolved, rh, kind

        if kind in ("hls","dash","vimeo","youtube","jwp","gdrive"):
            return url, h, kind

        return url, h, kind

    async def _resolve_appx_v2(self, url: str, base_h: Dict) -> Tuple[str, Dict]:
        """
        V2 video: try HLS manifest → API URL → deduplicated direct URL.
        ALWAYS returns a URL that requires the Bearer token (no browser link).
        """
        if not self._token:
            logger.warning("AppX V2: no Bearer token — download will fail")
            deduped = appx_dedup_path(url) or url
            return deduped, base_h

        try:
            from bot.v2_bypass import resolve_v2_best_url
            best_url, url_type = await resolve_v2_best_url(
                self.session, url, self._token, self.proxy
            )
            logger.info("AppX V2 resolved [%s]: %s", url_type, best_url[:90])
            return best_url, base_h
        except Exception as e:
            logger.warning("V2 resolve error: %s", e)
            return appx_dedup_path(url) or url, base_h

    async def _resolve_appx(self, url: str, base_h: Dict) -> Tuple[str, Dict]:
        """12-strategy bypass for AppX PDF / media content."""
        # Concurrent API calls
        fresh = v2_resolved = v2_content = None
        if self._token:
            r6, r10, r12 = await asyncio.gather(
                appx_fresh_url(self.session, urlparse(url).path, self._token),
                _appx_v2_stream_url(self.session, url, self._token),
                _appx_v2_content_bypass(self.session, url, self._token),
                return_exceptions=True,
            )
            fresh       = r6  if isinstance(r6,  str) else None
            v2_resolved = r10 if isinstance(r10, str) else None
            v2_content  = r12 if isinstance(r12, str) else None

        decoded  = appx_decode_prefix(url)
        rebuilt  = appx_resource_url(url)
        stripped = appx_strip_params(url)
        deduped  = appx_dedup_path(url)

        structural_fallbacks: List[Tuple[str, Dict, str]] = []
        if v2_content:   structural_fallbacks.append((v2_content,  base_h, "S12"))
        if v2_resolved:  structural_fallbacks.append((v2_resolved, base_h, "S10"))
        if fresh:        structural_fallbacks.append((fresh,        base_h, "S6"))
        if deduped:      structural_fallbacks.append((deduped,      base_h, "S11"))
        if rebuilt and rebuilt != url: structural_fallbacks.append((rebuilt, base_h, "S4"))
        if stripped != url:            structural_fallbacks.append((stripped, base_h, "S5"))

        candidates: List[Tuple[str, Dict, str]] = [(url, base_h, "S1:direct")]
        if self._token:
            h2 = {k: v for k, v in base_h.items() if k != "Cookie"}
            candidates.append((url, h2, "S2:bearer"))
        if decoded and decoded.startswith("http"):
            candidates.append((decoded, base_h, "S3:decoded"))
        if rebuilt and rebuilt != url:
            candidates.append((rebuilt, base_h, "S4:rebuilt"))
        if stripped != url:
            candidates.append((stripped, base_h, "S5:stripped"))
            if decoded:
                candidates.append((appx_strip_params(decoded), base_h, "S5b"))
        if fresh:        candidates.append((fresh,       base_h, "S6:fresh"))
        for cdn_url in appx_cdn_variants(url):
            candidates.append((cdn_url, base_h, "S7:cdn"))
        if self._token:
            h8 = {k: v for k, v in base_h.items() if k != "Cookie"}
            for tok_url in _inject_token_param(url, self._token):
                candidates.append((tok_url, h8, "S8:tok-param"))
            h9 = dict(base_h)
            h9.update({"X-Auth-Token": self._token, "X-API-Key": self._token})
            candidates.append((url, h9, "S9:v2-headers"))
            for v2u in appx_v2_cdn_variants(url):
                candidates.append((v2u, h9, "S9:v2-cdn"))
        if v2_resolved:  candidates.append((v2_resolved, base_h, "S10:v2-stream"))
        if deduped:
            candidates.append((deduped, base_h, "S11:dedup"))
            candidates.append((appx_strip_params(deduped), base_h, "S11b"))
        if v2_content:   candidates.append((v2_content,  base_h, "S12:v2-api"))

        winner = await _probe_batch(self.session, candidates, proxy=self.proxy)
        if winner:
            w_url, w_h, w_label = winner
            logger.info("AppX bypass ✅ [%s] %s", w_label, w_url[:90])
            return w_url, w_h

        if structural_fallbacks:
            best, best_h, label = structural_fallbacks[0]
            logger.warning("AppX: probes failed → structural [%s] %s", label, best[:90])
            return best, best_h

        logger.warning("AppX: no bypass for %s", url[:80])
        return url, base_h


# ── yt-dlp downloader ─────────────────────────────────────────────────────────
async def download_stream(
    url: str, output_path: str,
    headers: Dict = None, cookies_file: str = None,
    drm_keys: Dict = None, proxy: str = None,
    progress_hook=None, allow_unplayable: bool = False,
) -> bool:
    try:
        import yt_dlp
    except ImportError:
        logger.error("yt-dlp not installed")
        return False

    from config.settings import YTDLP_CONCURRENCY
    import os

    opts: Dict[str, Any] = {
        "outtmpl": output_path, "merge_output_format": "mp4",
        "quiet": True, "no_warnings": False, "noprogress": True,
        "retries": 10, "fragment_retries": 15,
        "skip_unavailable_fragments": True, "ignoreerrors": False,
        "http_headers": headers or {}, "hls_use_mpegts": True,
        "concurrent_fragment_downloads": YTDLP_CONCURRENCY,
        "buffersize": 256 * 1024, "http_chunk_size": 10 * 1024 * 1024,
        "socket_timeout": 30,
        "postprocessors": [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}],
    }
    if allow_unplayable or drm_keys:
        opts["allow_unplayable_formats"] = True
        opts["fixup"] = "never"
    if cookies_file and os.path.isfile(cookies_file):
        opts["cookiefile"] = cookies_file
    if proxy:  opts["proxy"] = proxy
    if progress_hook: opts["progress_hooks"] = [progress_hook]

    try:
        loop = asyncio.get_event_loop()
        with yt_dlp.YoutubeDL(opts) as ydl:
            code = await loop.run_in_executor(None, lambda: ydl.download([url]))
        return code == 0
    except Exception as e:
        logger.warning("yt-dlp error: %s", e)
        return False


# ── PDF / decrypt wrappers ────────────────────────────────────────────────────
def try_decrypt_pdf(src: str, dst: str) -> bool:
    from bot.decrypt import try_decrypt_pdf as _d
    return _d(src, dst)

async def merged_drm_keys(db) -> Dict[str, str]:
    from config.settings import DRM_KEYS
    keys = dict(DRM_KEYS)
    try:
        keys.update(await db.get_drm_keys())
    except Exception:
        pass
    return keys
