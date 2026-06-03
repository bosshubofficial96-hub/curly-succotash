"""
AppX Uploader Bot v3.1 — FIXED main entry point.

Starts:
  1. SQLite database (init schema)
  2. aiohttp web API server (own bypass API + management)
  3. python-telegram-bot Application (polling)
"""

import asyncio
import logging
import os
import sys

from telegram import BotCommand
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    ConversationHandler, MessageHandler, filters,
)

from bot.utils import setup_logging
setup_logging()
logger = logging.getLogger(__name__)

from config.settings import API_ENABLED, BOT_TOKEN, DB_PATH, TEMP_DIR
from database.db import Database, init_db
from bot.handlers import (
    # states
    WAIT_IDX, WAIT_CUSTOM_IDX,
    WAIT_LOGIN_EMAIL, WAIT_LOGIN_PASS,
    WAIT_COOKIE, WAIT_DRM_KEYS, WAIT_PROXY, WAIT_FEEDBACK,
    # conversation internals
    _cancel_conv, _cookie_save, _custom_idx, _feedback_recv,
    _idx_cb, _keys_save, _login_email, _login_pass, _proxy_save,
    # callback router
    callback_router,
    # user commands
    cmd_about, cmd_cancel, cmd_check, cmd_clear,
    cmd_errors, cmd_export, cmd_feedback, cmd_help,
    cmd_history, cmd_keys, cmd_language,
    cmd_login, cmd_logs, cmd_mystats, cmd_notify, cmd_ping,
    cmd_profile, cmd_proxy, cmd_quota, cmd_resume, cmd_settings,
    cmd_speed, cmd_start, cmd_status, cmd_support, cmd_svg,
    cmd_version, handle_doc, cmd_cookie,
)
from bot.admin import (
    cmd_addadmin, cmd_admin, cmd_alllogs, cmd_announce,
    cmd_ban, cmd_broadcast, cmd_clearlogs, cmd_debug,
    cmd_delkey, cmd_errorlogs, cmd_exportdb, cmd_getcookie,
    cmd_getconfig, cmd_jobs, cmd_killjob, cmd_killall,
    cmd_listkeys, cmd_maintenance, cmd_monitor, cmd_note,
    cmd_reload, cmd_removeadmin, cmd_searchuser, cmd_setconfig,
    cmd_setcookie, cmd_setkey, cmd_stats, cmd_topusers,
    cmd_unban, cmd_unwhitelist, cmd_userinfo, cmd_users,
    cmd_whitelist,
)


# ── Command aliases ────────────────────────────────────────────────────────────
async def cmd_stop(update, context):
    """Alias: /stop → /cancel."""
    await cmd_cancel(update, context)

async def cmd_info(update, context):
    """Alias: /info → /check."""
    await cmd_check(update, context)


# ── Bot command menus ─────────────────────────────────────────────────────────
USER_COMMANDS = [
    BotCommand("start",    "Main menu + welcome card"),
    BotCommand("help",     "Full help guide"),
    BotCommand("status",   "Live job progress"),
    BotCommand("cancel",   "Pause/stop current job"),
    BotCommand("stop",     "Stop current job"),
    BotCommand("resume",   "Resume paused job"),
    BotCommand("speed",    "Current download speed"),
    BotCommand("logs",     "Your activity logs"),
    BotCommand("errors",   "Error-only logs"),
    BotCommand("clear",    "Clear your logs"),
    BotCommand("history",  "Past 10 jobs"),
    BotCommand("profile",  "Your profile card"),
    BotCommand("mystats",  "Download statistics"),
    BotCommand("settings", "Bot configuration"),
    BotCommand("login",    "AppX email+password login"),
    BotCommand("cookie",   "Set AppX cookie"),
    BotCommand("keys",     "Add DRM keys"),
    BotCommand("proxy",    "Set HTTP proxy"),
    BotCommand("notify",   "Toggle notifications"),
    BotCommand("language", "Change language"),
    BotCommand("check",    "Validate/classify a URL"),
    BotCommand("info",     "URL info (alias of check)"),
    BotCommand("ping",     "Bot latency check"),
    BotCommand("about",    "About + global stats"),
    BotCommand("version",  "Bot version info"),
    BotCommand("support",  "Get support"),
    BotCommand("feedback", "Send feedback to admins"),
    BotCommand("export",   "Export failed links"),
    BotCommand("quota",    "Your usage quota"),
    BotCommand("svg",      "Download profile card SVG"),
]

ADMIN_COMMANDS = [
    BotCommand("admin",       "Admin control panel"),
    BotCommand("stats",       "Global statistics"),
    BotCommand("users",       "All users list"),
    BotCommand("ban",         "Ban a user"),
    BotCommand("unban",       "Unban a user"),
    BotCommand("addadmin",    "Grant admin rights"),
    BotCommand("removeadmin", "Revoke admin rights"),
    BotCommand("whitelist",   "Whitelist a user"),
    BotCommand("unwhitelist", "Remove from whitelist"),
    BotCommand("userinfo",    "Detailed user info"),
    BotCommand("searchuser",  "Search users"),
    BotCommand("topusers",    "Top users by files"),
    BotCommand("note",        "Add note to user"),
    BotCommand("jobs",        "All jobs"),
    BotCommand("killjob",     "Cancel a job"),
    BotCommand("killall",     "Cancel all running jobs"),
    BotCommand("broadcast",   "Broadcast message"),
    BotCommand("announce",    "Post to channel"),
    BotCommand("setkey",      "Add DRM key"),
    BotCommand("delkey",      "Delete DRM key"),
    BotCommand("listkeys",    "List all DRM keys"),
    BotCommand("setcookie",   "Set AppX cookie"),
    BotCommand("getcookie",   "View saved cookies"),
    BotCommand("alllogs",     "Global log viewer"),
    BotCommand("errorlogs",   "Error log viewer"),
    BotCommand("clearlogs",   "Clear log entries"),
    BotCommand("maintenance", "Toggle maintenance mode"),
    BotCommand("setconfig",   "Set config value"),
    BotCommand("getconfig",   "Get config value"),
    BotCommand("exportdb",    "Export database to JSON"),
    BotCommand("monitor",     "Live running jobs"),
    BotCommand("reload",      "Reload settings from DB"),
    BotCommand("debug",       "Debug info"),
]


def build_app(db: Database) -> Application:
    if not BOT_TOKEN:
        logger.critical("BOT_TOKEN is not set. Edit .env and restart.")
        sys.exit(1)

    app = Application.builder().token(BOT_TOKEN).build()
    app.bot_data["db"] = db

    # ── TXT file upload conversation ──────────────────────────────────────────
    txt_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Document.ALL, handle_doc)],
        states={
            WAIT_IDX:        [CallbackQueryHandler(_idx_cb, pattern=r"^sidx:")],
            WAIT_CUSTOM_IDX: [MessageHandler(filters.TEXT & ~filters.COMMAND, _custom_idx)],
        },
        fallbacks=[CommandHandler("cancel", _cancel_conv)],
        per_user=True, per_chat=True, allow_reentry=True,
    )

    # ── Login conversation ────────────────────────────────────────────────────
    login_conv = ConversationHandler(
        entry_points=[CommandHandler("login", cmd_login)],
        states={
            WAIT_LOGIN_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, _login_email)],
            WAIT_LOGIN_PASS:  [MessageHandler(filters.TEXT & ~filters.COMMAND, _login_pass)],
        },
        fallbacks=[CommandHandler("cancel", _cancel_conv)],
    )

    # ── Cookie conversation ───────────────────────────────────────────────────
    cookie_conv = ConversationHandler(
        entry_points=[CommandHandler("cookie", cmd_cookie)],
        states={
            WAIT_COOKIE: [MessageHandler(filters.TEXT & ~filters.COMMAND, _cookie_save)],
        },
        fallbacks=[CommandHandler("cancel", _cancel_conv)],
    )

    # ── DRM keys conversation ─────────────────────────────────────────────────
    keys_conv = ConversationHandler(
        entry_points=[CommandHandler("keys", cmd_keys)],
        states={
            WAIT_DRM_KEYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, _keys_save)],
        },
        fallbacks=[CommandHandler("cancel", _cancel_conv)],
    )

    # ── Proxy conversation ────────────────────────────────────────────────────
    proxy_conv = ConversationHandler(
        entry_points=[CommandHandler("proxy", cmd_proxy)],
        states={
            WAIT_PROXY: [MessageHandler(filters.TEXT & ~filters.COMMAND, _proxy_save)],
        },
        fallbacks=[CommandHandler("cancel", _cancel_conv)],
    )

    # ── Feedback conversation ─────────────────────────────────────────────────
    feedback_conv = ConversationHandler(
        entry_points=[CommandHandler("feedback", cmd_feedback)],
        states={
            WAIT_FEEDBACK: [MessageHandler(filters.TEXT & ~filters.COMMAND, _feedback_recv)],
        },
        fallbacks=[CommandHandler("cancel", _cancel_conv)],
    )

    # Register conversations first (take priority)
    for conv in [txt_conv, login_conv, cookie_conv, keys_conv, proxy_conv, feedback_conv]:
        app.add_handler(conv)

    # ── User commands ─────────────────────────────────────────────────────────
    for name, fn in {
        "start":    cmd_start,    "help":     cmd_help,
        "status":   cmd_status,   "cancel":   cmd_cancel,
        "stop":     cmd_stop,     "resume":   cmd_resume,
        "speed":    cmd_speed,    "logs":     cmd_logs,
        "errors":   cmd_errors,   "clear":    cmd_clear,
        "history":  cmd_history,  "profile":  cmd_profile,
        "mystats":  cmd_mystats,  "settings": cmd_settings,
        "notify":   cmd_notify,   "language": cmd_language,
        "check":    cmd_check,    "info":     cmd_info,
        "ping":     cmd_ping,     "about":    cmd_about,
        "version":  cmd_version,  "support":  cmd_support,
        "export":   cmd_export,   "quota":    cmd_quota,
        "svg":      cmd_svg,
    }.items():
        app.add_handler(CommandHandler(name, fn))

    # ── Admin commands ────────────────────────────────────────────────────────
    for name, fn in {
        "admin":       cmd_admin,       "stats":       cmd_stats,
        "users":       cmd_users,       "ban":         cmd_ban,
        "unban":       cmd_unban,       "addadmin":    cmd_addadmin,
        "removeadmin": cmd_removeadmin, "whitelist":   cmd_whitelist,
        "unwhitelist": cmd_unwhitelist, "userinfo":    cmd_userinfo,
        "searchuser":  cmd_searchuser,  "topusers":    cmd_topusers,
        "note":        cmd_note,        "jobs":        cmd_jobs,
        "killjob":     cmd_killjob,     "killall":     cmd_killall,
        "broadcast":   cmd_broadcast,   "announce":    cmd_announce,
        "setkey":      cmd_setkey,      "delkey":      cmd_delkey,
        "listkeys":    cmd_listkeys,    "setcookie":   cmd_setcookie,
        "getcookie":   cmd_getcookie,   "alllogs":     cmd_alllogs,
        "errorlogs":   cmd_errorlogs,   "clearlogs":   cmd_clearlogs,
        "maintenance": cmd_maintenance, "setconfig":   cmd_setconfig,
        "getconfig":   cmd_getconfig,   "exportdb":    cmd_exportdb,
        "monitor":     cmd_monitor,     "reload":      cmd_reload,
        "debug":       cmd_debug,
    }.items():
        app.add_handler(CommandHandler(name, fn))

    # ── Inline callback router (catch-all) ────────────────────────────────────
    app.add_handler(CallbackQueryHandler(callback_router))

    return app


async def main() -> None:
    os.makedirs(TEMP_DIR, exist_ok=True)

    # Init database
    await init_db(DB_PATH)
    db = Database(DB_PATH)

    # Apply persisted config overrides into runtime settings
    from config import settings as cfg
    proxy = await db.get_config("http_proxy", "")
    if proxy:
        cfg.HTTP_PROXY = proxy
    cfg.MAINTENANCE_MODE = (await db.get_config("maintenance_mode", "false")).lower() == "true"
    cfg.ADMIN_ONLY_MODE  = (await db.get_config("admin_only_mode",  "false")).lower() == "true"

    # Build bot application
    application = build_app(db)

    # Start web API (own bypass API + management panel)
    runner = None
    if API_ENABLED:
        try:
            from api_server import start_api
            runner = await start_api(db, application.bot)
        except Exception as e:
            logger.warning("Web API failed to start: %s", e)

    # Register Telegram bot command list
    try:
        await application.bot.set_my_commands(USER_COMMANDS)
        logger.info("Commands registered (%d user, %d admin)",
                    len(USER_COMMANDS), len(ADMIN_COMMANDS))
    except Exception as e:
        logger.warning("set_my_commands: %s", e)

    logger.info("🚀 %s v3.1 starting…", cfg.BOT_NAME)

    try:
        await application.initialize()
        await application.start()
        await application.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"],
        )
        logger.info("✅ Bot running. Ctrl+C to stop.")
        await asyncio.Event().wait()          # sleep forever
    finally:
        logger.info("Shutting down…")
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
        if runner:
            await runner.cleanup()
        logger.info("Stopped cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user.")
