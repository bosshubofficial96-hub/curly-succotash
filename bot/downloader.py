"""
Async file downloader with progress callbacks, retry logic, and DRM resolution.
"""

import os
import asyncio
import logging
import mimetypes
import hashlib
import time
from typing import Optional, Callable, Tuple, Dict
from urllib.parse import urlparse, unquote
import aiohttp
import aiofiles

from .drm import DRMResolver, is_valid_url, download_stream, try_decrypt_pdf
from config.settings import (
    DOWNLOAD_TIMEOUT, MAX_RETRIES, RETRY_DELAY,
    CHUNK_SIZE, MAX_FILE_SIZE_MB, TEMP_DIR, APPX_HEADERS,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], None]   # bytes_done, total_bytes


# ---------------------------------------------------------------------------
# Filename utilities
# ---------------------------------------------------------------------------

UNSAFE_CHARS_RE = __import__("re").compile(r'[<>:"/\\|?*\x00-\x1f]')

def safe_filename(name: str, max_len: int = 200) -> str:
    name = unquote(name)
    name = UNSAFE_CHARS_RE.sub("_", name)
    name = name.strip(". ")
    return name[:max_len] or "file"


def extract_filename(url: str, content_disposition: str = None,
                     content_type: str = None) -> str:
    # 1. Content-Disposition header
    if content_disposition:
        import re
        m = re.search(r'filename\*?=["\']?(?:UTF-8\'\')?([^"\';\r\n]+)', content_disposition, re.I)
        if m:
            return safe_filename(m.group(1).strip())

    # 2. URL path
    path = urlparse(url).path
    basename = os.path.basename(path)
    if basename and "." in basename:
        return safe_filename(basename)

    # 3. Fallback using content type
    ext = ""
    if content_type:
        ct = content_type.split(";")[0].strip()
        ext = mimetypes.guess_extension(ct) or ""
        if ext == ".jpe":
            ext = ".jpg"
    return f"file_{int(time.time())}{ext}"


def detect_mime(path: str, content_type: str = None) -> str:
    if content_type:
        return content_type.split(";")[0].strip()
    mime, _ = mimetypes.guess_type(path)
    return mime or "application/octet-stream"


# ---------------------------------------------------------------------------
# Core downloader
# ---------------------------------------------------------------------------

class Downloader:
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(limit=10, limit_per_host=5, ssl=False)
            timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT, connect=30)
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers=APPX_HEADERS,
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def download(
        self,
        url: str,
        dest_dir: str = TEMP_DIR,
        progress_cb: ProgressCallback = None,
        job_id: str = None,
    ) -> Tuple[Optional[str], str, str]:
        """
        Download *url* into *dest_dir*.

        Returns (local_path, filename, mime_type).
        Raises on unrecoverable error.
        """
        if not is_valid_url(url):
            raise ValueError(f"Invalid or unsafe URL: {url[:80]}")

        session = await self._get_session()
        resolver = DRMResolver(session)
        resolved_url, headers, url_type = await resolver.resolve(url)

        # HLS / DASH → yt-dlp path
        if url_type in ("hls", "dash"):
            out_path = os.path.join(dest_dir, f"stream_{job_id or int(time.time())}.mp4")
            ok = await download_stream(resolved_url, out_path, headers)
            if ok and os.path.exists(out_path):
                fname = os.path.basename(out_path)
                return out_path, fname, "video/mp4"
            raise RuntimeError(f"Stream download failed for {url[:80]}")

        # Regular HTTP download with retry
        last_error: Exception = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                result = await self._http_download(
                    session, resolved_url, headers, dest_dir, progress_cb
                )
                return result
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_error = e
                logger.warning("Download attempt %d/%d failed for %s: %s",
                               attempt, MAX_RETRIES, url[:60], e)
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY * attempt)
            except Exception as e:
                logger.error("Non-retryable download error for %s: %s", url[:60], e)
                raise

        raise last_error or RuntimeError("Download failed after all retries")

    async def _http_download(
        self,
        session: aiohttp.ClientSession,
        url: str,
        headers: Dict,
        dest_dir: str,
        progress_cb: ProgressCallback,
    ) -> Tuple[str, str, str]:
        os.makedirs(dest_dir, exist_ok=True)

        async with session.get(url, headers=headers, allow_redirects=True) as resp:
            resp.raise_for_status()

            content_disposition = resp.headers.get("Content-Disposition", "")
            content_type = resp.headers.get("Content-Type", "")
            content_length = int(resp.headers.get("Content-Length", 0))

            # Size guard
            if content_length and content_length > MAX_FILE_SIZE_MB * 1024 * 1024:
                raise ValueError(
                    f"File too large: {content_length / 1024 / 1024:.1f} MB "
                    f"(limit {MAX_FILE_SIZE_MB} MB)"
                )

            filename = extract_filename(url, content_disposition, content_type)
            local_path = os.path.join(dest_dir, filename)

            # Avoid overwriting — append suffix
            if os.path.exists(local_path):
                base, ext = os.path.splitext(filename)
                local_path = os.path.join(dest_dir, f"{base}_{int(time.time())}{ext}")
                filename = os.path.basename(local_path)

            downloaded = 0
            async with aiofiles.open(local_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                    await f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb and content_length:
                        progress_cb(downloaded, content_length)

        actual_size = os.path.getsize(local_path)
        mime = detect_mime(local_path, content_type)

        # PDF post-processing: try to remove encryption
        if mime == "application/pdf" or local_path.lower().endswith(".pdf"):
            decrypted = local_path + ".dec.pdf"
            if try_decrypt_pdf(local_path, decrypted):
                os.replace(decrypted, local_path)

        logger.info("Downloaded %s → %s (%d bytes)", url[:60], filename, actual_size)
        return local_path, filename, mime


# Module-level singleton
_downloader: Optional[Downloader] = None


def get_downloader() -> Downloader:
    global _downloader
    if _downloader is None:
        _downloader = Downloader()
    return _downloader
