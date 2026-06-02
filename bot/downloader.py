"""Async HTTP downloader with DRM resolution, retry, speed tracking."""

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
    APPX_HEADERS, CHUNK_SIZE, DOWNLOAD_TIMEOUT,
    HTTP_PROXY, MAX_FILE_SIZE_MB, MAX_RETRIES,
    RETRY_DELAY, TEMP_DIR, YTDLP_COOKIES_FILE,
)
from .drm import DRMResolver, classify, download_stream, is_valid_url, try_decrypt_pdf

logger = logging.getLogger(__name__)
ProgressCB = Callable[[int, int], None]

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe(name: str, mx: int = 200) -> str:
    name = unquote(name)
    name = _UNSAFE.sub("_", name)
    return name.strip(". ")[:mx] or "file"


def _get_filename(url: str, cd: str = "", ct: str = "") -> str:
    if cd:
        m = re.search(r'filename\*?=["\']?(?:UTF-8\'\')?([^"\';\r\n]+)', cd, re.I)
        if m: return _safe(m.group(1).strip())
    base = os.path.basename(urlparse(url).path)
    if base and "." in base: return _safe(base)
    ext = ""
    if ct:
        raw = ct.split(";")[0].strip()
        ext = mimetypes.guess_extension(raw) or ""
        if ext == ".jpe": ext = ".jpg"
    return f"file_{int(time.time())}{ext}"


def _mime(path: str, ct: str = "") -> str:
    if ct: return ct.split(";")[0].strip()
    m, _ = mimetypes.guess_type(path)
    return m or "application/octet-stream"


def _uniq(d: str, name: str) -> str:
    p = os.path.join(d, name)
    if not os.path.exists(p): return p
    b, e = os.path.splitext(name)
    return os.path.join(d, f"{b}_{int(time.time())}{e}")


class Downloader:
    _session: Optional[aiohttp.ClientSession] = None

    async def _sess(self, cookie: str = "") -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            h = dict(APPX_HEADERS)
            if cookie: h["Cookie"] = cookie
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit=8, limit_per_host=4, ssl=False),
                headers=h,
                timeout=aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT, connect=30, sock_read=120),
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close(); self._session = None

    async def download(
        self, url: str,
        dest_dir: str     = TEMP_DIR,
        progress_cb: ProgressCB = None,
        job_id: str       = "",
        cookie: str       = "",
        drm_keys: Dict    = None,
    ) -> Tuple[str, str, str]:
        if not is_valid_url(url):
            raise ValueError(f"Invalid URL: {url[:80]}")

        os.makedirs(dest_dir, exist_ok=True)
        sess = await self._sess(cookie)
        resolver = DRMResolver(sess, cookie=cookie, drm_keys=drm_keys, proxy=HTTP_PROXY)
        resolved, headers, kind = await resolver.resolve(url)

        # ── stream path ────────────────────────────────────────────────────
        if kind in ("hls", "dash", "vimeo", "youtube", "jwp"):
            def _hook(d):
                if progress_cb and d.get("status") == "downloading":
                    try:
                        done = d.get("downloaded_bytes", 0)
                        tot  = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                        if tot: progress_cb(done, tot)
                    except Exception: pass

            stem = f"stream_{job_id or int(time.time())}"
            out  = os.path.join(dest_dir, f"{stem}.mp4")
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

        # ── HTTP download with retry ────────────────────────────────────────
        last: Exception = RuntimeError("never started")
        for att in range(1, MAX_RETRIES + 1):
            try:
                return await self._get(sess, resolved or url, headers, dest_dir, progress_cb)
            except aiohttp.ClientResponseError as e:
                if e.status in (401, 403) and att == 1:
                    from .drm import appx_decode_prefix, appx_strip_sig
                    for fb in filter(None, [appx_decode_prefix(url), appx_strip_sig(url)]):
                        try: return await self._get(sess, fb, headers, dest_dir, progress_cb)
                        except Exception: pass
                last = e
            except (aiohttp.ClientError, TimeoutError, OSError) as e:
                last = e
            logger.warning("Attempt %d/%d — %s: %s", att, MAX_RETRIES, type(last).__name__, url[:60])
            if att < MAX_RETRIES:
                import asyncio; await asyncio.sleep(RETRY_DELAY * att)
        raise last

    async def _get(self, sess: aiohttp.ClientSession, url: str,
                    headers: Dict, dest_dir: str,
                    cb: ProgressCB) -> Tuple[str, str, str]:
        async with sess.get(url, headers=headers, allow_redirects=True, proxy=HTTP_PROXY) as r:
            r.raise_for_status()
            cd = r.headers.get("Content-Disposition","")
            ct = r.headers.get("Content-Type","")
            cl = int(r.headers.get("Content-Length",0))

            if cl and cl > MAX_FILE_SIZE_MB * 1024 * 1024:
                raise ValueError(f"File too large: {cl/(1<<20):.0f} MB (limit {MAX_FILE_SIZE_MB} MB)")

            fname = _get_filename(url, cd, ct)
            path  = _uniq(dest_dir, fname)

            done = 0
            async with aiofiles.open(path, "wb") as fh:
                async for chunk in r.content.iter_chunked(CHUNK_SIZE):
                    await fh.write(chunk)
                    done += len(chunk)
                    if cb: cb(done, cl or done)

        mime = _mime(path, ct)
        # PDF decrypt attempt
        if mime == "application/pdf" or path.lower().endswith(".pdf"):
            dec = path + ".dec.pdf"
            if try_decrypt_pdf(path, dec):
                os.replace(dec, path)
            elif os.path.exists(dec):
                os.remove(dec)

        logger.info("Downloaded %s → %s (%d B)", url[:60], os.path.basename(path), os.path.getsize(path))
        return path, os.path.basename(path), mime


_DL: Optional[Downloader] = None

def get_downloader() -> Downloader:
    global _DL
    if _DL is None: _DL = Downloader()
    return _DL
