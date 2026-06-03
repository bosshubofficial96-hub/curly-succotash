"""
Job queue engine — v3.2 FIXED.

Key fix: progress percentage now updates live during downloads.
A lightweight background periodic-edit task fires every 3 s while the
download is running, so the user sees the progress bar move in real time.
"""

import asyncio
import logging
import os
import uuid
from typing import Dict, List, Optional, Tuple

from config.settings import MAX_RETRIES, TEMP_DIR
from database.db import Database
from .downloader import get_downloader
from .drm import merged_drm_keys
from .keyboards import failed_kb, job_controls
from .progress import JobProgress

logger = logging.getLogger(__name__)

_tasks:   Dict[str, asyncio.Task]  = {}
_pauses:  Dict[str, asyncio.Event] = {}
_cancels: Dict[str, asyncio.Event] = {}

PROGRESS_EDIT_INTERVAL = 3   # seconds between Telegram message edits


def new_jid() -> str:
    return str(uuid.uuid4())

def is_running(jid: str) -> bool:
    t = _tasks.get(jid)
    return t is not None and not t.done()


async def start_job(
    bot, db: Database, uid: int, chat_id: int,
    entries: List[Tuple[str, str]],   # [(url, title), …]
    start: int, pmid: int,
    source_name: str = "",
) -> str:
    jid  = new_jid()
    urls = [e[0] for e in entries]
    await db.create_job(jid, uid, chat_id, len(urls), start, pmid, source_name)
    await db.bulk_insert_links(jid, urls)

    pev = asyncio.Event()
    cev = asyncio.Event()
    _pauses[jid]  = pev
    _cancels[jid] = cev

    t = asyncio.create_task(
        _run(bot, db, jid, uid, chat_id, entries, start, pmid, pev, cev),
        name=f"job-{jid[:8]}",
    )
    _tasks[jid] = t
    logger.info("Job %s started: %d links from idx %d", jid[:8], len(urls), start)
    return jid


async def pause_job(jid: str) -> bool:
    ev = _pauses.get(jid)
    if ev and not ev.is_set():
        ev.set()
        return True
    return False

async def resume_in_place(jid: str) -> bool:
    ev = _pauses.get(jid)
    if ev and ev.is_set():
        ev.clear()
        return True
    return False

async def cancel_job(jid: str, db: Database) -> bool:
    cev = _cancels.get(jid)
    if cev:
        cev.set()
        pev = _pauses.get(jid)
        if pev:
            pev.clear()
        return True
    return False

async def resume_from_db(
    bot, db: Database, jid: str, uid: int,
    chat_id: int, entries: List[Tuple[str, str]], pmid: int,
) -> bool:
    job = await db.get_job(jid)
    if not job or job["status"] not in ("paused", "running"):
        return False
    old = _tasks.get(jid)
    if old and not old.done():
        old.cancel()
        try:
            await old
        except Exception:
            pass

    pev = asyncio.Event()
    cev = asyncio.Event()
    _pauses[jid]  = pev
    _cancels[jid] = cev
    await db.set_job_status(jid, "running")

    t = asyncio.create_task(
        _run(bot, db, jid, uid, chat_id, entries, job["current_index"], pmid, pev, cev),
        name=f"job-{jid[:8]}-rsm",
    )
    _tasks[jid] = t
    return True


# ── Core job loop ─────────────────────────────────────────────────────────────
async def _run(
    bot, db: Database, jid: str, uid: int, chat_id: int,
    entries: List[Tuple[str, str]],
    start: int, pmid: int,
    pev: asyncio.Event, cev: asyncio.Event,
) -> None:
    dl       = get_downloader()
    prog     = JobProgress(total=len(entries), start=start)
    prog.completed = start

    job_tmp  = os.path.join(TEMP_DIR, jid)
    os.makedirs(job_tmp, exist_ok=True)

    cookie   = await db.get_active_cookie() or ""
    drm_keys = await merged_drm_keys(db)

    # ── Telegram message edit helper ─────────────────────────────────────
    _last_edit_text = [""]   # mutable cell to detect duplicate edits

    async def _edit(extra: str = "") -> None:
        text = prog.render(jid) + (f"\n\n{extra}" if extra else "")
        if text == _last_edit_text[0]:
            return                              # skip identical edits
        _last_edit_text[0] = text
        try:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=pmid,
                text=text,
                parse_mode="HTML",
                reply_markup=job_controls(jid, paused=pev.is_set()),
            )
        except Exception:
            pass

    # ── Per-file download loop ────────────────────────────────────────────
    for idx in range(start, len(entries)):
        # Pause checkpoint
        if pev.is_set():
            await db.set_job_status(jid, "paused")
            await db.update_job(jid, idx, prog.completed, prog.failed)
            try:
                await bot.edit_message_text(
                    chat_id=chat_id, message_id=pmid,
                    text=prog.render(jid) + "\n\n⏸ <b>Paused.</b> Tap ▶️ Resume.",
                    parse_mode="HTML",
                    reply_markup=job_controls(jid, paused=True),
                )
            except Exception:
                pass
            while pev.is_set() and not cev.is_set():
                await asyncio.sleep(1)
            if cev.is_set():
                break
            await db.set_job_status(jid, "running")

        if cev.is_set():
            break

        url, title = entries[idx]
        url        = url.strip()
        prog.current  = idx
        prog.filename = (title or os.path.basename(url.split("?")[0]))[:50]
        prog.phase    = "downloading"
        prog.dl_done  = 0
        prog.dl_total = 0

        await db.set_link(jid, idx + 1, "processing")
        await _edit()

        path:  Optional[str] = None
        fname = mime = ""
        ok    = False
        errmsg = ""

        # ── Live progress updater (fires every 3 s) ───────────────────────
        progress_edit_running = True

        async def _periodic_edit():
            while progress_edit_running:
                await asyncio.sleep(PROGRESS_EDIT_INTERVAL)
                if not progress_edit_running:
                    break
                await _edit()

        progress_task = asyncio.create_task(_periodic_edit())

        try:
            for att in range(1, MAX_RETRIES + 1):
                if cev.is_set():
                    break
                try:
                    prog.on_retry(att)

                    # Sync callback — just update prog state; _periodic_edit pushes it
                    def _cb(done: int, total: int) -> None:
                        prog.on_download(done, total)

                    path, fname, mime = await dl.download(
                        url,
                        dest_dir=job_tmp,
                        progress_cb=_cb,
                        job_id=jid,
                        cookie=cookie,
                        drm_keys=drm_keys,
                        title=title,
                    )
                    prog.filename = fname
                    ok = True
                    break
                except Exception as e:
                    errmsg = str(e)[:200]
                    logger.warning(
                        "[%s] att %d/%d — %s: %s",
                        jid[:8], att, MAX_RETRIES, url[:60], e,
                    )
                    if att < MAX_RETRIES:
                        await asyncio.sleep(min(3 * att, 30))
        finally:
            progress_edit_running = False
            progress_task.cancel()
            try:
                await progress_task
            except asyncio.CancelledError:
                pass

        if not ok:
            await db.set_link(jid, idx + 1, "failed", err=errmsg)
            await db.add_log("ERROR", f"Link {idx+1} failed: {errmsg}", uid, jid)
            prog.on_done(False)
            await db.update_job(jid, idx + 1, prog.completed, prog.failed)
            await _edit()
            continue

        # ── Upload to Telegram ────────────────────────────────────────────
        prog.on_upload()
        await _edit()

        up_ok = False
        try:
            sz = os.path.getsize(path)
            await _send(bot, chat_id, path, fname, mime, title, idx + 1, len(entries))
            await db.set_link(jid, idx + 1, "completed", fn=fname, sz=sz, mime=mime)
            await db.inc_stats(uid, files=1, b=sz)
            await db.add_log("INFO", f"Link {idx+1} OK: {fname}", uid, jid)
            up_ok = True
        except Exception as e:
            uperr = str(e)[:200]
            logger.error("Upload %s: %s", fname, e)
            await db.set_link(jid, idx + 1, "failed", err=f"Upload: {uperr}")
            await db.add_log("ERROR", f"Upload {idx+1} failed: {uperr}", uid, jid)
        finally:
            _rm(path)

        prog.on_done(up_ok)
        await db.update_job(jid, idx + 1, prog.completed, prog.failed)
        await _edit()

    # ── Final summary ─────────────────────────────────────────────────────
    _rmdir(job_tmp)
    cancelled = cev.is_set()

    if cancelled:
        fstatus = "cancelled"
        ftxt = (
            f"⏹ <b>Job Cancelled</b>\n\n"
            f"✅ Completed : <b>{prog.completed}</b>\n"
            f"❌ Failed    : <b>{prog.failed}</b>\n"
            f"⏭ Remaining : <b>{len(entries) - prog.current}</b>\n"
            f"🆔 <code>{jid[:8]}</code>"
        )
    else:
        fstatus = "completed"
        ftxt = (
            f"🎉 <b>Job Complete!</b>\n\n"
            f"🔗 Total    : <b>{len(entries)}</b>\n"
            f"✅ Uploaded : <b>{prog.completed}</b>\n"
            f"❌ Failed   : <b>{prog.failed}</b>\n"
            f"🆔 <code>{jid[:8]}</code>"
        )

    await db.set_job_status(jid, fstatus)

    # Notify user if enabled
    user = await db.get_user(uid)
    if user and user.get("notify_done") and fstatus == "completed":
        try:
            await bot.send_message(
                chat_id=uid,
                text=(f"🔔 Job <code>{jid[:8]}</code> done! "
                      f"✅ {prog.completed} file(s) sent."),
                parse_mode="HTML",
            )
        except Exception:
            pass

    kb = failed_kb(jid) if prog.failed else None
    try:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=pmid,
            text=ftxt, parse_mode="HTML", reply_markup=kb,
        )
    except Exception:
        try:
            await bot.send_message(
                chat_id=chat_id, text=ftxt,
                parse_mode="HTML", reply_markup=kb,
            )
        except Exception:
            pass

    _tasks.pop(jid, None)
    _pauses.pop(jid, None)
    _cancels.pop(jid, None)


# ── Send file to Telegram ─────────────────────────────────────────────────────
async def _send(
    bot, chat_id: int, path: str, fname: str,
    mime: str, title: str, idx: int, total: int,
) -> None:
    display = title or fname
    cap = f"📄 <b>{display[:200]}</b>\n📦 File {idx}/{total}"
    mt  = mime.lower()
    with open(path, "rb") as fh:
        if mt.startswith("video/"):
            await bot.send_video(
                chat_id=chat_id, video=fh, caption=cap,
                parse_mode="HTML", supports_streaming=True,
            )
        elif mt.startswith("audio/"):
            await bot.send_audio(
                chat_id=chat_id, audio=fh, caption=cap, parse_mode="HTML",
            )
        elif mt.startswith("image/"):
            await bot.send_photo(
                chat_id=chat_id, photo=fh, caption=cap, parse_mode="HTML",
            )
        else:
            await bot.send_document(
                chat_id=chat_id, document=fh,
                filename=fname, caption=cap, parse_mode="HTML",
            )


def _rm(p: Optional[str]) -> None:
    try:
        if p and os.path.exists(p):
            os.remove(p)
    except Exception:
        pass

def _rmdir(p: str) -> None:
    try:
        if os.path.isdir(p) and not os.listdir(p):
            os.rmdir(p)
    except Exception:
        pass
