"""
Main Telegram bot command and message handlers.
"""

import os
import logging
import asyncio
from typing import Optional

from telegram import Update, Document
from telegram.ext import (
    ContextTypes, CommandHandler, MessageHandler,
    filters, ConversationHandler,
)
from telegram.constants import ParseMode

from database.db import Database
from .queue_manager import start_job, cancel_job, resume_job, _running_tasks, _cancel_events
from .drm import is_valid_url
from config.settings import (
    ADMIN_IDS, ADMIN_ONLY_MODE,
    RATE_LIMIT_CALLS, RATE_LIMIT_PERIOD, TEMP_DIR,
)

logger = logging.getLogger(__name__)

# ConversationHandler states
WAITING_START_INDEX = 1


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

async def _guard(update: Update, db: Database) -> bool:
    """Returns False if message should be rejected."""
    user = update.effective_user
    if not user:
        return False

    await db.upsert_user(
        user.id, user.username or "", user.first_name or "", user.last_name or ""
    )

    if ADMIN_ONLY_MODE and user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ This bot is in admin-only mode.")
        return False

    if await db.is_banned(user.id):
        await update.message.reply_text("⛔ You have been banned from using this bot.")
        return False

    if not await db.check_rate_limit(user.id, RATE_LIMIT_CALLS, RATE_LIMIT_PERIOD):
        await update.message.reply_text(
            f"⏳ You're sending too many requests. Please wait a moment."
        )
        return False

    return True


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _guard(update, db):
        return

    text = (
        "👋 <b>Welcome to AppX Uploader Bot!</b>\n\n"
        "📤 <b>How to use:</b>\n"
        "1. Send me a <code>.txt</code> file with one URL per line\n"
        "2. Choose the starting link number\n"
        "3. I'll download and send each file back to you!\n\n"
        "⚡ <b>Supported:</b> PDFs, Videos, Images, Documents\n"
        "🔓 <b>DRM bypass:</b> AppX signed URLs, encrypted PDFs, HLS/DASH streams\n\n"
        "📋 <b>Commands:</b>\n"
        "/help — Full help & instructions\n"
        "/status — Current job progress\n"
        "/cancel — Stop current job\n"
        "/resume — Resume last paused job\n"
        "/logs — View processing logs\n"
        "/stop — Alias for /cancel"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _guard(update, db):
        return

    text = (
        "📖 <b>AppX Uploader Bot — Help</b>\n\n"
        "<b>📋 Commands</b>\n"
        "/start  — Welcome message\n"
        "/help   — This help page\n"
        "/status — Live progress of your active job\n"
        "/cancel — Pause/cancel the current job\n"
        "/resume — Resume the last paused job\n"
        "/logs   — Show recent download/upload logs\n"
        "/stop   — Same as /cancel\n\n"
        "<b>📤 Uploading a file</b>\n"
        "Send a <code>.txt</code> file containing one URL per line.\n"
        "Supported sources:\n"
        "• AppX (appx.co.in) signed URLs\n"
        "• Encrypted / DRM-protected PDFs\n"
        "• HLS / DASH video streams\n"
        "• Standard HTTPS download links\n\n"
        "<b>🔢 Start Index</b>\n"
        "After uploading the file you can choose which line to start from.\n"
        "Useful to skip already-downloaded files or retry from a specific point.\n\n"
        "<b>♻️ Resume</b>\n"
        "If a job is interrupted (bot restart, /cancel) use /resume to continue\n"
        "from where it left off — no re-downloading already-completed links.\n\n"
        "<b>⚠️ Notes</b>\n"
        "• Large files are split and sent as Telegram documents\n"
        "• Temp files are deleted after upload\n"
        "• Each link is retried up to 3 times on failure\n"
        "• Failed links are skipped and logged (see /logs)"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _guard(update, db):
        return

    user_id = update.effective_user.id
    job = await db.get_user_active_job(user_id)
    if not job:
        job = await db.get_user_latest_job(user_id)
    if not job:
        await update.message.reply_text("ℹ️ No jobs found. Send a .txt file to get started!")
        return

    total   = job["total_links"]
    current = job["current_index"]
    comp    = job["completed_links"]
    failed  = job["failed_links"]
    status  = job["status"]

    pct = int(100 * current / total) if total else 0
    bar = ("█" * int(12 * current / total) + "░" * (12 - int(12 * current / total))) if total else "░" * 12

    text = (
        f"📊 <b>Job Status</b>\n\n"
        f"🆔 Job   : <code>{job['job_id'][:8]}</code>\n"
        f"📌 State  : <b>{status.upper()}</b>\n"
        f"🔗 Links  : {current}/{total}  [{bar}] {pct}%\n"
        f"✅ Done   : {comp}\n"
        f"❌ Failed : {failed}\n"
        f"📅 Started: {job['created_at'][:16]}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /cancel & /stop
# ---------------------------------------------------------------------------

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _guard(update, db):
        return

    user_id = update.effective_user.id
    job = await db.get_user_active_job(user_id)
    if not job:
        await update.message.reply_text("ℹ️ No active job to cancel.")
        return

    await cancel_job(job["job_id"], db)
    await update.message.reply_text(
        f"⏸ Job <code>{job['job_id'][:8]}</code> has been paused.\n"
        "Use /resume to continue from where it left off.",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# /resume
# ---------------------------------------------------------------------------

async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _guard(update, db):
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    job = await db.get_user_latest_job(user_id)

    if not job or job["status"] not in ("paused",):
        await update.message.reply_text("ℹ️ No paused job to resume.")
        return

    # Reload URLs from DB
    async_db = db
    links = []
    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        async with conn.execute(
            "SELECT url FROM links WHERE job_id = ? ORDER BY line_number", (job["job_id"],)
        ) as cur:
            links = [r[0] async for r in cur]

    msg = await update.message.reply_text(
        f"▶️ Resuming job <code>{job['job_id'][:8]}</code> from link {job['current_index'] + 1}…",
        parse_mode=ParseMode.HTML,
    )

    await resume_job(
        context.bot, db, job["job_id"], user_id, chat_id, links, msg.message_id
    )


# ---------------------------------------------------------------------------
# /logs
# ---------------------------------------------------------------------------

async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _guard(update, db):
        return

    user_id = update.effective_user.id
    logs = await db.get_logs(user_id=user_id, limit=20)

    if not logs:
        await update.message.reply_text("📭 No logs yet.")
        return

    lines = ["📋 <b>Recent Logs</b> (last 20)\n"]
    for log in reversed(logs):
        icon = {"INFO": "ℹ️", "ERROR": "❌", "WARNING": "⚠️"}.get(log["level"], "📌")
        ts = log["created_at"][:16]
        msg = log["message"][:80]
        lines.append(f"{icon} <code>{ts}</code>  {msg}")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# TXT file handler — entry point for processing
# ---------------------------------------------------------------------------

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db: Database = context.bot_data["db"]
    if not await _guard(update, db):
        return ConversationHandler.END

    doc: Document = update.message.document
    if not doc.file_name or not doc.file_name.lower().endswith(".txt"):
        await update.message.reply_text("⚠️ Please send a <b>.txt</b> file.", parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    # Check for active job
    user_id = update.effective_user.id
    active = await db.get_user_active_job(user_id)
    if active:
        await update.message.reply_text(
            "⚠️ You already have an active job.\n"
            "Use /cancel to stop it first, or /status to check progress."
        )
        return ConversationHandler.END

    # Download the txt file
    try:
        file = await doc.get_file()
        tmp_path = os.path.join(TEMP_DIR, f"{user_id}_{doc.file_id}.txt")
        os.makedirs(TEMP_DIR, exist_ok=True)
        await file.download_to_drive(tmp_path)
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to download your file: {e}")
        return ConversationHandler.END

    # Parse URLs
    try:
        with open(tmp_path, "r", encoding="utf-8", errors="ignore") as f:
            raw_lines = [l.strip() for l in f.readlines()]
        urls = [l for l in raw_lines if l and is_valid_url(l)]
        os.remove(tmp_path)
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to parse file: {e}")
        return ConversationHandler.END

    if not urls:
        await update.message.reply_text(
            "⚠️ No valid URLs found in the file.\n"
            "Make sure each line contains a valid https:// URL."
        )
        return ConversationHandler.END

    context.user_data["pending_urls"] = urls

    await update.message.reply_text(
        f"📂 <b>File parsed!</b>\n"
        f"🔗 Found <b>{len(urls)}</b> valid URL(s)\n\n"
        f"📍 From which link number should I start? (1–{len(urls)})\n"
        f"Reply with a number, or <code>1</code> to start from the beginning.",
        parse_mode=ParseMode.HTML,
    )
    return WAITING_START_INDEX


async def handle_start_index(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db: Database = context.bot_data["db"]
    urls: list = context.user_data.get("pending_urls", [])

    if not urls:
        await update.message.reply_text("❌ Session expired. Please re-upload your file.")
        return ConversationHandler.END

    text = update.message.text.strip()
    try:
        start = int(text)
        if start < 1 or start > len(urls):
            raise ValueError()
        start_index = start - 1   # convert to 0-based
    except ValueError:
        await update.message.reply_text(
            f"⚠️ Invalid number. Enter a value between 1 and {len(urls)}."
        )
        return WAITING_START_INDEX

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    msg = await update.message.reply_text(
        f"🚀 Starting job from link {start}…\n\nPlease wait…",
        parse_mode=ParseMode.HTML,
    )

    job_id = await start_job(
        context.bot, db, user_id, chat_id,
        urls, start_index, msg.message_id
    )

    context.user_data.pop("pending_urls", None)
    return ConversationHandler.END


async def handle_cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("pending_urls", None)
    await update.message.reply_text("❌ Cancelled file upload.")
    return ConversationHandler.END
