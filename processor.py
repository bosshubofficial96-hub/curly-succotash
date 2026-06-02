"""
processor.py - Job queue processor that manages sequential URL processing.
Handles downloads, uploads to Telegram or Google Drive, retries, checkpoints,
and sends real-time progress updates to the user.
"""

import asyncio
import time
from typing import Dict, Optional, Any, List
from pathlib import Path

from telegram import Bot, InputFile
from telegram.error import TelegramError

from config import Config
from database import Database
from downloader import Downloader, DownloadError
from gdrive import GoogleDrive
from utils import format_file_size, estimate_remaining_time


class JobProcessor:
    def __init__(self, db: Database, downloader: Downloader, logger):
        self.db = db
        self.downloader = downloader
        self.logger = logger
        self.gdrive: Optional[GoogleDrive] = None  # set later if enabled
        self.active_jobs: Dict[int, Dict] = {}  # user_id -> job info
        self.user_upload_mode: Dict[int, str] = {}  # "telegram" or "gdrive"
        self.running = True
        self.current_item: Optional[Dict] = None
        self.progress_messages: Dict[int, int] = {}  # user_id -> last update time

    def set_gdrive(self, gdrive: GoogleDrive):
        self.gdrive = gdrive

    def set_upload_mode(self, user_id: int, mode: str):
        self.user_upload_mode[user_id] = mode

    async def start_job(self, user_id: int, job_id: int, start_position: int):
        """Start or resume a job for a user."""
        if user_id in self.active_jobs:
            await self.stop_user_processing(user_id)

        self.active_jobs[user_id] = {
            "job_id": job_id,
            "start_pos": start_position,
            "cancelled": False,
            "upload_mode": self.user_upload_mode.get(user_id, "telegram")
        }
        # Clear any existing checkpoint for this user if we're starting fresh
        # (resume will be handled separately)
        self.logger.info(f"Started job {job_id} for user {user_id} from position {start_position}")

    async def resume_job(self, user_id: int, job_id: int, last_completed_position: int):
        """Resume a job from last completed position."""
        if user_id in self.active_jobs:
            await self.stop_user_processing(user_id)

        self.active_jobs[user_id] = {
            "job_id": job_id,
            "start_pos": last_completed_position + 1,  # next to process
            "cancelled": False,
            "upload_mode": self.user_upload_mode.get(user_id, "telegram")
        }
        self.logger.info(f"Resumed job {job_id} for user {user_id} from position {last_completed_position + 1}")

    async def cancel_job(self, user_id: int):
        """Cancel active job for a user."""
        if user_id in self.active_jobs:
            self.active_jobs[user_id]["cancelled"] = True
            self.logger.info(f"Cancelled job for user {user_id}")
            await self.db.update_job_status(self.active_jobs[user_id]["job_id"], "cancelled")
            await self.db.clear_checkpoint(user_id)
            del self.active_jobs[user_id]

    async def stop_user_processing(self, user_id: int):
        """Stop processing for a user (can be resumed later)."""
        if user_id in self.active_jobs:
            # Save checkpoint before stopping
            job_id = self.active_jobs[user_id]["job_id"]
            if self.current_item and self.current_item.get("job_id") == job_id:
                last_pos = self.current_item.get("position", 0) - 1
                await self.db.save_checkpoint(user_id, job_id, last_pos)
            self.active_jobs[user_id]["cancelled"] = True
            del self.active_jobs[user_id]

    def get_active_job(self, user_id: int) -> Optional[Dict]:
        """Return active job info for a user."""
        return self.active_jobs.get(user_id)

    async def run(self):
        """Main loop: process jobs sequentially for all users."""
        self.running = True
        while self.running:
            # Process each user's job sequentially (one URL at a time globally)
            for user_id, job_info in list(self.active_jobs.items()):
                if job_info.get("cancelled"):
                    continue

                await self._process_next_url(user_id, job_info)

            await asyncio.sleep(Config.PROCESSOR_SLEEP_INTERVAL)

    async def _process_next_url(self, user_id: int, job_info: Dict):
        """Process the next pending URL for a given job."""
        job_id = job_info["job_id"]
        upload_mode = job_info["upload_mode"]

        # Get next pending item from DB
        next_item = await self.db.get_next_pending_item(job_id)
        if not next_item:
            # No more items – job complete
            await self.db.update_job_status(job_id, "completed")
            await self.db.clear_checkpoint(user_id)
            del self.active_jobs[user_id]
            await self._send_completion_report(user_id, job_id)
            return

        # Mark as processing
        self.current_item = next_item
        item_id = next_item["item_id"]
        url = next_item["url"]
        position = next_item["position"]

        await self.db.update_item_status(item_id, "downloading")
        await self._send_progress(user_id, job_id, position, url, "download", 0)

        # Download the file
        retries = 0
        success = False
        last_error = None
        temp_path = None

        while retries < Config.MAX_RETRIES and not success and not job_info.get("cancelled"):
            try:
                start_time = time.time()
                # Download with progress callback
                def progress_callback(current, total):
                    asyncio.create_task(self._send_download_progress(
                        user_id, job_id, position, current, total
                    ))

                temp_path, mime_type, file_size = await self.downloader.download(
                    url, user_id, job_id, progress_callback
                )
                if file_size > Config.MAX_FILE_SIZE_BYTES and upload_mode == "telegram":
                    # File too large for Telegram, fallback to GDrive if available
                    if self.gdrive:
                        upload_mode = "gdrive"
                        await self._notify_user(user_id, f"📦 File >50MB, switching to Google Drive for: {temp_path.name}")
                    else:
                        raise DownloadError(f"File too large ({format_file_size(file_size)}) and Google Drive not available")

                # Upload based on selected mode
                await self._upload_file(user_id, job_id, item_id, temp_path, mime_type, file_size, upload_mode, position)

                # Mark as completed
                await self.db.update_item_status(item_id, "completed",
                                                 local_file_path=str(temp_path),
                                                 file_size=file_size, mime_type=mime_type)
                await self.db.add_log(user_id, job_id, item_id, "info", f"Downloaded and uploaded: {url}")
                success = True

                # Cleanup temp file
                if temp_path and temp_path.exists():
                    temp_path.unlink()

                # Save checkpoint (last completed position)
                await self.db.save_checkpoint(user_id, job_id, position)

                # Send completion message
                await self._send_progress(user_id, job_id, position, url, "complete", 100,
                                          completed=position, failed=0)

            except (DownloadError, TelegramError, Exception) as e:
                last_error = str(e)
                self.logger.error(f"Error processing {url}: {last_error}")
                retries += 1
                await self.db.increment_retry(item_id)
                await self.db.add_log(user_id, job_id, item_id, "error", f"Retry {retries}: {last_error}")
                if retries < Config.MAX_RETRIES:
                    await asyncio.sleep(Config.RETRY_BACKOFF_FACTOR ** retries)
                    await self._notify_user(user_id, f"⚠️ Retrying ({retries}/{Config.MAX_RETRIES}): {url[:80]}")
                else:
                    # Mark as failed
                    await self.db.update_item_status(item_id, "failed", error_message=last_error)
                    await self._send_progress(user_id, job_id, position, url, "failed", completed=position, failed=1)

        self.current_item = None

    async def _upload_file(self, user_id: int, job_id: int, item_id: int,
                           file_path: Path, mime_type: str, file_size: int,
                           upload_mode: str, position: int):
        """Upload file either to Telegram or Google Drive."""
        if upload_mode == "gdrive" and self.gdrive:
            await self._send_progress(user_id, job_id, position, file_path.name, "upload", 0)
            file_id, share_link = await self.gdrive.upload_file(file_path, folder_id=Config.GOOGLE_DRIVE_FOLDER_ID)
            if share_link:
                await self._send_file_to_user(user_id, share_link, is_gdrive=True, file_name=file_path.name)
                self.logger.info(f"Uploaded to GDrive: {share_link}")
            else:
                raise Exception("Google Drive upload failed")
        else:
            # Upload to Telegram
            await self._send_progress(user_id, job_id, position, file_path.name, "upload", 0)
            await self._send_file_to_user(user_id, file_path, is_gdrive=False)
            self.logger.info(f"Sent via Telegram: {file_path.name}")

    async def _send_file_to_user(self, user_id: int, file: Path or str, is_gdrive: bool, file_name: str = ""):
        """Send file or GDrive link to user."""
        bot = Bot(token=Config.BOT_TOKEN)
        if is_gdrive:
            text = f"📁 **File uploaded to Google Drive**:\n[{file_name or 'Download'}]({file})"
            await bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown")
        else:
            with open(file, 'rb') as f:
                await bot.send_document(chat_id=user_id, document=InputFile(f, filename=file.name))

    async def _send_download_progress(self, user_id: int, job_id: int, position: int,
                                      current: int, total: int):
        """Send download progress updates (throttled)."""
        now = time.time()
        last = self.progress_messages.get(user_id, 0)
        if now - last < Config.PROGRESS_UPDATE_INTERVAL:
            return
        self.progress_messages[user_id] = now
        pct = int(current / total * 100) if total else 0
        await self._send_progress(user_id, job_id, position, "downloading", "download", pct)

    async def _send_progress(self, user_id: int, job_id: int, position: int,
                             file_name: str, stage: str, percent: int = None,
                             completed: int = 0, failed: int = 0):
        """Generic progress sender. The actual formatting is delegated to handlers."""
        # We need a reference to the Handlers instance or send directly.
        # For simplicity, we'll send raw message.
        from handlers import Handlers  # Avoid circular import; better to pass a callback
        # Instead, we'll just send a simple message.
        bot = Bot(token=Config.BOT_TOKEN)
        stats = await self.db.get_job_statistics(job_id)
        total = stats["total"]
        if stage == "download":
            bar = self._make_progress_bar(percent or 0)
            text = f"📥 Downloading [{position}/{total}]\n{bar} {percent}%\n`{file_name[:40]}`"
        elif stage == "upload":
            text = f"📤 Uploading [{position}/{total}]\n`{file_name[:40]}`"
        elif stage == "complete":
            text = f"✅ Completed [{position}/{total}]: `{file_name[:40]}`"
        elif stage == "failed":
            text = f"❌ Failed [{position}/{total}]: `{file_name[:40]}`"
        else:
            text = f"{stage.capitalize()}: {position}/{total}"
        await bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown")

    def _make_progress_bar(self, percent: int, length: int = 20) -> str:
        filled = int(length * percent / 100)
        bar = "█" * filled + "░" * (length - filled)
        return f"`[{bar}]`"

    async def _notify_user(self, user_id: int, message: str):
        bot = Bot(token=Config.BOT_TOKEN)
        await bot.send_message(chat_id=user_id, text=message)

    async def _send_completion_report(self, user_id: int, job_id: int):
        stats = await self.db.get_job_statistics(job_id)
        job = await self.db.get_job(job_id)
        text = (
            f"🏁 **Job completed!**\n"
            f"Total links: {stats['total']}\n"
            f"✅ Successful: {stats['completed']}\n"
            f"❌ Failed: {stats['failed']}\n"
            f"⚠️ Skipped: {stats['skipped']}\n"
            f"Thank you for using the bot."
        )
        bot = Bot(token=Config.BOT_TOKEN)
        await bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown")

    async def stop(self):
        self.running = False
