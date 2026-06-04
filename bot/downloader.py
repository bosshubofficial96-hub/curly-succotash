"""
Speed-optimised async HTTP downloader — v3 FIXED.

Improvements over v2:
  • 8 MB chunk size (was 2 MB)
  • TCPConnector: 16 total / 8 per host connections (was 8/4)
  • aiofiles for non-blocking disk I/O
  • Range-based parallel download for large files (≥ 50 MB)
  • Correct cookie forwarding to DRMResolver
  • Per-download speed tracking (bytes/sec)
  • resolve_url() method for bypass-link-only resolution (no download)
"""

import asyncio
import logging
import mimetypes
import os
import re
import time
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import unquote, urlparse

import aiofiles
import aiohttp

from config.settings import (
    APPX_HEADERS, CHUNK_SIZE, CONNECT_TIMEOUT, DOWNLOAD_TIMEOUT,
    HTTP_PROXY, MAX_CONNECTIONS, MAX_CONNECTIONS_PER_HOST,
    MAX_FILE_SIZE_MB, MAX_RETRIES, READ_TIMEOUT, RETRY_DELAY, TEMP_DIR,
    YTDLP_COOKIES_FILE,
)
from .drm import DRMResolver, classify_url as classify, download_stream, is_valid_url, decrypt_pdf as try_decrypt_pdf
logger = logging.getLogger(__name__)

ProgressCB = Callable[[int, int], None]

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_PARALLEL_THRESHOLD = 50 * 1024 * 1024   # 50 MB → use parallel range download
_RANGE_WORKERS      = 4                   # concurrent range workers


# ── Speed tracker ─────────────────────────────────────────────────────────────
class SpeedTracker:
    """Rolling 5-second average speed tracker."""
    def __init__(self):
        self._samples: list = []   # (timestamp, bytes)
        self._window  = 5.0

    def record(self, n_bytes: int) -> None:
        now = time.monotonic()
        self._samples.append((now, n_bytes))
        # prune old samples
        cutoff = now - self._window
        self._samples = [(t, b) for t, b in self._samples if t >= cutoff]

    @property
    def bps(self) -> float:
        if not self._samples:
            return 0.0
        total = sum(b for _, b in self._samples)
        span  = self._samples[-1][0] - self._samples[0][0]
        return total / span if span > 0 else total / self._window

    def fmt(self) -> str:
        b = self.bps
        if b < 1024:       return f"{b:.0f} B/s"
        if b < 1 << 20:    return f"{b/1024:.1f} KB/s"
        return f"{b/(1<<20):.1f} MB/s"


# global per-session speed tracker
_speed_tracker = SpeedTracker()


def get_speed_tracker() -> SpeedTracker:
    return _speed_tracker


# ── Filename helpers ──────────────────────────────────────────────────────────
def _safe(name: str, mx: int = 200) -> str:
    name = unquote(name)
    name = _UNSAFE.sub("_", name)
    return name.strip(". ")[:mx] or "file"


def _get_filename(url: str, cd: str = "", ct: str = "",
                   title: str = "") -> str:
    if cd:
        m = re.search(r'filename\*?=["\']?(?:UTF-8\'\')?([^"\';\r\n]+)', cd, re.I)
        if m:
            return _safe(m.group(1).strip())
    base = os.path.basename(urlparse(url).path.split("?")[0])
    if base and "." in base:
        stem, ext = os.path.splitext(_safe(base))
        if title:
            return _safe(title)[:180] + ext
        return _safe(base)
    ext = ""
    if ct:
        raw = ct.split(";")[0].strip()
        ext = mimetypes.guess_extension(raw) or ""
        if ext == ".jpe":
            ext = ".jpg"
    stem = _safe(title) if title else f"file_{int(time.time())}"
    return stem[:180] + ext


def _mime(path: str, ct: str = "") -> str:
    if ct:
        return ct.split(";")[0].strip()
    m, _ = mimetypes.guess_type(path)
    return m or "application/octet-stream"


def _uniq(d: str, name: str) -> str:
    p = os.path.join(d, name)
    if not os.path.exists(p):
        return p
    b, e = os.path.splitext(name)
    return os.path.join(d, f"{b}_{int(time.time())}{e}")


# ── Parallel range download ───────────────────────────────────────────────────
async def _parallel_download(
    session: aiohttp.ClientSession,
    url: str,
    headers: Dict,
    path: str,
    total: int,
    cb: Optional[ProgressCB],
    proxy: str = None,
) -> None:
    """Download large file in parallel chunks using Range requests."""
    part_size = total // _RANGE_WORKERS + 1
    parts     = []
    done_lock = asyncio.Lock()
    done_bytes = [0]

    async def _fetch_part(start: int, end: int, part_path: str) -> None:
        h = dict(headers)
        h["Range"] = f"bytes={start}-{end}"
        async with session.get(
            url, headers=h, allow_redirects=True,
            proxy=proxy, timeout=aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT),
        ) as r:
            async with aiofiles.open(part_path, "wb") as fh:
                async for chunk in r.content.iter_chunked(CHUNK_SIZE):
                    await fh.write(chunk)
                    _speed_tracker.record(len(chunk))
                    async with done_lock:
                        done_bytes[0] += len(chunk)
                        if cb:
                            cb(done_bytes[0], total)

    tasks = []
    for i in range(_RANGE_WORKERS):
        start = i * part_size
        end   = min(start + part_size - 1, total - 1)
        ppath = f"{path}.part{i}"
        parts.append(ppath)
        tasks.append(asyncio.create_task(_fetch_part(start, end, ppath)))

    await asyncio.gather(*tasks)

    # Concatenate parts
    async with aiofiles.open(path, "wb") as out:
        for ppath in parts:
            async with aiofiles.open(ppath, "rb") as inp:
                await out.write(await inp.read())
            os.remove(ppath)


# ── Main Downloader ────────────────────────────────────────────────────────────
class Downloader:
    _session: Optional[aiohttp.ClientSession] = None

    async def _sess(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(
                    limit=MAX_CONNECTIONS,
                    limit_per_host=MAX_CONNECTIONS_PER_HOST,
                    ssl=False,
                    enable_cleanup_closed=True,
                    keepalive_timeout=60,
                ),
                headers=APPX_HEADERS,
                timeout=aiohttp.ClientTimeout(
                    total=DOWNLOAD_TIMEOUT,
                    connect=CONNECT_TIMEOUT,
                    sock_read=READ_TIMEOUT,
                ),
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def resolve_url(
        self,
        url:      str,
        cookie:   str  = "",
        drm_keys: Dict = None,
    ) -> Tuple[str, Dict, str]:
        """
        Resolve/bypass a URL without downloading the file.

        Returns (resolved_url, headers, kind).
        Useful for sending bypass links to channels before downloading.
        Raises on invalid URL.
        """
        if not is_valid_url(url):
            raise ValueError(f"Invalid/blocked URL: {url[:80]}")
        sess     = await self._sess()
        resolver = DRMResolver(sess, cookie=cookie, drm_keys=drm_keys or {}, proxy=HTTP_PROXY)
        return await resolver.resolve(url)

    async def download(
        self,
        url:         str,
        dest_dir:    str         = TEMP_DIR,
        progress_cb: ProgressCB = None,
        job_id:      str        = "",
        cookie:      str        = "",
        drm_keys:    Dict       = None,
        title:       str        = "",
    ) -> Tuple[str, str, str]:
        """
        Returns (local_path, filename, mime_type).
        Raises on unrecoverable failure.
        """
        if not is_valid_url(url):
            raise ValueError(f"Invalid/blocked URL: {url[:80]}")

        os.makedirs(dest_dir, exist_ok=True)
        sess     = await self._sess()
        resolver = DRMResolver(sess, cookie=cookie, drm_keys=drm_keys, proxy=HTTP_PROXY)
        resolved, headers, kind = await resolver.resolve(url)

        # ── Stream (HLS/DASH/YouTube etc.) ───────────────────────────────
        if kind in ("hls", "dash", "vimeo", "youtube", "jwp"):
            def _hook(d):
                if progress_cb and d.get("status") == "downloading":
                    try:
                        done = d.get("downloaded_bytes", 0)
                        tot  = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                        if tot:
                            progress_cb(done, tot)
                        _speed_tracker.record(
                            d.get("speed", 0) or 0
                        )
                    except Exception:
                        pass

            stem = _safe(title) if title else f"stream_{job_id or int(time.time())}"
            out  = _uniq(dest_dir, f"{stem}.mp4")
            ok   = await download_stream(
                resolved or url, out, headers,
                cookies_file=YTDLP_COOKIES_FILE if os.path.isfile(YTDLP_COOKIES_FILE) else None,
                drm_keys=drm_keys,
                proxy=HTTP_PROXY,
                progress_hook=_hook,
            )
            if ok and os.path.exists(out):
                return out, os.path.basename(out), "video/mp4"
            raise RuntimeError(f"Stream download failed: {url[:80]}")

        # ── HTTP download with retry ──────────────────────────────────────
        last_err: Exception = RuntimeError("download never started")
        for att in range(1, MAX_RETRIES + 1):
            try:
                return await self._http_get(
                    sess, resolved or url, headers, dest_dir,
                    progress_cb, title=title,
                )
            except aiohttp.ClientResponseError as e:
                if e.status in (401, 403) and att == 1:
                    # Re-resolve with a fresh strategy
                    new_url, new_h, _ = await resolver.resolve(url)
                    if new_url and new_url != (resolved or url):
                        try:
                            return await self._http_get(
                                sess, new_url, new_h, dest_dir,
                                progress_cb, title=title,
                            )
                        except Exception:
                            pass
                last_err = e
                logger.warning(
                    "[att %d/%d] HTTP %s → %s", att, MAX_RETRIES, e.status, url[:70]
                )
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
                last_err = e
                logger.warning(
                    "[att %d/%d] %s → %s", att, MAX_RETRIES, type(e).__name__, url[:70]
                )
            if att < MAX_RETRIES:
                await asyncio.sleep(min(RETRY_DELAY * att, 30))

        raise last_err

    async def _http_get(
        self,
        sess:    aiohttp.ClientSession,
        url:     str,
        headers: Dict,
        dest_dir:str,
        cb:      Optional[ProgressCB],
        title:   str = "",
    ) -> Tuple[str, str, str]:
        # First do a lightweight HEAD to check size + check range support
        supports_range = False
        cl             = 0
        try:
            async with sess.head(
                url, headers=headers, allow_redirects=True,
                proxy=HTTP_PROXY, timeout=aiohttp.ClientTimeout(total=10),
            ) as hr:
                if hr.status < 400:
                    cl             = int(hr.headers.get("Content-Length", 0))
                    supports_range = "bytes" in hr.headers.get("Accept-Ranges", "")
                    cd_head        = hr.headers.get("Content-Disposition", "")
                    ct_head        = hr.headers.get("Content-Type", "")
        except Exception:
            cd_head = ct_head = ""

        if cl and cl > MAX_FILE_SIZE_MB * 1024 * 1024:
            raise ValueError(
                f"File too large: {cl / (1<<20):.0f} MB (limit {MAX_FILE_SIZE_MB} MB)"
            )

        # Parallel download for large files when server supports Range
        if supports_range and cl >= _PARALLEL_THRESHOLD:
            fname = _get_filename(url, cd_head, ct_head, title)
            path  = _uniq(dest_dir, fname)
            await _parallel_download(sess, url, headers, path, cl, cb, HTTP_PROXY)
        else:
            # Sequential stream download
            async with sess.get(
                url, headers=headers, allow_redirects=True,
                proxy=HTTP_PROXY,
            ) as r:
                r.raise_for_status()
                cd  = r.headers.get("Content-Disposition", cd_head)
                ct  = r.headers.get("Content-Type",        ct_head)
                cl  = int(r.headers.get("Content-Length",  cl)) or cl

                if cl and cl > MAX_FILE_SIZE_MB * 1024 * 1024:
                    raise ValueError(
                        f"File too large: {cl/(1<<20):.0f} MB (limit {MAX_FILE_SIZE_MB} MB)"
                    )

                fname  = _get_filename(url, cd, ct, title)
                path   = _uniq(dest_dir, fname)
                done   = 0
                t_last = time.monotonic()

                async with aiofiles.open(path, "wb") as fh:
                    async for chunk in r.content.iter_chunked(CHUNK_SIZE):
                        await fh.write(chunk)
                        done += len(chunk)
                        _speed_tracker.record(len(chunk))
                        if cb:
                            cb(done, cl or done)

            cd_head = cd; ct_head = ct

        mime = _mime(path, ct_head)

        # PDF decrypt
        if mime == "application/pdf" or path.lower().endswith(".pdf"):
            dec = path + ".dec.pdf"
            if try_decrypt_pdf(path, dec):
                os.replace(dec, path)
            elif os.path.exists(dec):
                os.remove(dec)

        sz = os.path.getsize(path)
        logger.info(
            "Downloaded %s → %s  (%s)",
            url[:60], os.path.basename(path),
            _fmt_bytes(sz),
        )
        return path, os.path.basename(path), mime


def _fmt_bytes(n: int) -> str:
    if n < 1024:      return f"{n} B"
    if n < 1 << 20:   return f"{n/1024:.1f} KB"
    if n < 1 << 30:   return f"{n/(1<<20):.1f} MB"
    return f"{n/(1<<30):.2f} GB"


# ── Singleton ─────────────────────────────────────────────────────────────────
_DL: Optional[Downloader] = None

def get_downloader() -> Downloader:
    global _DL
    if _DL is None:
        _DL = Downloader()
    return _DL
