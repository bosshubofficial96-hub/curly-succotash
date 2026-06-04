"""
AppX V2 Bypass Engine — dedicated module for static-trans-v2.appx.co.in content.

AppX V2 serves encrypted video at:
  https://static-trans-v2.appx.co.in/videos/{course}/{contentId}/{hash}/{quality}/
    encrypted.mkv/encrypted.mkv

Why "Open in browser" never works for V2:
  • The CDN requires Authorization: Bearer <TOKEN> on every request.
  • Chrome/browser cannot send custom auth headers when following a URL button.
  • There is NO publicly-accessible URL for V2 encrypted video — period.

What this module does instead:
  1. try_get_v2_hls_url()  — probe alternate HLS manifest paths at the same CDN location.
     HLS manifests (index.m3u8) + Bearer token → yt-dlp downloads & auto-decrypts AES-128.
  2. try_appx_v2_api()    — hit AppX API endpoints to get a better download URL or HLS URL.
  3. parse_v2_video_info() — extract course/content metadata from the URL.
  4. build_ytdlp_opts()   — return yt-dlp option dict ready to download V2 content.

Usage:
  from bot.v2_bypass import try_get_v2_hls_url, try_appx_v2_api, build_ytdlp_opts
"""

import asyncio
import logging
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger(__name__)

# V2 CDN host
APPX_V2_TRANS_HOST = "static-trans-v2.appx.co.in"

# Regex: /videos/{course}/{contentId}/{hash}/{quality}/
_V2_PATH_RE = re.compile(
    r"^/videos/([^/]+)/([^/]+)/([^/]+)/([^/]+)(?:/|$)",
    re.IGNORECASE,
)

# Quality tiers AppX uses (best-first for download)
_QUALITY_TIERS = ["1080p", "720p", "480p", "360p", "240p"]


def is_appx_v2_video(url: str) -> bool:
    """Return True if this URL is an AppX V2 encrypted video URL."""
    lo = url.lower()
    return APPX_V2_TRANS_HOST in lo and "/videos/" in lo


def parse_v2_video_info(url: str) -> Optional[Dict]:
    """
    Extract metadata from an AppX V2 video URL.

    Returns dict with keys: course_code, content_id, enc_hash, quality, base_path
    or None if URL doesn't match the V2 pattern.
    """
    p = urlparse(url)
    if APPX_V2_TRANS_HOST not in (p.hostname or ""):
        return None
    m = _V2_PATH_RE.match(p.path)
    if not m:
        return None
    return {
        "course_code": m.group(1),   # e.g. "akstechnicalclasses-data"
        "content_id":  m.group(2),   # e.g. "3661794-1777913165"
        "enc_hash":    m.group(3),   # e.g. "encrypted-c400d5"
        "quality":     m.group(4),   # e.g. "360p"
        "base_path":   m.group(0).rstrip("/"),  # /videos/.../360p
    }


def _clean_v2_path(url: str) -> str:
    """
    Remove the /encrypted.mkv/encrypted.mkv doubled-suffix from the URL path.
    Returns the directory base URL (ends at quality segment).
    e.g. .../360p/encrypted.mkv/encrypted.mkv  →  .../360p
    """
    p = urlparse(url)
    path = re.sub(r"(/encrypted\.mkv)+$", "", p.path.rstrip("/"))
    return f"https://{p.hostname}{path}"


async def try_get_v2_hls_url(
    session: aiohttp.ClientSession,
    url:     str,
    token:   str,
    proxy:   str = None,
) -> Optional[str]:
    """
    Probe common HLS manifest paths adjacent to the encrypted.mkv file.

    AppX V2 often has HLS manifests alongside the encrypted MKV. yt-dlp can
    download + auto-decrypt AES-128 HLS streams when provided auth headers.

    Returns the first valid m3u8 URL, or None.
    """
    base = _clean_v2_path(url)
    info = parse_v2_video_info(url)

    hls_candidates: List[str] = []

    # Direct quality-level manifests
    hls_candidates += [
        f"{base}/index.m3u8",
        f"{base}/playlist.m3u8",
        f"{base}/master.m3u8",
        f"{base}/hls.m3u8",
        f"{base}/stream.m3u8",
    ]

    # Go one level up (course/content level) if we have the info
    if info:
        enc_base = f"https://{APPX_V2_TRANS_HOST}/videos/{info['course_code']}/{info['content_id']}/{info['enc_hash']}"
        hls_candidates += [
            f"{enc_base}/index.m3u8",
            f"{enc_base}/master.m3u8",
            f"{enc_base}/playlist.m3u8",
        ]
        # Try other quality tiers too
        for q in _QUALITY_TIERS:
            if q != info.get("quality"):
                hls_candidates.append(f"{enc_base}/{q}/index.m3u8")

    headers = {
        "Authorization": f"Bearer {token}",
        "Referer":       "https://appx.co.in/",
        "Origin":        "https://appx.co.in",
    }
    timeout = aiohttp.ClientTimeout(total=8)

    for candidate in hls_candidates:
        try:
            async with session.get(
                candidate, headers=headers,
                allow_redirects=True, proxy=proxy, timeout=timeout,
            ) as r:
                if r.status == 200:
                    text = await r.text()
                    if "#EXTM3U" in text:
                        logger.info("V2 HLS found: %s", candidate)
                        return candidate
        except Exception as e:
            logger.debug("HLS probe %s: %s", candidate[:60], e)

    logger.debug("V2 HLS: no manifest found near %s", url[:70])
    return None


async def try_appx_v2_api(
    session: aiohttp.ClientSession,
    url:     str,
    token:   str,
    proxy:   str = None,
) -> Optional[str]:
    """
    Call AppX API endpoints to get a better download URL for this V2 content.

    Tries both content-ID-based endpoints and URL-resolution endpoints.
    Returns a usable URL (HLS or direct download) or None.
    """
    from config.settings import APPX_API_BASE

    info = parse_v2_video_info(url)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
        "Referer":       "https://appx.co.in/",
        "Origin":        "https://appx.co.in",
    }

    endpoints = []
    if info:
        cid = info["content_id"]
        crs = info["course_code"]
        q   = info["quality"]

        endpoints += [
            # Content-ID based (most likely to work)
            ("POST", f"{APPX_API_BASE}/api/v2/content/getUrl",
             {"contentId": cid, "courseCode": crs, "quality": q, "type": "video"}),
            ("POST", f"{APPX_API_BASE}/api/v2/media/getUrl",
             {"contentId": cid, "type": "video", "quality": q}),
            ("POST", f"{APPX_API_BASE}/api/v2/content/signed-url",
             {"contentId": cid, "type": "video"}),
            ("GET",  f"{APPX_API_BASE}/api/v2/lectures/{cid}/url", None),
            ("GET",  f"{APPX_API_BASE}/api/v2/content/{cid}/media", None),
            ("GET",  f"{APPX_API_BASE}/api/v1/lectures/{cid}/url", None),
            ("POST", f"{APPX_API_BASE}/api/v2/media/signed-url",
             {"contentId": cid, "courseCode": crs}),
        ]

    # URL-resolution based (works for any URL)
    endpoints += [
        ("POST", f"{APPX_API_BASE}/api/v2/content/stream",  {"url": url}),
        ("POST", f"{APPX_API_BASE}/api/v2/media/resolve",   {"resourceUrl": url}),
        ("POST", f"{APPX_API_BASE}/api/v2/content/resolve", {"url": url, "type": "video"}),
        ("POST", f"{APPX_API_BASE}/api/v2/content/getSignedUrl", {"url": url}),
    ]

    timeout = aiohttp.ClientTimeout(total=15)

    for method, ep, payload in endpoints:
        try:
            ctx = (
                session.post(ep, json=payload, headers=headers,
                             proxy=proxy, timeout=timeout)
                if method == "POST"
                else session.get(ep, headers=headers, proxy=proxy, timeout=timeout)
            )
            async with ctx as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    result = (
                        data.get("url")
                        or data.get("hlsUrl")
                        or data.get("signedUrl")
                        or data.get("downloadUrl")
                        or data.get("streamUrl")
                        or data.get("data", {}).get("url")
                        or data.get("data", {}).get("hlsUrl")
                        or data.get("data", {}).get("signedUrl")
                        or data.get("data", {}).get("downloadUrl")
                    )
                    if result and result.startswith("http"):
                        logger.info("V2 API ✅ %s %s → %s", method, ep, result[:80])
                        return result
        except Exception as e:
            logger.debug("V2 API %s %s: %s", method, ep, e)

    return None


def build_ytdlp_opts(
    output_path:  str,
    headers:      Dict,
    cookies_file: str  = None,
    proxy:        str  = None,
    progress_hook        = None,
) -> Dict:
    """
    Build yt-dlp options dict for downloading AppX V2 content.

    Key settings for AppX V2:
    - http_headers: passes Bearer token and AppX browser headers to every request
    - allow_unplayable_formats: allows downloading encrypted containers
    - concurrent_fragment_downloads: 4 (safe for AppX CDN)
    """
    from config.settings import YTDLP_CONCURRENCY
    import os

    opts = {
        "outtmpl":                       output_path,
        "merge_output_format":           "mp4",
        "quiet":                         True,
        "no_warnings":                   False,
        "noprogress":                    True,
        "retries":                       8,
        "fragment_retries":              10,
        "skip_unavailable_fragments":    False,
        "ignoreerrors":                  False,
        "http_headers":                  headers,
        "hls_use_mpegts":                True,
        "concurrent_fragment_downloads": min(YTDLP_CONCURRENCY, 4),
        "buffersize":                    256 * 1024,
        "http_chunk_size":               10 * 1024 * 1024,
        "socket_timeout":                30,
        "allow_unplayable_formats":      True,   # allows encrypted containers
        "fixup":                         "never",
        "postprocessors": [{
            "key":            "FFmpegVideoConvertor",
            "preferedformat": "mp4",
        }],
    }
    if cookies_file and os.path.isfile(cookies_file):
        opts["cookiefile"] = cookies_file
    if proxy:
        opts["proxy"] = proxy
    if progress_hook:
        opts["progress_hooks"] = [progress_hook]

    return opts


async def resolve_v2_best_url(
    session: aiohttp.ClientSession,
    url:     str,
    token:   str,
    proxy:   str = None,
) -> Tuple[str, str]:
    """
    Full V2 resolution: try HLS → API → original.

    Returns (best_url, url_type) where url_type is "hls", "api", or "direct".
    """
    # Try HLS first (best for yt-dlp — handles AES decryption automatically)
    hls = await try_get_v2_hls_url(session, url, token, proxy)
    if hls:
        return hls, "hls"

    # Try API
    api_url = await try_appx_v2_api(session, url, token, proxy)
    if api_url:
        return api_url, "api"

    # Fall back to deduplicated direct URL
    from bot.drm import appx_dedup_path
    deduped = appx_dedup_path(url) or url
    return deduped, "direct"
