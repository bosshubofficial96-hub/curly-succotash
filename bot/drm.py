"""
DRM / signed-URL bypass helpers for AppX (appx.co.in) and related CDN formats.

Supported schemes
-----------------
1. Google Cloud CDN Key-signed URLs  (URLPrefix + Expires + KeyName + Signature)
2. AppX live/encrypted PDF links     (encrypted_*.pdf wrapper)
3. AppX HLS / DASH streams           (m3u8 / mpd manifest rewriting)
4. Generic signed S3-style links     (AWSAccessKeyId / X-Amz-Signature)
5. Cloudflare signed URLs            (verify via direct fetch with headers)
"""

import re
import base64
import logging
import hashlib
import asyncio
import os
from typing import Optional, Tuple, Dict
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse, quote
import aiohttp
import aiofiles

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _b64_decode_safe(s: str) -> bytes:
    """Decode URL-safe or standard base64, padding as needed."""
    s = s.replace("-", "+").replace("_", "/")
    pad = 4 - len(s) % 4
    if pad != 4:
        s += "=" * pad
    return base64.b64decode(s)


def _extract_appx_real_url(url: str) -> Optional[str]:
    """
    AppX encrypted PDF/video links encode the actual resource path in
    the URLPrefix query parameter (base64).  Decode it to get the real URL
    before trying to fetch (the signed URL itself may still be valid).
    """
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    prefix_b64 = qs.get("URLPrefix", [None])[0]
    if not prefix_b64:
        return None
    try:
        real_url = _b64_decode_safe(prefix_b64).decode("utf-8")
        return real_url
    except Exception:
        return None


def detect_url_type(url: str) -> str:
    """
    Returns a tag describing what kind of link this is.
    Tags: appx_signed, hls, dash, s3_signed, generic
    """
    lower = url.lower()
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    if "appx.co.in" in lower or "appx-pdf-keyset" in lower:
        return "appx_signed"
    if ".m3u8" in lower or "playlist" in lower:
        return "hls"
    if ".mpd" in lower:
        return "dash"
    if "x-amz-signature" in lower or "awsaccesskeyid" in lower:
        return "s3_signed"
    return "generic"


# ---------------------------------------------------------------------------
# Session / header factories
# ---------------------------------------------------------------------------

def build_appx_headers(extra: Dict = None) -> Dict:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://appx.co.in/",
        "Origin": "https://appx.co.in",
        "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-site",
    }
    if extra:
        headers.update(extra)
    return headers


# ---------------------------------------------------------------------------
# DRM / link resolution pipeline
# ---------------------------------------------------------------------------

class DRMResolver:
    """
    Attempts multiple strategies to resolve a potentially DRM-protected or
    signed URL into a directly downloadable stream.
    """

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def resolve(self, url: str) -> Tuple[Optional[str], Dict, str]:
        """
        Returns (resolved_url, headers, detected_type).
        resolved_url may equal url if no transformation is needed.
        """
        url_type = detect_url_type(url)
        logger.debug("URL type detected: %s  for  %s", url_type, url[:80])

        if url_type == "appx_signed":
            return await self._resolve_appx(url)
        if url_type in ("hls", "dash"):
            return url, build_appx_headers(), url_type
        if url_type == "s3_signed":
            return url, {}, "s3_signed"
        return url, build_appx_headers(), "generic"

    async def _resolve_appx(self, url: str) -> Tuple[Optional[str], Dict, str]:
        """
        Strategy order for AppX:
        1. Try the signed URL directly — it may still be within its Expires window.
        2. Decode URLPrefix and try the raw (unsigned) URL with spoofed headers.
        3. Try common CDN key rotation patterns.
        """
        headers = build_appx_headers()

        # Strategy 1: direct signed URL
        try:
            async with self.session.head(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15), allow_redirects=True) as resp:
                if resp.status < 400:
                    logger.info("AppX direct signed URL is valid (status %s)", resp.status)
                    return url, headers, "appx_signed"
        except Exception as e:
            logger.debug("AppX direct HEAD failed: %s", e)

        # Strategy 2: decode URLPrefix and try raw URL
        real_url = _extract_appx_real_url(url)
        if real_url:
            logger.info("AppX URLPrefix decoded → %s", real_url[:80])
            try:
                async with self.session.head(real_url, headers=headers, timeout=aiohttp.ClientTimeout(total=15), allow_redirects=True) as resp:
                    if resp.status < 400:
                        logger.info("AppX raw URL is accessible (status %s)", resp.status)
                        return real_url, headers, "appx_signed"
            except Exception as e:
                logger.debug("AppX raw URL HEAD failed: %s", e)

        # Strategy 3: strip signature and try with Referer only
        try:
            parsed = urlparse(url)
            qs = parse_qs(parsed.query)
            # Remove signature-related params and try
            for key in ("Signature", "KeyName", "Expires", "URLPrefix"):
                qs.pop(key, None)
            clean_query = urlencode({k: v[0] for k, v in qs.items()})
            clean_url = urlunparse(parsed._replace(query=clean_query))
            async with self.session.head(clean_url, headers=headers, timeout=aiohttp.ClientTimeout(total=15), allow_redirects=True) as resp:
                if resp.status < 400:
                    logger.info("AppX stripped-signature URL is accessible (status %s)", resp.status)
                    return clean_url, headers, "appx_signed"
        except Exception as e:
            logger.debug("AppX stripped URL HEAD failed: %s", e)

        # Fallback: use original URL anyway and hope GET works even if HEAD fails
        logger.warning("All AppX resolution strategies failed; using original URL as fallback")
        return url, headers, "appx_signed"


# ---------------------------------------------------------------------------
# HLS / DASH stream downloader  (uses yt-dlp if available)
# ---------------------------------------------------------------------------

async def download_stream(url: str, output_path: str, headers: Dict = None) -> bool:
    """
    Attempt to download an HLS/DASH stream using yt-dlp.
    Returns True on success.
    """
    try:
        import yt_dlp  # optional dependency
    except ImportError:
        logger.warning("yt-dlp not installed; cannot download stream %s", url[:60])
        return False

    header_args = []
    if headers:
        for k, v in headers.items():
            header_args += ["--add-header", f"{k}:{v}"]

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--merge-output-format", "mp4",
        "-o", output_path,
        *header_args,
        url,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
        if proc.returncode == 0:
            logger.info("yt-dlp succeeded for %s", url[:60])
            return True
        logger.warning("yt-dlp exit=%s stderr=%s", proc.returncode, stderr.decode()[-300:])
        return False
    except asyncio.TimeoutError:
        logger.error("yt-dlp timed out for %s", url[:60])
        return False
    except Exception as e:
        logger.error("yt-dlp error: %s", e)
        return False


# ---------------------------------------------------------------------------
# PDF decryption helper
# ---------------------------------------------------------------------------

def try_decrypt_pdf(src_path: str, dst_path: str) -> bool:
    """
    Attempt to remove PDF encryption using pikepdf.
    Returns True if the output file was written.
    """
    try:
        import pikepdf  # optional dependency
        with pikepdf.open(src_path, password="") as pdf:
            pdf.save(dst_path)
        logger.info("PDF decrypted: %s → %s", src_path, dst_path)
        return True
    except ImportError:
        logger.warning("pikepdf not installed; skipping PDF decryption")
        return False
    except Exception as e:
        logger.warning("PDF decryption failed (%s): %s", os.path.basename(src_path), e)
        return False


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------

BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
URL_RE = re.compile(
    r"^https?://"
    r"(?:[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+)$"
)


def is_valid_url(url: str) -> bool:
    if not url or not URL_RE.match(url):
        return False
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if host in BLOCKED_HOSTS:
        return False
    if host.startswith("192.168.") or host.startswith("10.") or host.startswith("172."):
        return False
    return True
