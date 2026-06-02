"""
handlers.py - Telegram message and command handlers for the bot.
Manages user interactions, file processing, progress reporting, and upload modes.
"""

import asyncio
import re
from pathlib import Path
from typing import Optional, Dict, Any
from urllib.parse import urlparse

from telegram import Update, Document, Message
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from config import Config
from database import Database
from processor import JobProcessor
from gdrive import GoogleDrive
from utils import validate_url, format_file_size, estimate_remaining_time


class Handlers:
    def __init__(self, db: Database, processor: JobProcessor, gdrive: Optional[GoogleDrive], logger):
        self.db = db
        self.processor = processor
        self.gdrive = gdrive
        self.logger = logger
        # Store user upload preference: "telegram" or "gdrive"
        self.user_upload_mode = {}  # user_id -> mode

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command."""
        user = update.effective_user
        await self.db.register_user(user.id, user.username, user.first_name, user.last_name)

        welcome_text = (
            f"🎉 Welcome {user.first_name}!\n\n"
            f"I'm a downloader bot that can fetch files from URLs provided in a .txt file.\n\n"
            f"📌 How to use:\n"
            f"1. Create a .txt file with one URL per line\n"
            f"2. Send the file to me\n"
            f"3. Choose the starting link number\n"
            f"4. I'll download each file and send it back (or upload to Google Drive if enabled)\n\n"
            f"Commands:\n"
            f"/start - Show this message\n"
            f"/help - Detailed help\n"
            f"/status - Current job status\n"
            f"/cancel - Cancel processing\n"
            f"/resume - Resume from last checkpoint\n"
            f"/upload_mode - Switch between Telegram/Google Drive (if enabled)\n"
            f"/stop - Stop all processing\n"
            f"/logs - Admin only"
        )
        await update.message.reply_text(welcome_text)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help command."""
        help_text = (
            "📖 *Help Guide*\n\n"
            "1️⃣ *Upload a .txt file* containing URLs (one per line)\n"
            "2️⃣ *Select start position* – choose which link to begin from\n"
            "3️⃣ *Processing* – each URL is downloaded and sent to you\n\n"
            "✅ *Supported URLs*: HTTP/HTTPS links to videos, PDFs, images, etc.\n"
            "🔄 *Resume* – if bot restarts, use /resume to continue\n"
            "⏸️ *Cancel* – stops current job\n"
            "📊 *Status* – shows progress, completed/failed counts\n"
            "📁 *Upload mode* – switch between Telegram direct send or Google Drive\n\n"
            "⚠️ *Limits*: Files must be ≤50MB for Telegram. Larger files go to Drive.\n"
            "🔐 *Privacy*: Your files are deleted after successful upload.\n\n"
            "For admin commands, contact the bot owner."
        )
        await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /status command – show current job statistics."""
        user_id = update.effective_user.id
        active_job = self.processor.get_active_job(user_id)

        if not active_job:
            await update.message.reply_text("📭 No active job. Upload a .txt file to start.")
            return

        stats = await self.db.get_job_statistics(active_job["job_id"])
        job = await self.db.get_job(active_job["job_id"])
        total = stats["total"]
        completed = stats["completed"]
        failed = stats["failed"]
        pending = stats["pending"]

        current_pos = None
        current_url = None
        if self.processor.current_item:
            current_pos = self.processor.current_item.get("position")
            current_url = self.processor.current_item.get("url", "")[:80]

        status_text = (
            f"📊 *Job Status*\n"
            f"─────────────────\n"
            f"Job ID: `{active_job['job_id']}`\n"
            f"Filename: `{job['original_filename'][:30]}`\n"
            f"Total links: `{total}`\n"
            f"✅ Completed: `{completed}`\n"
            f"❌ Failed: `{failed}`\n"
            f"⏳ Pending: `{pending}`\n"
        )
        if current_pos:
            status_text += f"▶️ *Currently processing:* {current_pos}/{total}\n"
            if current_url:
                status_text += f"🔗 *URL:* `{current_url}...`\n"
        status_text += f"─────────────────\n"
        status_text += f"Use `/resume` if stopped, `/cancel` to stop current job."

        await update.message.reply_text(status_text, parse_mode=ParseMode.MARKDOWN)

    async def cancel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /cancel command – stop current job."""
        user_id = update.effective_user.id
        active_job = self.processor.get_active_job(user_id)

        if not active_job:
            await update.message.reply_text("ℹ️ No active job to cancel.")
            return

        await self.processor.cancel_job(user_id)
        await update.message.reply_text("✅ Current job cancelled. You can upload a new file.")

    async def resume_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /resume command – resume from last checkpoint."""
        user_id = update.effective_user.id
        checkpoint = await self.db.get_checkpoint(user_id)

        if not checkpoint:
            await update.message.reply_text("ℹ️ No saved checkpoint found. Upload a new file to start.")
            return

        job_id = checkpoint["job_id"]
        last_pos = checkpoint["last_completed_position"]

        job = await self.db.get_job(job_id)
        if not job or job["status"] in ("completed", "cancelled"):
            await self.db.clear_checkpoint(user_id)
            await update.message.reply_text("⚠️ Saved job is finished or cancelled. Please upload a new file.")
            return

        # Resume processing
        await self.processor.resume_job(user_id, job_id, last_pos)
        await update.message.reply_text(
            f"▶️ Resuming from link #{last_pos + 1}. I'll send you progress updates."
        )

    async def stop_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /stop command – stop all processing for the user."""
        user_id = update.effective_user.id
        await self.processor.stop_user_processing(user_id)
        await update.message.reply_text("🛑 Processing stopped. Use /resume to continue later.")

    async def upload_mode_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Toggle between sending files directly to Telegram or uploading to Google Drive."""
        if not Config.GOOGLE_DRIVE_ENABLED:
            await update.message.reply_text("Google Drive integration is not enabled by the admin.")
            return

        user_id = update.effective_user.id
        current = self.user_upload_mode.get(user_id, "telegram")
        new_mode = "gdrive" if current == "telegram" else "telegram"
        self.user_upload_mode[user_id] = new_mode

        mode_display = "📤 **Google Drive**" if new_mode == "gdrive" else "📎 **Telegram** (direct)"
        await update.message.reply_text(
            f"✅ Upload mode switched to {mode_display}\n\n"
            f"*Note*: Files >50MB will automatically use Google Drive if available.",
            parse_mode=ParseMode.MARKDOWN
        )

    async def gdrive_status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Check Google Drive connection status."""
        if not self.gdrive:
            await update.message.reply_text("❌ Google Drive is not configured.")
            return

        if self.gdrive.service:
            about = await self.gdrive.get_about()
            await update.message.reply_text(
                f"✅ Google Drive connected!\n"
                f"Storage: {about.get('storageQuota', {}).get('usage', 'N/A')} / {about.get('storageQuota', {}).get('limit', 'N/A')}"
            )
        else:
            await update.message.reply_text("⚠️ Google Drive not authenticated. Contact admin to run /gdrive_auth")

    async def handle_txt_file(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        This is the entry point for .txt file uploads.
        It stores the file info in context and starts the conversation for start position.
        Note: In bot.py, we already have a ConversationHandler that uses start_position_conversation.
        So this method is not directly called; the conversation handler uses start_position_conversation.
        But we keep this for fallback or direct message handling.
        """
        # Actual logic is inside process_txt_file (called after user chooses start position)
        pass

    async def process_txt_file(self, update: Update, file_info: Dict, start_position: int) -> None:
        """
        Process the uploaded .txt file: extract URLs, create job, add to queue.
        """
        user = update.effective_user
        user_id = user.id
        document: Document = file_info["document"]
        file_id = document.file_id
        original_name = document.file_name or "urls.txt"

        # Download the .txt file from Telegram
        file = await update.get_bot().get_file(file_id)
        local_txt_path = Config.USER_DATA_DIR / f"job_{user_id}_{original_name}"
        await file.download_to_drive(local_txt_path)

        # Read URLs from file
        try:
            with open(local_txt_path, "r", encoding="utf-8") as f:
                raw_urls = [line.strip() for line in f if line.strip()]
        except Exception as e:
            await update.message.reply_text(f"❌ Failed to read the file: {e}")
            local_txt_path.unlink(missing_ok=True)
            return

        # Validate URLs
        valid_urls = []
        invalid_count = 0
        for url in raw_urls:
            if validate_url(url):
                valid_urls.append(url)
            else:
                invalid_count += 1

        if not valid_urls:
            await update.message.reply_text("❌ No valid URLs found in the file.")
            local_txt_path.unlink(missing_ok=True)
            return

        # Adjust start position (convert to 0-index)
        start_idx = max(0, start_position - 1)
        if start_idx >= len(valid_urls):
            await update.message.reply_text(f"❌ Start position {start_position} exceeds total URLs ({len(valid_urls)}).")
            local_txt_path.unlink(missing_ok=True)
            return

        urls_to_process = valid_urls[start_idx:]

        # Create job in database
        job_id = await self.db.create_job(user_id, original_name, len(valid_urls))
        await self.db.add_queue_items(job_id, urls_to_process)

        # Also store original full URL list for checkpoint tracking
        await self.db.save_checkpoint(user_id, job_id, start_idx)  # last completed = start_idx - 1

        # Register user upload mode preference
        upload_mode = self.user_upload_mode.get(user_id, "telegram")
        self.processor.set_upload_mode(user_id, upload_mode)

        # Start processing
        await self.processor.start_job(user_id, job_id, start_idx)

        # Send confirmation
        invalid_msg = f" (ignored {invalid_count} invalid URLs)" if invalid_count else ""
        await update.message.reply_text(
            f"✅ Job created! ID: `{job_id}`\n"
            f"📄 Total URLs: {len(valid_urls)}{invalid_msg}\n"
            f"▶️ Starting from link #{start_position}\n"
            f"📤 Upload mode: {'Google Drive' if upload_mode == 'gdrive' else 'Telegram'}\n\n"
            f"I'll send progress updates as each file is processed.",
            parse_mode=ParseMode.MARKDOWN
        )

        # Cleanup temporary txt file
        local_txt_path.unlink(missing_ok=True)

    async def echo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Echo non-command text (simple feedback)."""
        await update.message.reply_text(
            "I only understand commands starting with / or .txt file uploads. Send /help for guidance."
        )

    async def send_progress_update(self, user_id: int, job_id: int, current_pos: int, total: int,
                                   current_file_name: str, stage: str, progress_pct: int = None,
                                   eta: int = None, completed: int = 0, failed: int = 0) -> None:
        """Send nicely formatted progress message to user."""
        if stage == "download":
            progress_bar = self._make_progress_bar(progress_pct or 0)
            text = (
                f"📥 *Downloading*: `{current_file_name[:40]}`\n"
                f"📍 {current_pos}/{total}\n"
                f"{progress_bar} {progress_pct}%\n"
                f"✅ Completed: {completed}  ❌ Failed: {failed}\n"
            )
            if eta:
                text += f"⏱️ ETA: {eta}s"
        elif stage == "upload":
            progress_bar = self._make_progress_bar(progress_pct or 0)
            text = (
                f"📤 *Uploading*: `{current_file_name[:40]}`\n"
                f"📍 {current_pos}/{total}\n"
                f"{progress_bar} {progress_pct}%\n"
            )
        elif stage == "complete":
            text = (
                f"✅ *Completed*: `{current_file_name[:40]}`\n"
                f"📍 {current_pos}/{total}\n"
                f"📊 Total completed: {completed}/{total} (Failed: {failed})"
            )
        elif stage == "failed":
            text = (
                f"❌ *Failed*: `{current_file_name[:40]}`\n"
                f"Will retry later or skip."
            )
        else:
            text = f"Processing: {current_pos}/{total} - {stage}"

        try:
            await self._send_or_edit(user_id, text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            self.logger.warning(f"Failed to send progress to {user_id}: {e}")

    def _make_progress_bar(self, percent: int, length: int = 20) -> str:
        filled = int(length * percent / 100)
        bar = "█" * filled + "░" * (length - filled)
        return f"`[{bar}]`"

    async def _send_or_edit(self, user_id: int, text: str, **kwargs):
        """Simple send (no message editing for simplicity)."""
        # In production you may store message_id to edit, but we'll just send new messages.
        from telegram import Bot
        bot = Bot(token=Config.BOT_TOKEN)
        await bot.send_message(chat_id=user_id, text=text, **kwargs)
