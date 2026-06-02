"""
admin.py - Admin-only commands for monitoring, user management,
broadcasting, and system statistics.
"""

import asyncio
from typing import List, Dict, Any
from datetime import datetime, timedelta

from telegram import Update, Bot
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from config import Config
from database import Database
from processor import JobProcessor
from gdrive import GoogleDrive


class AdminHandlers:
    def __init__(self, db: Database, processor: JobProcessor, logger):
        self.db = db
        self.processor = processor
        self.logger = logger
        self.gdrive: GoogleDrive = None

    def set_gdrive(self, gdrive: GoogleDrive):
        self.gdrive = gdrive

    async def _is_admin(self, user_id: int) -> bool:
        return user_id in Config.ADMIN_IDS

    async def logs_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Send recent error logs to admin."""
        user_id = update.effective_user.id
        if not await self._is_admin(user_id):
            await update.message.reply_text("⛔ Admin only.")
            return

        limit = 50
        logs = await self.db.get_error_logs(limit)
        if not logs:
            await update.message.reply_text("📭 No error logs found.")
            return

        # Format logs into message (truncate if too long)
        log_text = "📋 *Recent Error Logs:*\n\n"
        for log in logs[:20]:  # Telegram message limit
            timestamp = log["timestamp"][:19]
            msg = log["message"][:100]
            log_text += f"`{timestamp}` - {msg}\n"
        if len(logs) > 20:
            log_text += f"\n... and {len(logs)-20} more. Check server logs for details."

        await update.message.reply_text(log_text, parse_mode=ParseMode.MARKDOWN)

    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show system statistics."""
        user_id = update.effective_user.id
        if not await self._is_admin(user_id):
            await update.message.reply_text("⛔ Admin only.")
            return

        # Get user stats
        users = await self.db.get_all_users()
        total_users = len(users)
        active_jobs = len(self.processor.active_jobs)

        # Get job stats for last 7 days
        seven_days_ago = (datetime.now() - timedelta(days=7)).date()
        # We'll query directly
        stats_text = (
            f"📊 *System Statistics*\n"
            f"─────────────────\n"
            f"👥 Total users: `{total_users}`\n"
            f"⚡ Active jobs: `{active_jobs}`\n"
            f"─────────────────\n"
            f"🤖 Bot uptime: ... (to be implemented)\n"
            f"💾 Database: SQLite\n"
            f"📁 Temp files: `{Config.USER_DATA_DIR}`\n"
        )
        await update.message.reply_text(stats_text, parse_mode=ParseMode.MARKDOWN)

    async def broadcast_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Broadcast a message to all users."""
        user_id = update.effective_user.id
        if not await self._is_admin(user_id):
            await update.message.reply_text("⛔ Admin only.")
            return

        if not context.args:
            await update.message.reply_text(
                "Usage: `/broadcast <message>`\n"
                "Example: `/broadcast System maintenance in 1 hour.`",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        message = " ".join(context.args)
        confirm = await update.message.reply_text(f"📢 Broadcasting to all users...\nMessage: {message[:100]}")

        users = await self.db.get_all_users()
        bot = update.get_bot()
        success = 0
        failed = 0

        for user in users:
            try:
                await bot.send_message(chat_id=user["user_id"], text=f"📢 *Broadcast from admin:*\n{message}", parse_mode=ParseMode.MARKDOWN)
                success += 1
                await asyncio.sleep(0.05)  # slight delay to avoid flooding
            except Exception as e:
                self.logger.warning(f"Failed to broadcast to {user['user_id']}: {e}")
                failed += 1

        await confirm.edit_text(f"✅ Broadcast sent!\nSuccess: {success}\nFailed: {failed}")

    async def list_users_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """List all registered users (paginated)."""
        user_id = update.effective_user.id
        if not await self._is_admin(user_id):
            await update.message.reply_text("⛔ Admin only.")
            return

        users = await self.db.get_all_users()
        if not users:
            await update.message.reply_text("No users found.")
            return

        # Paginate: 10 users per message
        page = 0
        if context.args and context.args[0].isdigit():
            page = int(context.args[0]) - 1
        page_size = 10
        start = page * page_size
        end = start + page_size
        page_users = users[start:end]

        if not page_users:
            await update.message.reply_text(f"No users on page {page+1}.")
            return

        text = f"👥 *Users (page {page+1}/{((len(users)-1)//page_size)+1})*\n\n"
        for u in page_users:
            username = u.get("username", "no username")
            text += f"• ID: `{u['user_id']}` – @{username} – Admin: {u['is_admin']}\n"

        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def job_queue_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show current active jobs in the queue."""
        user_id = update.effective_user.id
        if not await self._is_admin(user_id):
            await update.message.reply_text("⛔ Admin only.")
            return

        active_jobs = self.processor.active_jobs
        if not active_jobs:
            await update.message.reply_text("No active jobs.")
            return

        text = "🔄 *Active Jobs*\n\n"
        for uid, job in active_jobs.items():
            job_id = job["job_id"]
            upload_mode = job.get("upload_mode", "telegram")
            job_stats = await self.db.get_job_statistics(job_id)
            text += f"User `{uid}` – Job `{job_id}`\n"
            text += f"  Progress: {job_stats['completed']}/{job_stats['total']} completed\n"
            text += f"  Mode: {upload_mode}\n"
            if self.processor.current_item and self.processor.current_item.get("job_id") == job_id:
                pos = self.processor.current_item.get("position", 0)
                text += f"  Currently: URL #{pos}\n"

        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def gdrive_auth_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Authenticate Google Drive (OAuth flow)."""
        user_id = update.effective_user.id
        if not await self._is_admin(user_id):
            await update.message.reply_text("⛔ Admin only.")
            return

        if not Config.GOOGLE_DRIVE_ENABLED:
            await update.message.reply_text("Google Drive is not enabled in config.")
            return

        await update.message.reply_text("Starting Google Drive authentication...")
        if not self.gdrive:
            from gdrive import GoogleDrive
            self.gdrive = GoogleDrive(self.db, self.logger)

        success = await self.gdrive.authenticate()
        if success:
            await update.message.reply_text("✅ Google Drive authenticated successfully!")
        else:
            await update.message.reply_text("❌ Authentication failed. Check credentials file.")

    async def ban_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Ban a user by ID."""
        user_id = update.effective_user.id
        if not await self._is_admin(user_id):
            await update.message.reply_text("⛔ Admin only.")
            return

        if not context.args or not context.args[0].isdigit():
            await update.message.reply_text("Usage: /ban <user_id>")
            return

        target_id = int(context.args[0])
        await self.db.ban_user(target_id)
        await update.message.reply_text(f"✅ User {target_id} banned.")

        # Cancel any active job for that user
        if target_id in self.processor.active_jobs:
            await self.processor.cancel_job(target_id)

    async def unban_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Unban a user."""
        user_id = update.effective_user.id
        if not await self._is_admin(user_id):
            await update.message.reply_text("⛔ Admin only.")
            return

        if not context.args or not context.args[0].isdigit():
            await update.message.reply_text("Usage: /unban <user_id>")
            return

        target_id = int(context.args[0])
        await self.db.unban_user(target_id)
        await update.message.reply_text(f"✅ User {target_id} unbanned.")
