#!/usr/bin/env python3
"""
bot.py - Main entry point for the Telegram URL Downloader Bot.
All command handlers are added after post_init to avoid NoneType errors.
"""

import asyncio
import sys
from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    PicklePersistence,
    ConversationHandler,
    ContextTypes,
    CallbackQueryHandler,
)

from config import Config
from logging_config import setup_logging
from database import Database
from downloader import Downloader
from processor import JobProcessor
from handlers import Handlers
from admin import AdminHandlers
from gdrive import GoogleDrive
from utils import ensure_directories, cleanup_old_temp_files

# Global instances (used by shutdown)
db = None
downloader = None
processor = None
logger = None

# Conversation states
WAITING_FOR_START_POSITION = 1


async def post_init(application: Application) -> None:
    """Initialize all components and add command handlers after they are ready."""
    global db, downloader, processor, logger

    logger = setup_logging()
    logger.info("Bot starting up...")

    ensure_directories()
    cleanup_old_temp_files()

    # Database
    db = Database(Config.DATABASE_URL)
    await db.initialize()

    # Downloader and processor
    downloader = Downloader(db, logger)
    processor = JobProcessor(db, downloader, logger)
    asyncio.create_task(processor.run())

    # Google Drive
    gdrive = None
    if Config.GOOGLE_DRIVE_ENABLED:
        gdrive = GoogleDrive(db, logger)
        await gdrive.authenticate()
        processor.set_gdrive(gdrive)
        logger.info("Google Drive enabled")

    # Handlers (all command logic)
    handlers = Handlers(db, processor, gdrive, logger)
    admin_handlers = AdminHandlers(db, processor, logger)
    if gdrive:
        admin_handlers.set_gdrive(gdrive)

    # Register callback handler for inline buttons
    application.add_handler(CallbackQueryHandler(handlers.button_callback))

    # ----- Public commands -----
    application.add_handler(CommandHandler("start", handlers.start_command))
    application.add_handler(CommandHandler("help", handlers.help_command))
    application.add_handler(CommandHandler("status", handlers.status_command))
    application.add_handler(CommandHandler("cancel", handlers.cancel_command))
    application.add_handler(CommandHandler("resume", handlers.resume_command))
    application.add_handler(CommandHandler("stop", handlers.stop_command))
    application.add_handler(CommandHandler("upload_mode", handlers.upload_mode_command))
    application.add_handler(CommandHandler("gdrive_status", handlers.gdrive_status_command))

    # ----- Admin commands -----
    admin_filter = filters.Chat(chat_id=Config.ADMIN_IDS)
    application.add_handler(CommandHandler("logs", admin_handlers.logs_command, filters=admin_filter))
    application.add_handler(CommandHandler("admin_stats", admin_handlers.stats_command, filters=admin_filter))
    application.add_handler(CommandHandler("broadcast", admin_handlers.broadcast_command, filters=admin_filter))
    application.add_handler(CommandHandler("list_users", admin_handlers.list_users_command, filters=admin_filter))
    application.add_handler(CommandHandler("job_queue", admin_handlers.job_queue_command, filters=admin_filter))
    application.add_handler(CommandHandler("gdrive_auth", admin_handlers.gdrive_auth_command, filters=admin_filter))
    application.add_handler(CommandHandler("ban", admin_handlers.ban_command, filters=admin_filter))
    application.add_handler(CommandHandler("unban", admin_handlers.unban_command, filters=admin_filter))

    # ----- Conversation for start position -----
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Document.FileExtension("txt") & filters.USER, 
                                     lambda u, c: handlers.start_position_conversation(u, c))],
        states={
            WAITING_FOR_START_POSITION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, 
                               lambda u, c: handlers.receive_start_position(u, c)),
                CommandHandler("skip", lambda u, c: handlers.receive_start_position(u, c)),
            ],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: handlers.cancel_conversation(u, c))],
    )
    application.add_handler(conv_handler)

    # Fallback for non‑command text
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.echo))

    # Set bot commands (for menu)
    commands = [
        BotCommand("start", "Start the bot"),
        BotCommand("help", "Show help"),
        BotCommand("status", "Current job status"),
        BotCommand("cancel", "Cancel current job"),
        BotCommand("resume", "Resume from checkpoint"),
        BotCommand("stop", "Stop processing"),
        BotCommand("upload_mode", "Toggle Telegram/GDrive"),
        BotCommand("gdrive_status", "GDrive connection status"),
        BotCommand("logs", "Error logs (admin)"),
        BotCommand("admin_stats", "System stats (admin)"),
        BotCommand("broadcast", "Broadcast message (admin)"),
        BotCommand("list_users", "List users (admin)"),
        BotCommand("job_queue", "Active jobs (admin)"),
    ]
    await application.bot.set_my_commands(commands)

    logger.info("Bot fully initialized and ready.")


async def shutdown(application: Application) -> None:
    logger.info("Shutting down...")
    if processor:
        await processor.stop()
    if db:
        await db.close()
    if downloader:
        await downloader.close()
    logger.info("Shutdown complete.")


def main() -> None:
    """Start the bot."""
    persistence = PicklePersistence(filepath="conversation_data.pkl")
    application = (
        Application.builder()
        .token(Config.BOT_TOKEN)
        .persistence(persistence)
        .post_init(post_init)
        .post_shutdown(shutdown)
        .build()
    )

    print("🚀 Bot is running... Press Ctrl+C to stop.")
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
