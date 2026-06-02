 #!/usr/bin/env python3
"""
Telegram Bot for downloading content from URLs in .txt files.
Supports queue management, resume, admin commands, progress tracking,
and Google Drive uploads.
"""

import asyncio
import sys
from pathlib import Path

from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    PicklePersistence,
    ConversationHandler,
    ContextTypes,
)

from config import Config
from logging_config import setup_logging
from database import Database
from downloader import Downloader
from processor import JobProcessor
from handlers import Handlers
from admin import AdminHandlers
from gdrive import GoogleDrive
from utils import ensure_directories

# Global instances
db = None
downloader = None
processor = None
handlers = None
admin_handlers = None
gdrive = None
logger = None

# Conversation states for extraction start choice
WAITING_FOR_START_POSITION = 1


async def post_init(application: Application) -> None:
    """Initialize all components after the application starts."""
    global db, downloader, processor, handlers, admin_handlers, gdrive, logger

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

    # Initialize Google Drive if enabled
    if Config.GOOGLE_DRIVE_ENABLED:
        gdrive = GoogleDrive(db, logger)
        await gdrive.authenticate()
        logger.info("Google Drive integration enabled")
    else:
        gdrive = None
        logger.info("Google Drive integration disabled")

    # Initialize handlers with references
    handlers = Handlers(db, processor, gdrive, logger)
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
        BotCommand("gdrive_auth", "Authenticate Google Drive (admin only)"),
        BotCommand("gdrive_status", "Check Google Drive connection"),
    ]
    if Config.GOOGLE_DRIVE_ENABLED:
        commands.append(BotCommand("upload_mode", "Toggle upload destination (Telegram/GDrive)"))
    await application.bot.set_my_commands(commands)

    logger.info("Bot is ready and commands registered.")


async def shutdown(application: Application) -> None:
    """Graceful shutdown."""
    logger.info("Shutting down...")
    if processor:
        await processor.stop()
    if db:
        await db.close()
    if downloader:
        await downloader.close()
    logger.info("Shutdown complete.")


async def start_position_conversation(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Ask user from which link number to start processing."""
    await update.message.reply_text(
        "📄 The uploaded file contains URLs.\n"
        "Please enter the starting link number (1 = first link, 2 = second, etc.)\n"
        "Or send /skip to start from the beginning."
    )
    return WAITING_FOR_START_POSITION


async def receive_start_position(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Store the user's chosen start position and begin processing."""
    user_input = update.message.text.strip()
    if user_input.lower() == "/skip":
        start_pos = 1
    else:
        try:
            start_pos = int(user_input)
            if start_pos < 1:
                await update.message.reply_text("❌ Please enter a number >= 1.")
                return WAITING_FOR_START_POSITION
        except ValueError:
            await update.message.reply_text("❌ Invalid input. Send a number or /skip.")
            return WAITING_FOR_START_POSITION

    # Retrieve the uploaded file info stored in context.user_data
    file_info = context.user_data.get("pending_txt_file")
    if not file_info:
        await update.message.reply_text("❌ No file pending. Please upload a .txt file again.")
        return ConversationHandler.END

    # Process the file with the chosen start position
    await handlers.process_txt_file(
        update=update,
        file_info=file_info,
        start_position=start_pos,
    )

    context.user_data.pop("pending_txt_file", None)
    return ConversationHandler.END


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the start position conversation."""
    await update.message.reply_text("❌ Operation cancelled. Upload a new .txt file to try again.")
    context.user_data.pop("pending_txt_file", None)
    return ConversationHandler.END


def main() -> None:
    """Start the bot."""
    # Create application with persistence
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
    if Config.GOOGLE_DRIVE_ENABLED:
        application.add_handler(CommandHandler("upload_mode", handlers.upload_mode_command))
        application.add_handler(CommandHandler("gdrive_status", handlers.gdrive_status_command))

    # ---- Admin-only commands ----
    admin_filter = filters.Chat(chat_id=Config.ADMIN_IDS)
    application.add_handler(CommandHandler("logs", admin_handlers.logs_command, filters=admin_filter))
    application.add_handler(CommandHandler("admin_stats", admin_handlers.stats_command, filters=admin_filter))
    application.add_handler(CommandHandler("broadcast", admin_handlers.broadcast_command, filters=admin_filter))
    application.add_handler(CommandHandler("list_users", admin_handlers.list_users_command, filters=admin_filter))
    application.add_handler(CommandHandler("job_queue", admin_handlers.job_queue_command, filters=admin_filter))
    if Config.GOOGLE_DRIVE_ENABLED:
        application.add_handler(CommandHandler("gdrive_auth", admin_handlers.gdrive_auth_command, filters=admin_filter))

    # ---- Conversation handler for start position ----
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Document.FileExtension("txt") & filters.USER, start_position_conversation)],
        states={
            WAITING_FOR_START_POSITION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_start_position),
                CommandHandler("skip", receive_start_position),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    )
    application.add_handler(conv_handler)

    # Generic fallback for non-txt files
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.echo))

    # Start the bot
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
