"""
AppX Uploader Bot — entry point.
"""

import asyncio
import logging
import os
import sys

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ConversationHandler, filters,
)

from bot.utils import setup_logging
from bot.handlers import (
    cmd_start, cmd_help, cmd_status, cmd_cancel, cmd_logs, cmd_resume,
    handle_document, handle_start_index, handle_cancel_conversation,
    WAITING_START_INDEX,
)
from bot.admin import (
    cmd_admin, cmd_stats, cmd_users, cmd_ban, cmd_unban,
    cmd_addadmin, cmd_jobs, cmd_killjob, cmd_alllogs, cmd_broadcast,
)
from database.db import Database, init_db
from config.settings import BOT_TOKEN, DB_PATH, TEMP_DIR, LOG_DIR

logger = logging.getLogger(__name__)


def build_application() -> Application:
    if not BOT_TOKEN:
        logger.critical("BOT_TOKEN is not set. Check your .env file.")
        sys.exit(1)

    app = Application.builder().token(BOT_TOKEN).build()

    db = Database(DB_PATH)
    app.bot_data["db"] = db

    # ------------------------------------------------------------------ conv
    txt_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Document.FileExtension("txt"), handle_document)],
        states={
            WAITING_START_INDEX: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_start_index),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", handle_cancel_conversation),
            CommandHandler("stop",   handle_cancel_conversation),
        ],
        allow_reentry=True,
    )

    # ----------------------------------------------------------------- cmds
    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("help",      cmd_help))
    app.add_handler(CommandHandler("status",    cmd_status))
    app.add_handler(CommandHandler("cancel",    cmd_cancel))
    app.add_handler(CommandHandler("stop",      cmd_cancel))
    app.add_handler(CommandHandler("resume",    cmd_resume))
    app.add_handler(CommandHandler("logs",      cmd_logs))

    # Admin
    app.add_handler(CommandHandler("admin",     cmd_admin))
    app.add_handler(CommandHandler("stats",     cmd_stats))
    app.add_handler(CommandHandler("users",     cmd_users))
    app.add_handler(CommandHandler("ban",       cmd_ban))
    app.add_handler(CommandHandler("unban",     cmd_unban))
    app.add_handler(CommandHandler("addadmin",  cmd_addadmin))
    app.add_handler(CommandHandler("jobs",      cmd_jobs))
    app.add_handler(CommandHandler("killjob",   cmd_killjob))
    app.add_handler(CommandHandler("alllogs",   cmd_alllogs))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))

    app.add_handler(txt_conv)

    return app


async def main() -> None:
    setup_logging()
    os.makedirs(TEMP_DIR, exist_ok=True)
    os.makedirs(LOG_DIR,  exist_ok=True)

    logger.info("Initialising database at %s", DB_PATH)
    await init_db(DB_PATH)

    logger.info("Starting AppX Uploader Bot…")
    application = build_application()

    await application.initialize()
    await application.start()
    await application.updater.start_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )

    logger.info("Bot is running. Press Ctrl+C to stop.")
    try:
        await asyncio.Event().wait()   # run forever
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutdown requested.")
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    asyncio.run(main())
