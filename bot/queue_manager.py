"""
Queue manager: orchestrates sequential link processing for a job.
"""

import os
import asyncio
import logging
import uuid
from typing import Dict, Optional, List

from database.db import Database
from .downloader import get_downloader
from .progress import JobProgress
from config.settings import TEMP_DIR, MAX_RETRIES

logger = logging.getLogger(__name__)

# job_id → asyncio.Task
_running_tasks: Dict[str, asyncio.Task] = {}
# job_id → cancel event
_cancel_events: Dict[str, asyncio.Event] = {}


def new_job_id() -> str:
    return str(uuid.uuid4())


async def start_job(
    bot,
    db: Database,
    user_id: int,
    chat_id: int,
    urls: List[str],
    start_index: int,
    progress_msg_id: int,
) -> str:
    job_id = new_job_id()
    await db.create_job(job_id, user_id, len(urls), start_index)
    await db.bulk_insert_links(job_id, urls)

    cancel_event = asyncio.Event()
    _cancel_events[job_id] = cancel_event

    task = asyncio.create_task(
        _process_job(bot, db, job_id, user_id, chat_id, urls, start_index,
                     progress_msg_id, cancel_event)
    )
    _running_tasks[job_id] = task
    logger.info("Job %s started for user %s (%d links, from index %d)",
                job_id, user_id, len(urls), start_index)
    return job_id


async def cancel_job(job_id: str, db: Database) -> bool:
    event = _cancel_events.get(job_id)
    if event:
        event.set()
        await db.pause_job(job_id)
        return True
    return False


async def resume_job(
    bot, db: Database, job_id: str, user_id: int,
    chat_id: int, urls: List[str], progress_msg_id: int
) -> bool:
    job = await db.get_job(job_id)
    if not job or job["status"] not in ("paused", "running"):
        return False

    current_idx = job["current_index"]
    await db.resume_job(job_id)

    cancel_event = asyncio.Event()
    _cancel_events[job_id] = cancel_event

    task = asyncio.create_task(
        _process_job(bot, db, job_id, user_id, chat_id, urls, current_idx,
                     progress_msg_id, cancel_event)
    )
    _running_tasks[job_id] = task
    return True


async def _process_job(
    bot,
    db: Database,
    job_id: str,
    user_id: int,
    chat_id: int,
    urls: List[str],
    start_index: int,
    progress_msg_id: int,
    cancel_event: asyncio.Event,
) -> None:
    downloader = get_downloader()
    progress = JobProgress(total=len(urls), start_index=start_index)
    progress.completed = start_index        # count already-done items from resume
    last_edit = 0.0
    EDIT_INTERVAL = 3.0   # seconds between Telegram message edits

    async def _edit_progress(filename: str = "") -> None:
        nonlocal last_edit
        now = asyncio.get_event_loop().time()
        if now - last_edit < EDIT_INTERVAL:
            return
        last_edit = now
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=progress_msg_id,
                text=progress.build_message(filename, job_id),
                parse_mode="HTML",
            )
        except Exception:
            pass

    job_temp_dir = os.path.join(TEMP_DIR, job_id)
    os.makedirs(job_temp_dir, exist_ok=True)

    for idx in range(start_index, len(urls)):
        if cancel_event.is_set():
            logger.info("Job %s cancelled at index %d", job_id, idx)
            await db.finish_job(job_id, "paused")
            break

        url = urls[idx].strip()
        progress.current = idx
        progress.phase = "downloading"
        await _edit_progress(url[:50])

        # Mark link as processing
        await db.update_link_status(job_id, idx + 1, "processing")

        local_path: Optional[str] = None
        filename = ""
        mime_type = "application/octet-stream"
        success = False
        error_msg = ""

        for attempt in range(1, MAX_RETRIES + 1):
            if cancel_event.is_set():
                break
            try:
                def _prog_cb(done: int, total: int) -> None:
                    progress.update_download(done, total)

                local_path, filename, mime_type = await downloader.download(
                    url,
                    dest_dir=job_temp_dir,
                    progress_cb=_prog_cb,
                    job_id=job_id,
                )
                success = True
                break
            except Exception as e:
                error_msg = str(e)
                logger.warning("Attempt %d/%d failed for %s: %s", attempt, MAX_RETRIES, url[:60], e)
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(3 * attempt)

        if not success or not local_path:
            await db.update_link_status(job_id, idx + 1, "failed", error_msg=error_msg)
            progress.finish_item(False)
            await db.update_job_progress(job_id, idx + 1, progress.completed, progress.failed)
            await db.add_log("ERROR", f"Link {idx + 1} failed: {error_msg}", user_id, job_id)
            await _edit_progress()
            continue

        # Upload to Telegram
        progress.phase = "uploading"
        await _edit_progress(filename)

        try:
            file_size = os.path.getsize(local_path)
            await _send_file(bot, chat_id, local_path, filename, mime_type, idx + 1, len(urls))
            await db.update_link_status(
                job_id, idx + 1, "completed",
                filename=filename, file_size=file_size, mime_type=mime_type
            )
            progress.finish_item(True)
            await db.add_log("INFO", f"Link {idx + 1} completed: {filename}", user_id, job_id)
        except Exception as e:
            error_msg = str(e)
            logger.error("Upload failed for %s: %s", filename, e)
            await db.update_link_status(job_id, idx + 1, "failed", error_msg=f"Upload: {error_msg}")
            progress.finish_item(False)
            await db.add_log("ERROR", f"Upload {idx + 1} failed: {error_msg}", user_id, job_id)
        finally:
            # Clean temp file
            try:
                if local_path and os.path.exists(local_path):
                    os.remove(local_path)
            except Exception:
                pass

        await db.update_job_progress(job_id, idx + 1, progress.completed, progress.failed)
        await _edit_progress()

    # Cleanup temp dir if empty
    try:
        if os.path.isdir(job_temp_dir) and not os.listdir(job_temp_dir):
            os.rmdir(job_temp_dir)
    except Exception:
        pass

    if not cancel_event.is_set():
        await db.finish_job(job_id, "completed")
        _running_tasks.pop(job_id, None)
        _cancel_events.pop(job_id, None)

        # Final summary
        report = (
            f"✅ <b>Job Complete!</b>\n\n"
            f"🔗 Total links   : {len(urls)}\n"
            f"✅ Completed     : {progress.completed}\n"
            f"❌ Failed        : {progress.failed}\n"
            f"🆔 Job ID        : <code>{job_id[:8]}</code>"
        )
        if progress.failed > 0:
            report += "\n\n⚠️ Use /logs to see details of failed links."

        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=progress_msg_id,
                text=report,
                parse_mode="HTML",
            )
        except Exception:
            try:
                await bot.send_message(chat_id=chat_id, text=report, parse_mode="HTML")
            except Exception:
                pass


async def _send_file(bot, chat_id: int, path: str, filename: str,
                      mime_type: str, idx: int, total: int) -> None:
    caption = f"📄 <b>{filename}</b>\n📦 File {idx}/{total}"
    mt = mime_type.lower()

    with open(path, "rb") as f:
        if mt.startswith("video/"):
            await bot.send_video(chat_id=chat_id, video=f, caption=caption,
                                  parse_mode="HTML", supports_streaming=True)
        elif mt.startswith("audio/"):
            await bot.send_audio(chat_id=chat_id, audio=f, caption=caption, parse_mode="HTML")
        elif mt.startswith("image/"):
            await bot.send_photo(chat_id=chat_id, photo=f, caption=caption, parse_mode="HTML")
        else:
            await bot.send_document(
                chat_id=chat_id,
                document=f,
                filename=filename,
                caption=caption,
                parse_mode="HTML",
            )
