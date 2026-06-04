"""
Speed-optimised async HTTP downloader — v4.3

KEY CHANGE vs v3:
  AppX V2 video (kind="appx_v2") now routes through yt-dlp with Bearer-token
  headers BEFORE attempting plain HTTP GET.  This is the only reliable method
  because static-trans-v2.appx.co.in requires Authorization on every request,
  and yt-dlp handles auth, redirects, and AES-128 HLS decryption automatically.

Standard AppX (PDF/media, kind="appx") still uses plain HTTP GET after the
12-strategy URL bypass resolves a usable URL.
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
from .drm import DRMResolver, classify, download_stream, is_valid_url
from .decrypt import post_process_download

logger = logging.getLogger(__name__)

ProgressCB = Callable[[int, int], None]

_UNSAFE             = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_PARALLEL_THRESHOLD = 50 * 1024 * 1024   # 50 MB
_RANGE_WORKERS      = 4


# ── Speed tracker ──────────────────────────────────────────────────────────────
class SpeedTracker:
    def __init__(self):
        self._samples: list = []
        self._window  = 5.0

    def record(self, n_bytes: int) -> None:
        now = time.monotonic()
        self._samples.append((now, n_bytes))
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
        if b < 1024:     return f"{b:.0f} B/s"
        if b < 1 << 20:  return f"{b/1024:.1f} KB/s"
        return f"{b/(1<<20):.1f} MB/s"


_speed_tracker = SpeedTracker()

def get_speed_tracker() -> SpeedTracker:
    return _speed_tracker


# ── Filename helpers ───────────────────────────────────────────────────────────
def _safe(name: str, mx: int = 200) -> str:
    name = unquote(name)
    name = _UNSAFE.sub("_", name)
    return name.strip(". ")[:mx] or "file"

def _get_filename(url: str, cd: str = "", ct: str = "", title: str = "") -> str:
    if cd:
        m = re.search(r'filename\*?=["\']?(?:UTF-8\'\')?([^"\';\r\n]+)', cd, re.I)
        if m:
            return _safe(m.group(1).strip())
    base = os.path.basename(urlparse(url).path.split("?")[0])
    if base and "." in base:
        stem, ext = os.path.splitext(_safe(base))
        return (_safe(title)[:180] + ext) if title else _safe(base)
    ext = ""
    if ct:
        raw = ct.split(";")[0].strip()
        ext = mimetypes.guess_extension(raw) or ""
        if ext == ".jpe": ext = ".jpg"
    stem = _safe(title) if title else f"file_{int(time.time())}"
    return stem[:180] + ext

def _mime(path: str, ct: str = "") -> str:
    if ct: return ct.split(";")[0].strip()
    m, _ = mimetypes.guess_type(path)
    return m or "application/octet-stream"

def _uniq(d: str, name: str) -> str:
    p = os.path.join(d, name)
    if not os.path.exists(p): return p
    b, e = os.path.splitext(name)
    return os.path.join(d, f"{b}_{int(time.time())}{e}")


# ── Parallel range download ────────────────────────────────────────────────────
async def _parallel_download(
    session: aiohttp.ClientSession, url: str, headers: Dict,
    path: str, total: int, cb: Optional[ProgressCB], proxy: str = None,
) -> None:
    part_size  = total // _RANGE_WORKERS + 1
    parts      = []
    done_lock  = asyncio.Lock()
    done_bytes = [0]

    async def _fetch_part(start: int, end: int, part_path: str) -> None:
        h = {**headers, "Range": f"bytes={start}-{end}"}
        async with session.get(url, headers=h, allow_redirects=True,
                               proxy=proxy,
                               timeout=aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT)) as r:
            async with aiofiles.open(part_path, "wb") as fh:
                async for chunk in r.content.iter_chunked(CHUNK_SIZE):
                    await fh.write(chunk)
                    _speed_tracker.record(len(chunk))
                    async with done_lock:
                        done_bytes[0] += len(chunk)
                        if cb: cb(done_bytes[0], total)

    tasks = []
    for i in range(_RANGE_WORKERS):
        start = i * part_size
        end   = min(start + part_size - 1, total - 1)
        pp    = f"{path}.part{i}"
        parts.append(pp)
        tasks.append(asyncio.create_task(_fetch_part(start, end, pp)))
    await asyncio.gather(*tasks)

    async with aiofiles.open(path, "wb") as out:
        for pp in parts:
            async with aiofiles.open(pp, "rb") as inp:
                await out.write(await inp.read())
            os.remove(pp)


# ── Main Downloader ────────────────────────────────────────────────────────────
class Downloader:
    _session: Optional[aiohttp.ClientSession] = None

    async def _sess(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(
                    limit=MAX_CONNECTIONS, limit_per_host=MAX_CONNECTIONS_PER_HOST,
                    ssl=False, enable_cleanup_closed=True, keepalive_timeout=60,
                ),
                headers=APPX_HEADERS,
                timeout=aiohttp.ClientTimeout(
                    total=DOWNLOAD_TIMEOUT, connect=CONNECT_TIMEOUT, sock_read=READ_TIMEOUT,
                ),
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def resolve_url(
        self, url: str, cookie: str = "", drm_keys: Dict = None,
    ) -> Tuple[str, Dict, str]:
        """Resolve/bypass a URL without downloading. Returns (url, headers, kind)."""
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
        """Returns (local_path, filename, mime_type). Raises on failure."""
        if not is_valid_url(url):
            raise ValueError(f"Invalid/blocked URL: {url[:80]}")

        os.makedirs(dest_dir, exist_ok=True)
        sess     = await self._sess()
        resolver = DRMResolver(sess, cookie=cookie, drm_keys=drm_keys, proxy=HTTP_PROXY)
        resolved, headers, kind = await resolver.resolve(url)

        # ── AppX V2 encrypted video → yt-dlp with auth headers ───────────────
        # This is the ONLY reliable download method for static-trans-v2.appx.co.in.
        # yt-dlp passes headers on every fragment request, handles HLS AES-128
        # decryption automatically, and follows auth-gated redirects correctly.
        if kind == "appx_v2":
            return await self._download_appx_v2(
                resolved or url, headers, dest_dir, progress_cb, job_id, title,
                drm_keys=drm_keys,
            )

        # ── HLS / DASH / streaming ────────────────────────────────────────────
        if kind in ("hls", "dash", "vimeo", "youtube", "jwp"):
            return await self._download_stream_kind(
                resolved or url, headers, dest_dir, progress_cb, job_id, title,
                drm_keys=drm_keys,
            )

        # ── Standard AppX PDF / generic HTTP ─────────────────────────────────
        last_err: Exception = RuntimeError("download never started")
        for att in range(1, MAX_RETRIES + 1):
            try:
                path, fname, mime = await self._http_get(
                    sess, resolved or url, headers, dest_dir, progress_cb, title=title,
                )
                # Post-process: PDF decrypt, DRM decrypt if needed
                path = post_process_download(
                    path, mime, drm_keys=drm_keys,
                    content_id=job_id,
                )
                return path, fname, mime
            except aiohttp.ClientResponseError as e:
                if e.status in (401, 403) and att == 1:
                    new_url, new_h, _ = await resolver.resolve(url)
                    if new_url and new_url != (resolved or url):
                        try:
                            path, fname, mime = await self._http_get(
                                sess, new_url, new_h, dest_dir, progress_cb, title=title,
                            )
                            path = post_process_download(path, mime, drm_keys=drm_keys)
                            return path, fname, mime
                        except Exception:
                            pass
                last_err = e
                logger.warning("[att %d/%d] HTTP %s → %s", att, MAX_RETRIES, e.status, url[:70])
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
                last_err = e
                logger.warning("[att %d/%d] %s → %s", att, MAX_RETRIES, type(e).__name__, url[:70])
            if att < MAX_RETRIES:
                await asyncio.sleep(min(RETRY_DELAY * att, 30))
        raise last_err

    # ── AppX V2 download (yt-dlp) ─────────────────────────────────────────────
    async def _download_appx_v2(
        self,
        url:         str,
        headers:     Dict,
        dest_dir:    str,
        cb:          Optional[ProgressCB],
        job_id:      str,
        title:       str,
        drm_keys:    Dict = None,
    ) -> Tuple[str, str, str]:
        """
        Download AppX V2 encrypted video via yt-dlp.

        yt-dlp sends Bearer token on every fragment/segment request,
        handles AES-128 HLS decryption, and remuxes to MP4.
        Falls back to plain HTTP if yt-dlp fails (in case content is not
        actually encrypted, just auth-gated).
        """
        stem = _safe(title) if title else f"v2_{job_id or int(time.time())}"
        out  = _uniq(dest_dir, f"{stem}.mp4")

        def _hook(d):
            if cb and d.get("status") == "downloading":
                try:
                    done = d.get("downloaded_bytes", 0)
                    tot  = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                    if tot: cb(done, tot)
                    _speed_tracker.record(d.get("speed", 0) or 0)
                except Exception:
                    pass

        cookies_file = YTDLP_COOKIES_FILE if os.path.isfile(YTDLP_COOKIES_FILE) else None

        ok = await download_stream(
            url, out, headers,
            cookies_file=cookies_file,
            drm_keys=drm_keys,
            proxy=HTTP_PROXY,
            progress_hook=_hook,
            allow_unplayable=True,    # allow encrypted containers
        )
        if ok and os.path.exists(out) and os.path.getsize(out) > 0:
            path = post_process_download(out, "video/mp4", drm_keys=drm_keys)
            return path, os.path.basename(path), "video/mp4"

        # yt-dlp failed — fall back to plain HTTP GET with auth headers
        logger.warning("AppX V2 yt-dlp failed, trying plain HTTP for %s", url[:70])
        sess = await self._sess()
        for att in range(1, 4):
            try:
                path, fname, mime = await self._http_get(
                    sess, url, headers, dest_dir, cb, title=title,
                )
                path = post_process_download(path, mime, drm_keys=drm_keys)
                return path, fname, mime
            except Exception as e:
                logger.warning("V2 HTTP fallback att %d: %s", att, e)
                if att < 3: await asyncio.sleep(RETRY_DELAY * att)

        raise RuntimeError(f"AppX V2 download failed (yt-dlp + HTTP): {url[:80]}")

    # ── Generic stream download (HLS/DASH/etc.) ───────────────────────────────
    async def _download_stream_kind(
        self,
        url:      str,
        headers:  Dict,
        dest_dir: str,
        cb:       Optional[ProgressCB],
        job_id:   str,
        title:    str,
        drm_keys: Dict = None,
    ) -> Tuple[str, str, str]:
        def _hook(d):
            if cb and d.get("status") == "downloading":
                try:
                    done = d.get("downloaded_bytes", 0)
                    tot  = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                    if tot: cb(done, tot)
                    _speed_tracker.record(d.get("speed", 0) or 0)
                except Exception:
                    pass

        stem = _safe(title) if title else f"stream_{job_id or int(time.time())}"
        out  = _uniq(dest_dir, f"{stem}.mp4")
        cookies_file = YTDLP_COOKIES_FILE if os.path.isfile(YTDLP_COOKIES_FILE) else None

        ok = await download_stream(
            url, out, headers,
            cookies_file=cookies_file,
            drm_keys=drm_keys,
            proxy=HTTP_PROXY,
            progress_hook=_hook,
        )
        if ok and os.path.exists(out):
            return out, os.path.basename(out), "video/mp4"
        raise RuntimeError(f"Stream download failed: {url[:80]}")

    # ── Plain HTTP download ───────────────────────────────────────────────────
    async def _http_get(
        self,
        sess:     aiohttp.ClientSession,
        url:      str,
        headers:  Dict,
        dest_dir: str,
        cb:       Optional[ProgressCB],
        title:    str = "",
    ) -> Tuple[str, str, str]:
        supports_range = False
        cl = 0
        cd_head = ct_head = ""
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
            pass

        if cl and cl > MAX_FILE_SIZE_MB * 1024 * 1024:
            raise ValueError(f"File too large: {cl/(1<<20):.0f} MB (limit {MAX_FILE_SIZE_MB} MB)")

        if supports_range and cl >= _PARALLEL_THRESHOLD:
            fname = _get_filename(url, cd_head, ct_head, title)
            path  = _uniq(dest_dir, fname)
            await _parallel_download(sess, url, headers, path, cl, cb, HTTP_PROXY)
        else:
            async with sess.get(url, headers=headers, allow_redirects=True, proxy=HTTP_PROXY) as r:
                r.raise_for_status()
                cd  = r.headers.get("Content-Disposition", cd_head)
                ct  = r.headers.get("Content-Type",        ct_head)
                cl  = int(r.headers.get("Content-Length",  cl)) or cl

                if cl and cl > MAX_FILE_SIZE_MB * 1024 * 1024:
                    raise ValueError(f"File too large: {cl/(1<<20):.0f} MB")

                fname = _get_filename(url, cd, ct, title)
                path  = _uniq(dest_dir, fname)
                done  = 0

                async with aiofiles.open(path, "wb") as fh:
                    async for chunk in r.content.iter_chunked(CHUNK_SIZE):
                        await fh.write(chunk)
                        done += len(chunk)
                        _speed_tracker.record(len(chunk))
                        if cb: cb(done, cl or done)

                cd_head, ct_head = cd, ct

        mime = _mime(path, ct_head)
        sz   = os.path.getsize(path)
        logger.info("Downloaded %s → %s (%s)", url[:60], os.path.basename(path), _fmt_bytes(sz))
        return path, os.path.basename(path), mime


def _fmt_bytes(n: int) -> str:
    if n < 1024:     return f"{n} B"
    if n < 1 << 20:  return f"{n/1024:.1f} KB"
    if n < 1 << 30:  return f"{n/(1<<20):.1f} MB"
    return f"{n/(1<<30):.2f} GB"


_DL: Optional[Downloader] = None

def get_downloader() -> Downloader:
    global _DL
    if _DL is None:
        _DL = Downloader()
    return _DL
