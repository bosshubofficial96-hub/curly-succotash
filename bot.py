#!/usr/bin/env python3
"""
Telegram Bot for downloading content from URLs in .txt files.
Supports queue management, resume, admin commands, and progress tracking.
"""

import asyncio
import os
import sys
from pathlib import Path

from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackContext,
    PicklePersistence,
)

from config import Config
from logging_config import setup_logging
from database import Database
from downloader import Downloader
from processor import JobProcessor
from handlers import Handlers
from admin import AdminHandlers
from utils import ensure_directories

# Global instances
db = None
downloader = None
processor = None
handlers = None
admin_handlers = None
logger = None


async def post_init(application: Application) -> None:
    """Set up bot commands and initialize components after application starts."""
    global db, downloader, processor, handlers, admin_handlers, logger

    logger = setup_logging()
    logger.info("Bot is starting up...")

    # Ensure required directories exist
    ensure_directories()

    # Initialize database
    db = Database(Config.DATABASE_URL)
    await db.initialize()

    # Initialize downloader and processor
    downloader = Downloader(db, logger)
    processor = JobProcessor(db, downloader, logger)

    # Start processor background task (processes queued URLs one by one)
    asyncio.create_task(processor.run())

    # Initialize handlers with references
    handlers = Handlers(db, processor, logger)
    admin_handlers = AdminHandlers(db, processor, logger)

    # Set bot commands for menu
    commands = [
        BotCommand("start", "Start the bot and see help"),
        BotCommand("help", "Show help message"),
        BotCommand("status", "Show current job status"),
        BotCommand("cancel", "Cancel current processing"),
        BotCommand("resume", "Resume processing from last checkpoint"),
        BotCommand("logs", "Get recent error logs (admin only)"),
        BotCommand("stop", "Stop all processing"),
    ]
    await application.bot.set_my_commands(commands)

    logger.info("Bot is ready and commands registered.")


async def shutdown(application: Application) -> None:
    """Graceful shutdown: stop processor, close database."""
    logger.info("Shutting down...")
    if processor:
        await processor.stop()
    if db:
        await db.close()
    logger.info("Shutdown complete.")


def main() -> None:
    """Start the bot."""
    # Create application with persistence (optional, for conversations)
    persistence = PicklePersistence(filepath="conversation_data.pkl")
    application = (
        Application.builder()
        .token(Config.BOT_TOKEN)
        .persistence(persistence)
        .post_init(post_init)
        .post_shutdown(shutdown)
        .build()
    )

    # ---- Public commands ----
    application.add_handler(CommandHandler("start", handlers.start_command))
    application.add_handler(CommandHandler("help", handlers.help_command))
    application.add_handler(CommandHandler("status", handlers.status_command))
    application.add_handler(CommandHandler("cancel", handlers.cancel_command))
    application.add_handler(CommandHandler("resume", handlers.resume_command))
    application.add_handler(CommandHandler("stop", handlers.stop_command))

    # ---- Admin-only commands ----
    application.add_handler(CommandHandler("logs", admin_handlers.logs_command, filters=filters.Chat(Config.ADMIN_IDS)))
    application.add_handler(CommandHandler("admin_stats", admin_handlers.stats_command, filters=filters.Chat(Config.ADMIN_IDS)))
    application.add_handler(CommandHandler("broadcast", admin_handlers.broadcast_command, filters=filters.Chat(Config.ADMIN_IDS)))
    application.add_handler(CommandHandler("list_users", admin_handlers.list_users_command, filters=filters.Chat(Config.ADMIN_IDS)))
    application.add_handler(CommandHandler("job_queue", admin_handlers.job_queue_command, filters=filters.Chat(Config.ADMIN_IDS)))

    # ---- File upload handler ----
    application.add_handler(
        MessageHandler(
            filters.Document.FileExtension("txt") & filters.USER,
            handlers.handle_txt_file,
        )
    )

    # Generic fallback for non-txt files
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.echo)
    )

    # Start the bot
    print("Bot is running... Press Ctrl+C to stop.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Bot stopped by user.")
        sys.exit(0)
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)
