"""
Advanced DRM / CDN bypass engine — v4.1  (AppX Bypass V2 + 10-strategy)

Root-cause fixes vs v4.0:
  • appx_resource_url() was appending the FULL path to the decoded URLPrefix,
    doubling the directory segments (e.g. /courses/123/…/courses/123/file.pdf).
    Fixed: only the filename is appended to the decoded directory prefix.
  • _resolve_appx() used to return the original URL when ALL probes failed.
    Now it returns the BEST STRUCTURALLY-DECODED URL instead — the downloader
    handles retries; the channel bypass link is always the best URL we can build.
  • API calls (S6 fresh-url, S10 V2 stream) now run CONCURRENTLY at startup
    so the total resolve time is not (S6_time + S10_time) but max(S6, S10).
  • Probes run in PARALLEL batches (5 concurrent) instead of sequentially,
    reducing worst-case resolve time from ~120 s → ~24 s.
  • Probe timeout reduced to 8 s (was 12 s).

Bypass V1 (strategies 1-7):
  1. Original URL + full cookie + Bearer token
  2. Bearer-only (no Cookie header)
  3. Stripped CloudFront params (Signature/KeyName/Expires/URLPrefix removed)
  4. URLPrefix base64-decoded + filename → real CDN file URL  ← FIXED
  5. Decoded prefix cleaned of extra params
  6. AppX REST API fresh signed URL
  7. CDN subdomain rotation (4 V1 hosts)

Bypass V2 (strategies 8-10):
  8. Token injected as ?token= / ?auth= query param
  9. X-Auth-Token + X-API-Key headers + 5 V2 CDN hosts
 10. AppX V2 stream/resolve API endpoints

Fallback priority when all probes fail (best → worst):
  S10 V2 API > S6 fresh URL > S4 direct file URL > S3 stripped+decoded > original
"""

import asyncio
import base64
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import aiohttp

logger = logging.getLogger(__name__)

# ── Known AppX CDN host pools ─────────────────────────────────────────────────
_APPX_CDN_HOSTS = [
    "static-db-v2.appx.co.in",
    "static-db.appx.co.in",
    "cdn.appx.co.in",
    "media.appx.co.in",
]

_APPX_V2_CDN_HOSTS = [
    "appxcdn.appx.co.in",
    "d1bsb8xfl4oazp.cloudfront.net",
    "appx-pdf-keyset.s3.ap-south-1.amazonaws.com",
    "appxcontent.s3.ap-south-1.amazonaws.com",
    "appxlectures.s3.ap-south-1.amazonaws.com",
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
    if "appx.co.in" in lo or "appx-pdf-keyset" in lo: return "appx"
    if ".m3u8" in lo:                                   return "hls"
    if ".mpd"  in lo:                                   return "dash"
    if "x-amz-signature" in lo or "awsaccesskeyid" in lo: return "s3"
    if "storage.googleapis" in lo:                      return "gcs"
    if "jwplatform" in lo or "jwpsrv" in lo:            return "jwp"
    if "vimeo.com"  in lo:                              return "vimeo"
    if "youtube.com" in lo or "youtu.be" in lo:         return "youtube"
    if "drive.google.com" in lo:                        return "gdrive"
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
    """
    Decode URLPrefix → append ONLY the filename to the directory prefix.

    AppX CloudFront sign URLs where URLPrefix encodes the directory base,
    e.g. URLPrefix decodes to:
        https://static-db-v2.appx.co.in/courses/123/lecture/456/

    The original URL path is:
        /courses/123/lecture/456/file.pdf

    We extract just "file.pdf" and append it to the decoded prefix, giving:
        https://static-db-v2.appx.co.in/courses/123/lecture/456/file.pdf

    (Previous bug: full path was appended, doubling the directory segments.)
    """
    prefix = appx_decode_prefix(url)
    if not prefix or not prefix.startswith("http"):
        return None
    path = urlparse(url).path  # e.g. /courses/123/lecture/456/file.pdf
    # Extract ONLY the filename from the path
    filename = path.rstrip("/").rsplit("/", 1)[-1]  # "file.pdf"
    if not filename:
        return None
    return prefix.rstrip("/") + "/" + filename

def appx_cdn_variants(url: str) -> List[str]:
    path = urlparse(url).path
    return [f"https://{h}{path}" for h in _APPX_CDN_HOSTS]

def appx_v2_cdn_variants(url: str) -> List[str]:
    path = urlparse(url).path
    return [f"https://{h}{path}" for h in _APPX_V2_CDN_HOSTS]

def _inject_token_param(url: str, token: str) -> List[str]:
    """S8: Try common token query-param names used by V2 CDN endpoints."""
    p  = urlparse(url)
    qs = parse_qs(p.query, keep_blank_values=True)
    results = []
    for param in ("token", "auth", "access_token", "jwt", "t"):
        q = {k: v[0] for k, v in qs.items()}
        q[param] = token
        results.append(urlunparse(p._replace(query=urlencode(q))))
    return results


# ── Reliable reachability probe ───────────────────────────────────────────────
async def _probe(
    session: aiohttp.ClientSession,
    url:     str,
    headers: Dict,
    proxy:   str = None,
) -> bool:
    """
    Returns True if the server accepts this URL.
    1. Range-GET bytes=0-0 (fast; works for most CDNs)
    2. If Range-GET gives 4xx, fall back to plain GET reading 1 byte
       (some AppX V2 CDN origins reject Range headers).
    """
    timeout = aiohttp.ClientTimeout(total=8)  # 8 s — fast fail

    # Attempt 1: Range-GET
    h_range = {**headers, "Range": "bytes=0-0"}
    try:
        async with session.get(
            url, headers=h_range, allow_redirects=True,
            proxy=proxy, timeout=timeout,
        ) as r:
            if r.status in (200, 206):
                return True
            if r.status not in (400, 403, 405, 416):
                return False
            # 4xx Range error → try plain GET
    except Exception as e:
        logger.debug("probe-range(%s): %s", url[:55], e)
        return False

    # Attempt 2: plain GET (read first byte only)
    try:
        async with session.get(
            url, headers=headers, allow_redirects=True,
            proxy=proxy, timeout=timeout,
        ) as r:
            if r.status == 200:
                await r.content.read(1)
                return True
    except Exception as e:
        logger.debug("probe-plain(%s): %s", url[:55], e)
    return False


async def _probe_batch(
    session:    aiohttp.ClientSession,
    candidates: List[Tuple[str, Dict, str]],
    proxy:      str = None,
    batch_size: int = 5,
) -> Optional[Tuple[str, Dict, str]]:
    """
    Probe candidates in parallel batches.
    Returns the first (url, headers, label) that passes, or None.
    """
    for i in range(0, len(candidates), batch_size):
        batch = candidates[i:i + batch_size]
        tasks = [
            asyncio.create_task(_probe(session, u, h, proxy))
            for u, h, _ in batch
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for (u, h, label), ok in zip(batch, results):
            if ok is True:
                return (u, h, label)
    return None


# ── AppX REST API fresh-URL fetcher ───────────────────────────────────────────
async def appx_fresh_url(
    session:       aiohttp.ClientSession,
    resource_path: str,
    token:         str,
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
                    data  = await r.json(content_type=None)
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


async def _appx_v2_stream_url(
    session: aiohttp.ClientSession,
    url:     str,
    token:   str,
) -> Optional[str]:
    """S10 — POST to AppX V2 stream/resolve API to get a direct pre-signed URL."""
    from config.settings import APPX_API_BASE
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
        "Referer":       "https://appx.co.in/",
        "Origin":        "https://appx.co.in",
    }
    for ep, payload in [
        (f"{APPX_API_BASE}/api/v2/content/stream",  {"url": url}),
        (f"{APPX_API_BASE}/api/v2/media/resolve",   {"resourceUrl": url}),
        (f"{APPX_API_BASE}/api/v2/content/resolve", {"url": url, "type": "auto"}),
    ]:
        try:
            async with session.post(
                ep, json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=18),
            ) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    resolved = (
                        data.get("url")
                        or data.get("downloadUrl")
                        or data.get("streamUrl")
                        or data.get("data", {}).get("url")
                        or data.get("data", {}).get("downloadUrl")
                    )
                    if resolved and resolved.startswith("http"):
                        logger.info("AppX V2 stream API ✅ %s", ep)
                        return resolved
        except Exception as e:
            logger.debug("AppX V2 stream %s: %s", ep, e)
    return None


# ── AppX login ────────────────────────────────────────────────────────────────
async def appx_login(
    session:  aiohttp.ClientSession,
    email:    str,
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
        10-strategy AppX bypass with parallel API calls and parallel probing.

        Selection order:
          1. Probe-based (fastest reachable wins)
          2. Fallback (best structural candidate when all probes fail):
             S10 V2 API > S6 fresh API URL > S4 direct file URL >
             S3 stripped-cleaned > S5 stripped > original URL
        """

        # ── Phase 1: Run API-based resolvers concurrently ─────────────────
        # S6 and S10 make real HTTP calls to AppX APIs; run them in parallel
        # so we don't pay (S6_time + S10_time) serially.
        fresh: Optional[str]      = None
        v2_resolved: Optional[str] = None
        if self._token:
            s6_coro  = appx_fresh_url(self.session, urlparse(url).path, self._token)
            s10_coro = _appx_v2_stream_url(self.session, url, self._token)
            s6_res, s10_res = await asyncio.gather(
                s6_coro, s10_coro, return_exceptions=True
            )
            fresh       = s6_res  if isinstance(s6_res,  str) else None
            v2_resolved = s10_res if isinstance(s10_res, str) else None

        # ── Phase 2: Build structural bypass candidates ────────────────────
        # "Structural" = URL constructed via decoding/manipulation (no extra HTTP).
        # These are used both for probing AND as the fallback when probes fail.

        decoded  = appx_decode_prefix(url)           # decoded URLPrefix directory
        rebuilt  = appx_resource_url(url)             # decoded prefix + filename (FIXED)
        stripped = appx_strip_params(url)             # remove CloudFront signed params

        # Ordered best-first fallback list (used when all probes fail)
        structural_fallbacks: List[Tuple[str, Dict, str]] = []
        if v2_resolved:
            structural_fallbacks.append((v2_resolved, base_h, "S10:v2-api"))
        if fresh:
            structural_fallbacks.append((fresh, base_h, "S6:api-fresh"))
        if rebuilt and rebuilt != url:
            structural_fallbacks.append((rebuilt, base_h, "S4:direct-file-url"))
        if decoded and decoded.startswith("http") and decoded != url:
            stripped_decoded = appx_strip_params(decoded)
            structural_fallbacks.append((stripped_decoded, base_h, "S3b:decoded+stripped"))
        if stripped != url:
            structural_fallbacks.append((stripped, base_h, "S5:stripped-params"))

        # ── Phase 3: Build probe candidate list ───────────────────────────
        candidates: List[Tuple[str, Dict, str]] = []

        # S1 — original URL + cookie + bearer
        candidates.append((url, base_h, "S1:direct+cookie"))

        # S2 — bearer only (no Cookie header)
        if self._token:
            h2 = {k: v for k, v in base_h.items() if k != "Cookie"}
            candidates.append((url, h2, "S2:bearer-only"))

        # S3 — decoded URLPrefix (directory URL — probing feasibility)
        if decoded and decoded.startswith("http"):
            candidates.append((decoded, base_h, "S3:decoded-prefix"))

        # S4 — FIXED: decoded prefix + FILENAME ONLY (not full path)
        if rebuilt and rebuilt != url:
            candidates.append((rebuilt, base_h, "S4:direct-file-url"))

        # S5 — strip CloudFront signed params
        if stripped != url:
            candidates.append((stripped, base_h, "S5:stripped-params"))
            if decoded:
                candidates.append(
                    (appx_strip_params(decoded), base_h, "S5b:decoded+stripped")
                )

        # S6 — fresh URL from AppX REST API
        if fresh:
            candidates.append((fresh, base_h, "S6:api-fresh-url"))

        # S7 — V1 CDN subdomain rotation
        for cdn_url in appx_cdn_variants(url):
            candidates.append((cdn_url, base_h, "S7:cdn-rotation"))

        # S8 — token as URL query param
        if self._token:
            h8 = {k: v for k, v in base_h.items() if k != "Cookie"}
            for tok_url in _inject_token_param(url, self._token):
                candidates.append((tok_url, h8, "S8:token-param"))

        # S9 — X-Auth-Token / X-API-Key headers + V2 CDN hosts
        if self._token:
            h9 = dict(base_h)
            h9["X-Auth-Token"] = self._token
            h9["X-API-Key"]    = self._token
            candidates.append((url, h9, "S9:v2-auth-headers"))
            for v2_url in appx_v2_cdn_variants(url):
                candidates.append((v2_url, h9, "S9:v2-cdn+headers"))

        # S10 — V2 API result
        if v2_resolved:
            candidates.append((v2_resolved, base_h, "S10:v2-stream-api"))

        # ── Phase 4: Probe in parallel batches ────────────────────────────
        winner = await _probe_batch(
            self.session, candidates, proxy=self.proxy, batch_size=5
        )
        if winner:
            w_url, w_h, w_label = winner
            logger.info("AppX bypass ✅ %s → %s", w_label, w_url[:90])
            return w_url, w_h

        # ── Phase 5: All probes failed — return best structural candidate ──
        # The downloader will attempt the URL directly; the channel bypass link
        # should always be the best decoded URL, never the original signed URL.
        if structural_fallbacks:
            best_url, best_h, best_label = structural_fallbacks[0]
            logger.warning(
                "AppX: probes failed, using best structural bypass [%s]: %s",
                best_label, best_url[:90],
            )
            return best_url, best_h

        # True last resort: original URL with full auth headers
        logger.warning(
            "AppX: no bypass found for %s — returning original URL with auth headers",
            url[:80],
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
