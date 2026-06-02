"""
downloader.py - Asynchronous file downloader with progress tracking,
retries, range request resume support, and temporary file management.
"""

import asyncio
import os
import re
import hashlib
from pathlib import Path
from typing import Optional, Callable, Awaitable, Tuple
from urllib.parse import urlparse, unquote

import aiohttp
import aiofiles
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config import Config
from database import Database

try:
    import magic
    HAS_MAGIC = True
except ImportError:
    HAS_MAGIC = False


class DownloadError(Exception):
    """Custom exception for download failures."""
    pass


class Downloader:
    def __init__(self, db: Database, logger):
        self.db = db
        self.logger = logger
        self.session: Optional[aiohttp.ClientSession] = None
        self.timeout = aiohttp.ClientTimeout(total=Config.DOWNLOAD_TIMEOUT)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            connector = aiohttp.TCPConnector(limit=5, force_close=False, enable_cleanup_closed=True)
            self.session = aiohttp.ClientSession(
                headers=Config.DEFAULT_HEADERS,
                timeout=self.timeout,
                connector=connector
            )
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    def _extract_filename(self, url: str, content_disposition: Optional[str] = None) -> str:
        """Extract filename from URL or Content-Disposition header."""
        if content_disposition:
            match = re.search(r'filename[*]?=["\']?([^"\']+)["\']?', content_disposition, re.I)
            if match:
                return unquote(match.group(1))
        parsed = urlparse(url)
        path = unquote(parsed.path)
        name = os.path.basename(path)
        if not name or '.' not in name:
            name = f"file_{hashlib.md5(url.encode()).hexdigest()[:8]}"
        # Remove unsafe characters
        return re.sub(r'[<>:"/\\|?*]', '_', name)

    async def _detect_mime(self, file_path: Path, url: str) -> str:
        """Detect MIME type of downloaded file."""
        if HAS_MAGIC:
            mime = magic.from_file(str(file_path), mime=True)
            if mime:
                return mime
        ext = file_path.suffix.lower()
        mime_map = {
            '.pdf': 'application/pdf',
            '.mp4': 'video/mp4',
            '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.txt': 'text/plain',
            '.doc': 'application/msword',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.zip': 'application/zip',
            '.rar': 'application/x-rar-compressed',
        }
        return mime_map.get(ext, 'application/octet-stream')

    @retry(
        stop=stop_after_attempt(Config.MAX_RETRIES),
        wait=wait_exponential(multiplier=Config.RETRY_BACKOFF_FACTOR, min=2, max=30),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError, DownloadError)),
        reraise=True
    )
    async def download(
        self,
        url: str,
        user_id: int,
        job_id: int,
        progress_callback: Optional[Callable[[int, int], Awaitable[None]]] = None,
        start_byte: int = 0
    ) -> Tuple[Path, str, int]:
        """
        Download a file with resume support.

        Args:
            url: Source URL
            user_id: Telegram user ID (for logging)
            job_id: Job ID in database
            progress_callback: Async callback(current_bytes, total_bytes)
            start_byte: Byte offset to resume from (0 for new download)

        Returns:
            Tuple of (local_temp_file_path, detected_mime_type, final_file_size)

        Raises:
            DownloadError on unrecoverable failure.
        """
        session = await self._get_session()
        headers = Config.get_headers_for_url(url)
        if start_byte > 0:
            headers["Range"] = f"bytes={start_byte}-"
            self.logger.info(f"Resuming download at byte {start_byte} for {url[:80]}")

        async with session.get(url, headers=headers, allow_redirects=True) as response:
            # Handle 416 (Range Not Satisfiable) – start over
            if response.status == 416:
                start_byte = 0
                headers.pop("Range", None)
                async with session.get(url, headers=headers, allow_redirects=True) as resp2:
                    return await self._perform_download(resp2, url, job_id, progress_callback, 0)
            elif response.status == 404:
                raise DownloadError(f"File not found (404): {url}")
            elif response.status == 403:
                raise DownloadError(f"Access forbidden (403): {url}")
            elif response.status >= 400:
                raise DownloadError(f"HTTP {response.status}: {url}")

            total_size = response.headers.get("Content-Length")
            total_size = int(total_size) if total_size and total_size.isdigit() else None

            # Parse Content-Range for resume
            content_range = response.headers.get("Content-Range")
            if content_range and start_byte > 0:
                match = re.search(r'bytes \d+-(\d+)/(\d+)', content_range)
                if match:
                    total_size = int(match.group(2))

            return await self._perform_download(response, url, job_id, progress_callback, start_byte, total_size)

    async def _perform_download(
        self,
        response: aiohttp.ClientResponse,
        url: str,
        job_id: int,
        progress_callback: Optional[Callable[[int, int], Awaitable[None]]],
        start_byte: int,
        total_size: int = None
    ) -> Tuple[Path, str, int]:
        """Internal method to write chunks and track progress."""
        filename = self._extract_filename(url, response.headers.get("Content-Disposition"))
        temp_path = Config.USER_DATA_DIR / f"{job_id}_{filename}"

        # If total_size not known, try to get from response
        if total_size is None:
            total_size = response.headers.get("Content-Length")
            total_size = int(total_size) if total_size and total_size.isdigit() else None

        mode = 'ab' if start_byte > 0 else 'wb'
        downloaded = start_byte

        async with aiofiles.open(temp_path, mode) as f:
            async for chunk in response.content.iter_chunked(Config.CHUNK_SIZE):
                await f.write(chunk)
                downloaded += len(chunk)
                if progress_callback and total_size:
                    await progress_callback(downloaded, total_size)

        mime = await self._detect_mime(temp_path, url)
        return temp_path, mime, downloaded
