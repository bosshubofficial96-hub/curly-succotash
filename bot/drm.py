"""
Advanced DRM / CDN bypass engine — v4.2  (AppX V2 full rework)

What changed vs v4.1:
  • Added static-trans-v2.appx.co.in (V2 video CDN) to CDN host pools.
  • appx_dedup_path(): fixes doubled filename in V2 video paths, e.g.
      /360p/encrypted.mkv/encrypted.mkv  →  /360p/encrypted.mkv
  • _appx_v2_content_bypass(): NEW.  Parses courseCode + contentId directly
    from V2 video URL paths and calls AppX V2 API endpoints to retrieve a
    publicly-accessible CloudFront-signed URL (no Bearer token needed to open).
  • All three API resolvers (S6 fresh-url, S10 V2 stream, S12 V2 content) now
    run concurrently at startup.
  • Structural fallback no longer returns the original URL when all probes
    fail — it returns the best decoded/API-resolved URL instead.
  • Probes run in parallel batches; probe timeout is 8 s.

Strategy summary
────────────────
Bypass V1 (S1-S7)
  S1  original URL + full Cookie + Bearer
  S2  Bearer-only (Cookie removed)
  S3  CloudFront URLPrefix decoded → directory prefix URL
  S4  decoded prefix + filename-only (URLPrefix path-doubling bug fixed)
  S5  CloudFront signed params stripped (Signature/KeyName/Expires/URLPrefix)
  S5b decoded + params stripped
  S6  AppX REST API → fresh signed URL
  S7  V1 CDN subdomain rotation (4 hosts)

Bypass V2 (S8-S12)
  S8  token injected as ?token= / ?auth= query param (5 variants)
  S9  X-Auth-Token + X-API-Key headers + 5 V2 CDN hosts
  S10 AppX V2 stream/resolve API
  S11 Deduplicated path (removes /file.mkv/file.mkv suffix bug)
  S12 AppX V2 content API (extracts contentId from URL, calls V2 endpoints)

Fallback order when all probes fail (best → worst):
  S12 V2 content API > S10 V2 stream > S6 fresh URL >
  S11 dedup path > S4 direct file URL > S5 stripped > original
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
    "static-trans-v2.appx.co.in",          # V2 video transcoding CDN ← NEW
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

# AppX V2 video URL pattern:
# /videos/{courseCode}/{contentId}/{encryptedHash}/{quality}/...
_V2_VIDEO_RE = re.compile(
    r"/videos/([^/]+)/([^/]+)/[^/]+/([^/]+)/",
    re.IGNORECASE,
)


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
    """Decode the URLPrefix CloudFront query param → directory base URL."""
    qs  = parse_qs(urlparse(url).query)
    raw = (qs.get("URLPrefix") or qs.get("urlprefix") or [None])[0]
    return _b64d(raw) if raw else None


def appx_strip_params(url: str) -> str:
    """Remove CloudFront signing params, keep any remaining query params."""
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
    Decode URLPrefix and reconstruct the direct CDN file URL.

    URLPrefix decodes to the directory prefix, e.g.:
      https://static-db-v2.appx.co.in/courses/123/lecture/456/

    We extract ONLY the filename from the original path and append it:
      https://static-db-v2.appx.co.in/courses/123/lecture/456/file.pdf

    (Bug in v4.0: full path was appended, doubling directory segments.)
    """
    prefix = appx_decode_prefix(url)
    if not prefix or not prefix.startswith("http"):
        return None
    path     = urlparse(url).path       # /courses/123/lecture/456/file.pdf
    filename = path.rstrip("/").rsplit("/", 1)[-1]   # "file.pdf"
    if not filename:
        return None
    return prefix.rstrip("/") + "/" + filename


def appx_dedup_path(url: str) -> Optional[str]:
    """
    Fix doubled-filename suffix produced by AppX V2 CDN URLs.

    AppX V2 video paths often end in /encrypted.mkv/encrypted.mkv (or any
    duplicated segment).  Remove the final duplicate so the URL points to the
    actual file:
      /360p/encrypted.mkv/encrypted.mkv  →  /360p/encrypted.mkv

    Returns None if no duplication is detected (original URL is already clean).
    """
    p     = urlparse(url)
    parts = p.path.rstrip("/").split("/")
    if len(parts) >= 2 and parts[-1] and parts[-1] == parts[-2]:
        clean = "/".join(parts[:-1])
        fixed = urlunparse(p._replace(path=clean))
        return fixed if fixed != url else None
    return None


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


# ── AppX V2 content ID parser ─────────────────────────────────────────────────
def appx_parse_v2_video(url: str) -> Optional[Tuple[str, str, str]]:
    """
    Parse courseCode, contentId, and quality from an AppX V2 video URL.

    URL pattern:
      https://static-trans-v2.appx.co.in/videos/{courseCode}/{contentId}/
        {encryptedHash}/{quality}/encrypted.mkv/encrypted.mkv

    Returns (courseCode, contentId, quality) or None.
    """
    path = urlparse(url).path
    m    = _V2_VIDEO_RE.search(path)
    if not m:
        return None
    course_code = m.group(1)  # e.g. "akstechnicalclasses-data"
    content_id  = m.group(2)  # e.g. "3661794-1777913165"
    quality     = m.group(3)  # e.g. "360p"
    return course_code, content_id, quality


# ── AppX API resolvers ────────────────────────────────────────────────────────
async def appx_fresh_url(
    session:       aiohttp.ClientSession,
    resource_path: str,
    token:         str,
) -> Optional[str]:
    """S6 — AppX V1 REST API: get a fresh CloudFront-signed URL for a path."""
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
                        logger.info("AppX S6 API → fresh URL via %s", ep)
                        return fresh
        except Exception as e:
            logger.debug("AppX S6 %s: %s", ep, e)
    return None


async def _appx_v2_stream_url(
    session: aiohttp.ClientSession,
    url:     str,
    token:   str,
) -> Optional[str]:
    """S10 — POST to AppX V2 stream/resolve API for a direct pre-signed URL."""
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
                        logger.info("AppX S10 V2 stream API ✅ %s", ep)
                        return resolved
        except Exception as e:
            logger.debug("AppX S10 stream %s: %s", ep, e)
    return None


async def _appx_v2_content_bypass(
    session: aiohttp.ClientSession,
    url:     str,
    token:   str,
) -> Optional[str]:
    """
    S12 — AppX V2 content bypass (NEW).

    Extracts courseCode + contentId from the V2 video CDN URL path, then
    calls multiple AppX V2 API endpoints that return a publicly-accessible
    CloudFront-signed URL (openable in Chrome without auth headers).

    Targets URLs like:
      https://static-trans-v2.appx.co.in/videos/{courseCode}/{contentId}/...
    """
    parsed = appx_parse_v2_video(url)
    if not parsed:
        logger.debug("S12: could not parse V2 video URL: %s", url[:80])
        return None

    course_code, content_id, quality = parsed
    logger.info(
        "S12: V2 content bypass — course=%s contentId=%s quality=%s",
        course_code, content_id, quality,
    )

    from config.settings import APPX_API_BASE
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
        "Referer":       "https://appx.co.in/",
        "Origin":        "https://appx.co.in",
        "X-Course-Code": course_code,
    }

    endpoints: List[Tuple[str, str, Optional[Dict]]] = [
        # (endpoint, method, payload)
        (f"{APPX_API_BASE}/api/v2/content/getUrl", "POST",
         {"contentId": content_id, "courseCode": course_code,
          "quality": quality, "type": "video"}),

        (f"{APPX_API_BASE}/api/v2/media/getUrl", "POST",
         {"contentId": content_id, "type": "video", "quality": quality}),

        (f"{APPX_API_BASE}/api/v2/content/signed-url", "POST",
         {"url": url, "contentId": content_id, "type": "video"}),

        (f"{APPX_API_BASE}/api/v2/media/signed-url", "POST",
         {"contentId": content_id, "courseCode": course_code}),

        # GET variants
        (f"{APPX_API_BASE}/api/v2/lectures/{content_id}/url", "GET", None),
        (f"{APPX_API_BASE}/api/v2/content/{content_id}/media", "GET", None),
        (f"{APPX_API_BASE}/api/v1/lectures/{content_id}/url", "GET", None),

        # Fallback: post entire original URL for server-side resolution
        (f"{APPX_API_BASE}/api/v2/content/getSignedUrl", "POST",
         {"url": url, "courseCode": course_code}),
    ]

    for ep, method, payload in endpoints:
        try:
            ctx = (
                session.post(ep, json=payload, headers=headers,
                             timeout=aiohttp.ClientTimeout(total=15))
                if method == "POST"
                else session.get(ep, headers=headers,
                                 timeout=aiohttp.ClientTimeout(total=15))
            )
            async with ctx as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    result = (
                        data.get("url")
                        or data.get("signedUrl")
                        or data.get("downloadUrl")
                        or data.get("streamUrl")
                        or data.get("hlsUrl")
                        or data.get("data", {}).get("url")
                        or data.get("data", {}).get("signedUrl")
                        or data.get("data", {}).get("downloadUrl")
                    )
                    if result and result.startswith("http"):
                        logger.info("S12 V2 content bypass ✅ via %s → %s",
                                    ep, result[:80])
                        return result
        except Exception as e:
            logger.debug("S12 %s %s: %s", method, ep, e)

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


# ── Reliable reachability probe ───────────────────────────────────────────────
async def _probe(
    session: aiohttp.ClientSession,
    url:     str,
    headers: Dict,
    proxy:   str = None,
) -> bool:
    """
    Returns True if the server responds 200/206 to this URL.
    1. Range-GET bytes=0-0 (fast; avoids downloading the whole file)
    2. If Range-GET gives 4xx, fall back to plain GET reading 1 byte
       (some AppX V2 CDN origins reject Range headers outright).
    """
    timeout = aiohttp.ClientTimeout(total=8)

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
    except Exception as e:
        logger.debug("probe-range(%s): %s", url[:55], e)
        return False

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
    """Probe candidates in parallel batches; return first passing (url, h, label)."""
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
        12-strategy AppX bypass.

        1. Three API resolvers run concurrently at startup (S6, S10, S12).
        2. All structural and API-resolved candidates are probed in parallel
           batches of 5 (8-second timeout each).
        3. If all probes fail, the best structurally-decoded or API-resolved
           URL is returned instead of the original — so the bypass link always
           points to the most promising URL, not the raw signed original.
        """

        # ── Phase 1: Run API resolvers concurrently ───────────────────────
        fresh:       Optional[str] = None
        v2_resolved: Optional[str] = None
        v2_content:  Optional[str] = None

        if self._token:
            s6_coro  = appx_fresh_url(
                self.session, urlparse(url).path, self._token)
            s10_coro = _appx_v2_stream_url(self.session, url, self._token)
            s12_coro = _appx_v2_content_bypass(self.session, url, self._token)

            s6_r, s10_r, s12_r = await asyncio.gather(
                s6_coro, s10_coro, s12_coro, return_exceptions=True
            )
            fresh       = s6_r  if isinstance(s6_r,  str) else None
            v2_resolved = s10_r if isinstance(s10_r, str) else None
            v2_content  = s12_r if isinstance(s12_r, str) else None

        # ── Phase 2: Structural URL transformations ────────────────────────
        decoded  = appx_decode_prefix(url)
        rebuilt  = appx_resource_url(url)          # prefix + filename only
        stripped = appx_strip_params(url)
        deduped  = appx_dedup_path(url)            # fix /file.mkv/file.mkv

        # Fallback priority list (returned when all probes fail):
        # best-first so structural_fallbacks[0] is always our best bet
        structural_fallbacks: List[Tuple[str, Dict, str]] = []
        if v2_content:
            structural_fallbacks.append((v2_content, base_h, "S12:v2-content-api"))
        if v2_resolved:
            structural_fallbacks.append((v2_resolved, base_h, "S10:v2-stream-api"))
        if fresh:
            structural_fallbacks.append((fresh, base_h, "S6:api-fresh"))
        if deduped:
            structural_fallbacks.append((deduped, base_h, "S11:dedup-path"))
        if rebuilt and rebuilt != url:
            structural_fallbacks.append((rebuilt, base_h, "S4:direct-file-url"))
        if stripped != url:
            structural_fallbacks.append((stripped, base_h, "S5:stripped-params"))

        # ── Phase 3: Build full probe candidate list ───────────────────────
        candidates: List[Tuple[str, Dict, str]] = []

        # S1 — original URL + full auth headers (baseline)
        candidates.append((url, base_h, "S1:direct+cookie"))

        # S2 — bearer-only (no Cookie)
        if self._token:
            h2 = {k: v for k, v in base_h.items() if k != "Cookie"}
            candidates.append((url, h2, "S2:bearer-only"))

        # S3 — decoded URLPrefix directory URL
        if decoded and decoded.startswith("http"):
            candidates.append((decoded, base_h, "S3:decoded-prefix"))

        # S4 — decoded prefix + FILENAME ONLY (fixed path-doubling)
        if rebuilt and rebuilt != url:
            candidates.append((rebuilt, base_h, "S4:direct-file-url"))

        # S5 — strip all CloudFront signed params
        if stripped != url:
            candidates.append((stripped, base_h, "S5:stripped-params"))
            if decoded:
                stripped_decoded = appx_strip_params(decoded)
                candidates.append((stripped_decoded, base_h, "S5b:decoded+stripped"))

        # S6 — fresh URL from AppX REST API
        if fresh:
            candidates.append((fresh, base_h, "S6:api-fresh-url"))

        # S7 — V1 CDN subdomain rotation
        for cdn_url in appx_cdn_variants(url):
            candidates.append((cdn_url, base_h, "S7:cdn-rotation"))

        # S8 — token injected as URL query param
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

        # S10 — V2 stream/resolve API result
        if v2_resolved:
            candidates.append((v2_resolved, base_h, "S10:v2-stream-api"))

        # S11 — deduplicated path (fix /encrypted.mkv/encrypted.mkv)
        if deduped:
            candidates.append((deduped, base_h, "S11:dedup-path"))
            # Also try with auth stripped
            candidates.append((appx_strip_params(deduped), base_h,
                               "S11b:dedup+stripped"))

        # S12 — V2 content API (extracts contentId, returns signed URL)
        if v2_content:
            candidates.append((v2_content, base_h, "S12:v2-content-api"))

        # ── Phase 4: Probe in parallel batches ────────────────────────────
        winner = await _probe_batch(
            self.session, candidates, proxy=self.proxy, batch_size=5
        )
        if winner:
            w_url, w_h, w_label = winner
            logger.info("AppX bypass ✅ [%s] → %s", w_label, w_url[:90])
            return w_url, w_h

        # ── Phase 5: Probe-free fallback — best structural candidate ───────
        # When every probe fails (e.g. CDN IP-blocks the server), still return
        # the best decoded/API-resolved URL so the channel link is useful.
        if structural_fallbacks:
            best_url, best_h, best_label = structural_fallbacks[0]
            logger.warning(
                "AppX: all probes failed — using best structural bypass "
                "[%s]: %s", best_label, best_url[:90],
            )
            return best_url, best_h

        logger.warning(
            "AppX: no bypass found for %s — returning original URL with full auth",
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
