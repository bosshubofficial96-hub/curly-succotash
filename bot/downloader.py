"""
Speed-optimised async HTTP downloader — v4.4

KEY CHANGE vs v4.3:
  _download_appx_v2() now tries plain HTTP with Bearer token FIRST.
  Plain HTTP is more reliable for direct CDN MKV files than yt-dlp's
  generic extractor. yt-dlp is still tried as a fallback for HLS streams.

  Root cause of v4.3 failure: yt-dlp generic extractor on a raw CDN URL
  (.../encrypted.mkv) fails because it tries to scrape the URL as a webpage,
  not as a direct video. Plain aiohttp GET with Authorization header is the
  correct approach for token-gated direct-download CDN files.
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


# ── Build auth headers for V2 from cookie string ──────────────────────────────
def _v2_auth_headers(cookie: str = "", extra_headers: Dict = None) -> Dict:
    """
    Build auth headers for AppX V2 CDN requests.
    Extracts Bearer token from cookie string and merges with APPX_HEADERS.
    """
    from .drm import extract_token
    h = dict(APPX_HEADERS)
    h["Referer"] = "https://appx.co.in/"
    h["Origin"]  = "https://appx.co.in"
    if cookie:
        h["Cookie"] = cookie
        token = extract_token(cookie)
        if token:
            h["Authorization"] = f"Bearer {token}"
    if extra_headers:
        # Carry over Authorization from resolved headers if already set
        for k, v in extra_headers.items():
            if k in ("Authorization", "Cookie"):
                h.setdefault(k, v)
    return h


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

        # ── AppX V2 encrypted video ───────────────────────────────────────────
        # Strategy: plain HTTP with Bearer token first (most reliable for direct
        # CDN MKV files), then yt-dlp as fallback (better for HLS streams).
        if kind == "appx_v2":
            return await self._download_appx_v2(
                resolved or url, headers, dest_dir, progress_cb, job_id, title,
                drm_keys=drm_keys, cookie=cookie,
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


    # ── AppX V2 download (plain HTTP first, yt-dlp fallback) ─────────────────
    async def _download_appx_v2(
        self,
        url:         str,
        headers:     Dict,
        dest_dir:    str,
        cb:          Optional[ProgressCB],
        job_id:      str,
        title:       str,
        drm_keys:    Dict = None,
        cookie:      str  = "",
    ) -> Tuple[str, str, str]:
        """
        Download AppX V2 encrypted video.

        STRATEGY ORDER (most → least reliable):
        1. Plain HTTP GET with Bearer token headers  ← NEW primary strategy
           Works because the CDN just needs the auth header; it returns the
           raw MKV/MP4 bytes. Simple, fast, no dependency on yt-dlp extractors.
        2. yt-dlp with Bearer headers                ← fallback for HLS streams
           Better when the URL is actually an HLS manifest (.m3u8) or when
           yt-dlp can resolve a better URL via its generic extractor.
        3. Deduplicated URL via plain HTTP           ← last resort

        Why plain HTTP is tried first:
        The CDN URL is a direct file link (not a streaming manifest). yt-dlp's
        generic extractor tries to parse it as a webpage, which fails. Plain
        aiohttp GET just downloads the bytes with auth headers — exactly what
        browsers do when they have the right cookie/auth.
        """
        sess = await self._sess()

        # Build auth headers — ensure Bearer token is present
        auth_headers = _v2_auth_headers(cookie, extra_headers=headers)
        stem = _safe(title) if title else f"v2_{job_id or int(time.time())}"

        # ── Strategy 1: Plain HTTP GET with Bearer token ──────────────────────
        logger.info("AppX V2: trying plain HTTP download for %s", url[:80])
        for att in range(1, 4):
            try:
                path, fname, mime = await self._http_get(
                    sess, url, auth_headers, dest_dir, cb, title=title,
                )
                path = post_process_download(path, mime, drm_keys=drm_keys)
                logger.info("AppX V2 ✅ plain HTTP [att %d]: %s (%s)",
                            att, fname, _fmt_bytes(os.path.getsize(path)))
                return path, fname, mime
            except aiohttp.ClientResponseError as e:
                logger.warning("AppX V2 HTTP att %d HTTP %s: %s", att, e.status, url[:70])
                if e.status in (401, 403):
                    # Auth failed — no point retrying with same token
                    logger.warning("AppX V2: Bearer token rejected (HTTP %s). "
                                   "Cookie may be expired.", e.status)
                    break
                if att < 3:
                    await asyncio.sleep(RETRY_DELAY * att)
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
                logger.warning("AppX V2 HTTP att %d %s: %s",
                               att, type(e).__name__, e)
                if att < 3:
                    await asyncio.sleep(RETRY_DELAY * att)

        # ── Strategy 2: yt-dlp (handles HLS manifests + AES-128 decrypt) ─────
        logger.info("AppX V2: trying yt-dlp for %s", url[:80])
        out = _uniq(dest_dir, f"{stem}.mp4")

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
            url, out, auth_headers,
            cookies_file=cookies_file,
            drm_keys=drm_keys,
            proxy=HTTP_PROXY,
            progress_hook=_hook,
            allow_unplayable=True,
        )
        if ok and os.path.exists(out) and os.path.getsize(out) > 0:
            path = post_process_download(out, "video/mp4", drm_keys=drm_keys)
            logger.info("AppX V2 ✅ yt-dlp: %s", os.path.basename(path))
            return path, os.path.basename(path), "video/mp4"

        # ── Strategy 3: Deduplicated URL via plain HTTP ───────────────────────
        from .drm import appx_dedup_path
        deduped = appx_dedup_path(url)
        if deduped and deduped != url:
            logger.info("AppX V2: trying deduped URL %s", deduped[:80])
            try:
                path, fname, mime = await self._http_get(
                    sess, deduped, auth_headers, dest_dir, cb, title=title,
                )
                path = post_process_download(path, mime, drm_keys=drm_keys)
                logger.info("AppX V2 ✅ deduped HTTP: %s", fname)
                return path, fname, mime
            except Exception as e:
                logger.warning("AppX V2 deduped HTTP: %s", e)

        raise RuntimeError(
            f"AppX V2 download failed — all strategies exhausted.\n"
            f"URL: {url[:120]}\n"
            f"Hint: your AppX cookie/token may be expired. "
            f"Use /cookie to set a fresh token."
        )


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
