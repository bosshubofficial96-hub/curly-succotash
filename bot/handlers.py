"""
User-facing handlers — 30+ commands + inline callbacks + conversation flows.
"""

import io
import logging
import os
from datetime import datetime
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackQueryHandler, CommandHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters,
)

from config.settings import (
    ADMIN_IDS, ADMIN_ONLY_MODE, BOT_NAME,
    MAINTENANCE_MODE, RATE_LIMIT_CALLS, RATE_LIMIT_PERIOD,
    REQUIRE_JOIN_CHANNEL, TEMP_DIR, WELCOME_IMAGE_ENABLED,
)
from database.db import Database
from .drm import appx_login, is_valid_url
from .fonts import APP_NAME, DIVIDER, TAGLINE, bold, script, smallcaps
from .image_gen import generate_welcome_card, get_svg_source
from .keyboards import (
    admin_menu, back_main, back_settings, config_menu, confirm,
    drm_menu, failed_kb, job_controls, lang_kb, main_menu,
    notify_kb, settings_menu, start_index_kb, users_menu,
)
from .queue_manager import (
    cancel_job, is_running, new_jid, pause_job, resume_from_db,
    resume_in_place, start_job,
)
from .utils import fmt_bytes

logger = logging.getLogger(__name__)

# ── Conversation states ────────────────────────────────────────────────────────
(
    WAIT_IDX, WAIT_CUSTOM_IDX,
    WAIT_LOGIN_EMAIL, WAIT_LOGIN_PASS,
    WAIT_COOKIE, WAIT_DRM_KEYS, WAIT_PROXY,
    WAIT_FEEDBACK, WAIT_BROADCAST_MSG,
    WAIT_NOTE_UID, WAIT_NOTE_TEXT,
    WAIT_SEARCH_QUERY,
) = range(12)


# ── Guard ─────────────────────────────────────────────────────────────────────
async def guard(update: Update, db: Database) -> bool:
    user = update.effective_user
    if not user: return False
    await db.upsert_user(user.id, user.username or "",
                          user.first_name or "", user.last_name or "")

    if MAINTENANCE_MODE and user.id not in ADMIN_IDS:
        txt = "🔧 Bot is under maintenance. Please try again later."
        if update.message: await update.message.reply_text(txt)
        elif update.callback_query: await update.callback_query.answer(txt, show_alert=True)
        return False

    if ADMIN_ONLY_MODE and user.id not in ADMIN_IDS:
        txt = "⛔ This bot is admin-only."
        if update.message: await update.message.reply_text(txt)
        elif update.callback_query: await update.callback_query.answer(txt, show_alert=True)
        return False

    if await db.is_banned(user.id):
        txt = "⛔ You are banned from using this bot."
        if update.message: await update.message.reply_text(txt)
        elif update.callback_query: await update.callback_query.answer(txt, show_alert=True)
        return False

    if not await db.check_rate(user.id, RATE_LIMIT_CALLS, RATE_LIMIT_PERIOD):
        txt = "⏳ Rate limit reached. Please wait."
        if update.message: await update.message.reply_text(txt)
        elif update.callback_query: await update.callback_query.answer(txt, show_alert=True)
        return False

    if REQUIRE_JOIN_CHANNEL:
        try:
            member = await update.get_bot().get_chat_member(
                f"@{REQUIRE_JOIN_CHANNEL}", user.id)
            if member.status in ("left", "kicked"):
                txt = f"📢 Please join @{REQUIRE_JOIN_CHANNEL} to use this bot."
                if update.message: await update.message.reply_text(txt)
                return False
        except Exception: pass

    return True


def _welcome(first_name: str, is_admin: bool = False) -> str:
    return (
        f"✨ {APP_NAME}\n"
        f"   {TAGLINE}\n"
        f"{DIVIDER}\n\n"
        f"👋 Hello, <b>{first_name}</b>!\n\n"
        f"📤 <b>What I do:</b>\n"
        f"  • Accept a <code>.txt</code> file with one URL per line\n"
        f"  • Bypass AppX CDN / DRM / signed-URL protection\n"
        f"  • Download PDFs, Videos, Images & Documents\n"
        f"  • Send every file back to you on Telegram\n\n"
        f"⚡ <b>Supported:</b> AppX Live DRM V2/V3 · HLS/DASH · Encrypted PDFs\n"
        f"🛡️ <b>Bypass:</b> CDN Key · URLPrefix · Widevine · ClearKey · pikepdf\n\n"
        f"{'🛡️ <b>Admin mode active</b>\n\n' if is_admin else ''}"
        f"➡️ Send a <b>.txt</b> file or tap a button below to get started!"
    )


# ══════════════════════════════════════════════════════════════════════════════
# /start
# ══════════════════════════════════════════════════════════════════════════════
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await guard(update, db): return
    u       = update.effective_user
    is_adm  = await db.is_admin(u.id)
    user_db = await db.get_user(u.id)

    # Send welcome image if enabled
    if WELCOME_IMAGE_ENABLED and user_db:
        try:
            png = generate_welcome_card(
                first_name = u.first_name or "User",
                username   = u.username or "",
                user_id    = u.id,
                joined     = user_db.get("joined_at", datetime.utcnow().isoformat()),
                jobs       = user_db.get("total_jobs", 0),
                files      = user_db.get("total_files", 0),
                bytes_sent = user_db.get("total_bytes", 0),
                is_admin   = is_adm,
            )
            if png:
                await update.message.reply_photo(
                    photo=io.BytesIO(png),
                    caption=_welcome(u.first_name or "there", is_adm),
                    parse_mode=ParseMode.HTML,
                    reply_markup=main_menu(is_adm),
                )
                return
        except Exception as e:
            logger.warning("Welcome image failed: %s", e)

    await update.message.reply_text(
        _welcome(u.first_name or "there", is_adm),
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu(is_adm),
    )


# ══════════════════════════════════════════════════════════════════════════════
# /help
# ══════════════════════════════════════════════════════════════════════════════
HELP = (
    f"📖 {bold('Help Guide')}\n{DIVIDER}\n\n"
    f"<b>📤 How to use</b>\n"
    "1. Send a <code>.txt</code> file — one URL per line\n"
    "2. Choose starting link (quick-pick or custom)\n"
    "3. Watch live progress with inline controls\n\n"
    f"<b>⌨️ User Commands (30+)</b>\n"
    "/start    — Main menu + welcome card\n"
    "/help     — This guide\n"
    "/status   — Current job live status\n"
    "/cancel   — Pause current job\n"
    "/stop     — Same as /cancel\n"
    "/resume   — Resume last paused job\n"
    "/logs     — Your recent logs\n"
    "/errors   — Error-only logs\n"
    "/history  — Past 10 jobs\n"
    "/profile  — Your profile card\n"
    "/mystats  — Your download stats\n"
    "/settings — Configure credentials\n"
    "/cookie   — Set AppX cookie\n"
    "/login    — AppX email+password login\n"
    "/keys     — Add DRM keys\n"
    "/proxy    — Set HTTP proxy\n"
    "/notify   — Toggle job notifications\n"
    "/language — Change language\n"
    "/check    — Validate a single URL\n"
    "/info     — Info about a URL/job\n"
    "/ping     — Bot latency check\n"
    "/about    — About this bot\n"
    "/version  — Bot version info\n"
    "/support  — Get support\n"
    "/feedback — Send feedback to admins\n"
    "/clear    — Clear your logs\n"
    "/export   — Export failed link list\n"
    "/retry    — Retry last failed links\n"
    "/quota    — Your usage quota\n"
    "/speed    — Show last download speed\n"
    "/svg      — Get your welcome card SVG\n\n"
    f"<b>🔓 DRM Bypass</b>\n"
    "• URLPrefix decode → real CDN path\n"
    "• Signature strip → unsigned URL\n"
    "• AppX cookie auth → restricted access\n"
    "• Widevine/ClearKey key injection\n"
    "• yt-dlp HLS/DASH stream merge\n"
    "• pikepdf encrypted PDF removal"
)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await guard(update, db): return
    await update.message.reply_text(HELP, parse_mode=ParseMode.HTML, reply_markup=back_main())


# ══════════════════════════════════════════════════════════════════════════════
# /status
# ══════════════════════════════════════════════════════════════════════════════
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await guard(update, db): return
    uid = update.effective_user.id
    job = await db.get_active_job(uid) or await db.get_latest_job(uid)
    if not job:
        await update.message.reply_text("ℹ️ No jobs yet. Send a .txt file!", reply_markup=back_main())
        return
    await update.message.reply_text(
        _job_txt(job), parse_mode=ParseMode.HTML,
        reply_markup=job_controls(job["job_id"], paused=job["status"]=="paused"),
    )


def _job_txt(j: dict) -> str:
    tot  = j["total_links"]; cur = j["current_index"]
    comp = j["completed_links"]; fail = j["failed_links"]
    pct  = int(100*cur/tot) if tot else 0
    bar  = ("█"*int(12*cur/tot)+"░"*(12-int(12*cur/tot))) if tot else "░"*12
    st   = j["status"].upper()
    ic   = {"RUNNING":"⚡","COMPLETED":"✅","PAUSED":"⏸","CANCELLED":"⏹","FAILED":"❌"}.get(st,"❓")
    src  = f"\n📂 Source  : <code>{j.get('source_name','—')[:30]}</code>" if j.get("source_name") else ""
    return (
        f"📊 {bold('Job Status')}\n{DIVIDER}\n"
        f"🆔 Job    : <code>{j['job_id'][:8]}</code>\n"
        f"{ic} Status : <b>{st}</b>\n"
        f"🔗 Links  : {cur}/{tot}  [{bar}] {pct}%\n"
        f"✅ Done   : {comp}\n❌ Failed : {fail}"
        f"{src}\n📅 Start  : {j['created_at'][:16]}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# /cancel  /stop
# ══════════════════════════════════════════════════════════════════════════════
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await guard(update, db): return
    uid = update.effective_user.id
    job = await db.get_active_job(uid)
    if not job:
        await update.message.reply_text("ℹ️ No active job.", reply_markup=back_main()); return
    await cancel_job(job["job_id"], db)
    await update.message.reply_text(
        f"⏹ Job <code>{job['job_id'][:8]}</code> cancelled.\nUse /resume to restart.",
        parse_mode=ParseMode.HTML, reply_markup=back_main(),
    )


# ══════════════════════════════════════════════════════════════════════════════
# /resume
# ══════════════════════════════════════════════════════════════════════════════
async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await guard(update, db): return
    uid = update.effective_user.id; chat_id = update.effective_chat.id
    job = await db.get_latest_job(uid)
    if not job or job["status"] not in ("paused",):
        await update.message.reply_text("ℹ️ No paused job to resume."); return
    if is_running(job["job_id"]) and await resume_in_place(job["job_id"]):
        await update.message.reply_text(f"▶️ Resumed job <code>{job['job_id'][:8]}</code>.",
                                         parse_mode=ParseMode.HTML); return
    urls = await db.get_links(job["job_id"])
    msg  = await update.message.reply_text(
        f"▶️ Resuming from link {job['current_index']+1}…", parse_mode=ParseMode.HTML)
    await resume_from_db(context.bot, db, job["job_id"], uid, chat_id, urls, msg.message_id)


# ══════════════════════════════════════════════════════════════════════════════
# /logs  /errors  /clear
# ══════════════════════════════════════════════════════════════════════════════
async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await guard(update, db): return
    rows = await db.get_logs(uid=update.effective_user.id, limit=25)
    if not rows:
        await update.message.reply_text("📭 No logs yet.", reply_markup=back_main()); return
    lines = [f"📋 {bold('Your Logs')}\n{DIVIDER}"]
    for r in reversed(rows):
        ic = {"INFO":"ℹ️","ERROR":"❌","WARNING":"⚠️"}.get(r["level"],"•")
        lines.append(f"{ic} <code>{r['created_at'][:16]}</code>  {r['message'][:70]}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=back_main())

async def cmd_errors(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await guard(update, db): return
    rows = await db.get_logs(uid=update.effective_user.id, level="ERROR", limit=20)
    if not rows:
        await update.message.reply_text("✅ No errors found.", reply_markup=back_main()); return
    lines = [f"❌ {bold('Error Logs')}\n{DIVIDER}"]
    for r in reversed(rows):
        lines.append(f"❌ <code>{r['created_at'][:16]}</code>  {r['message'][:70]}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=back_main())

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await guard(update, db): return
    n = await db.clear_logs(uid=update.effective_user.id)
    await update.message.reply_text(f"🗑️ Cleared {n} log entries.", reply_markup=back_main())


# ══════════════════════════════════════════════════════════════════════════════
# /history
# ══════════════════════════════════════════════════════════════════════════════
async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await guard(update, db): return
    jobs = await db.get_user_jobs(update.effective_user.id, limit=10)
    if not jobs:
        await update.message.reply_text("📭 No jobs yet.", reply_markup=back_main()); return
    ic = {"running":"⚡","completed":"✅","paused":"⏸","cancelled":"⏹","failed":"❌"}
    lines = [f"📁 {bold('Job History')}\n{DIVIDER}"]
    for j in jobs:
        lines.append(
            f"{ic.get(j['status'],'❓')} <code>{j['job_id'][:8]}</code>  "
            f"{j['current_index']}/{j['total_links']}  "
            f"✅{j['completed_links']} ❌{j['failed_links']}  "
            f"<i>{j['created_at'][:10]}</i>"
        )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=back_main())


# ══════════════════════════════════════════════════════════════════════════════
# /profile
# ══════════════════════════════════════════════════════════════════════════════
async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await guard(update, db): return
    u    = update.effective_user
    udb  = await db.get_user(u.id)
    adm  = await db.is_admin(u.id)
    if not udb:
        await update.message.reply_text("❌ Profile not found."); return

    try:
        png = generate_welcome_card(
            first_name = u.first_name or "User",
            username   = u.username or "",
            user_id    = u.id,
            joined     = udb.get("joined_at", datetime.utcnow().isoformat()),
            jobs       = udb.get("total_jobs", 0),
            files      = udb.get("total_files", 0),
            bytes_sent = udb.get("total_bytes", 0),
            is_admin   = adm,
        )
        if png:
            txt = (
                f"👤 {bold(u.first_name or 'User')}\n{DIVIDER}\n"
                f"🆔 ID       : <code>{u.id}</code>\n"
                f"👤 Username : @{u.username or '—'}\n"
                f"🛡️ Role     : {'Admin' if adm else 'Member'}\n"
                f"📅 Joined   : {udb.get('joined_at','?')[:10]}\n"
                f"📦 Jobs     : {udb.get('total_jobs',0)}\n"
                f"📄 Files    : {udb.get('total_files',0)}\n"
                f"💾 Data     : {fmt_bytes(udb.get('total_bytes',0))}"
            )
            await update.message.reply_photo(
                photo=io.BytesIO(png), caption=txt,
                parse_mode=ParseMode.HTML, reply_markup=back_main()
            )
            return
    except Exception as e:
        logger.warning("Profile card error: %s", e)

    await update.message.reply_text(
        f"👤 Profile — {bold(u.first_name or 'User')}\n"
        f"🆔 ID: <code>{u.id}</code>\n"
        f"📦 Jobs: {udb.get('total_jobs',0)}  📄 Files: {udb.get('total_files',0)}",
        parse_mode=ParseMode.HTML, reply_markup=back_main()
    )


# ══════════════════════════════════════════════════════════════════════════════
# /mystats
# ══════════════════════════════════════════════════════════════════════════════
async def cmd_mystats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await guard(update, db): return
    u   = update.effective_user
    udb = await db.get_user(u.id)
    if not udb:
        await update.message.reply_text("No stats yet."); return
    jobs = await db.get_user_jobs(u.id, 100)
    comp = sum(j["completed_links"] for j in jobs)
    fail = sum(j["failed_links"]    for j in jobs)
    await update.message.reply_text(
        f"📊 {bold('Your Statistics')}\n{DIVIDER}\n"
        f"📦 Total Jobs     : <b>{udb.get('total_jobs',0)}</b>\n"
        f"📄 Files Uploaded : <b>{udb.get('total_files',0)}</b>\n"
        f"💾 Data Sent      : <b>{fmt_bytes(udb.get('total_bytes',0))}</b>\n"
        f"✅ Links Done     : <b>{comp}</b>\n"
        f"❌ Links Failed   : <b>{fail}</b>\n"
        f"📅 Joined         : <b>{udb.get('joined_at','?')[:10]}</b>\n"
        f"👁️ Last Seen      : <b>{udb.get('last_seen','?')[:16]}</b>",
        parse_mode=ParseMode.HTML, reply_markup=back_main()
    )


# ══════════════════════════════════════════════════════════════════════════════
# /ping
# ══════════════════════════════════════════════════════════════════════════════
async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    import time
    t0 = time.monotonic()
    m  = await update.message.reply_text("🏓 Pinging…")
    ms = int((time.monotonic() - t0) * 1000)
    await m.edit_text(f"🏓 Pong!  Latency: <b>{ms} ms</b>", parse_mode=ParseMode.HTML)


# ══════════════════════════════════════════════════════════════════════════════
# /about
# ══════════════════════════════════════════════════════════════════════════════
async def cmd_about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await guard(update, db): return
    s = await db.get_stats()
    await update.message.reply_text(
        f"🤖 {bold(BOT_NAME)}\n{DIVIDER}\n"
        f"{TAGLINE}\n\n"
        f"📌 <b>Version</b>    : 3.0.0 Advanced\n"
        f"🐍 <b>Runtime</b>    : Python + python-telegram-bot 21\n"
        f"🔓 <b>DRM</b>        : yt-dlp · pikepdf · ClearKey\n"
        f"💾 <b>Database</b>   : SQLite (aiosqlite)\n"
        f"🌐 <b>API Server</b> : aiohttp REST panel\n\n"
        f"👥 Users: {s['total_users']}   📄 Files: {s['total_files']}",
        parse_mode=ParseMode.HTML, reply_markup=back_main()
    )


# ══════════════════════════════════════════════════════════════════════════════
# /version
# ══════════════════════════════════════════════════════════════════════════════
async def cmd_version(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await guard(update, db): return
    await update.message.reply_text(
        f"🔖 {bold('Version Info')}\n{DIVIDER}\n"
        f"Bot Version  : <code>3.0.0</code>\n"
        f"Release Date : <code>2024-12-01</code>\n"
        f"PTB Version  : <code>21.6</code>\n"
        f"yt-dlp       : latest\n"
        f"Python       : 3.10+",
        parse_mode=ParseMode.HTML, reply_markup=back_main()
    )


# ══════════════════════════════════════════════════════════════════════════════
# /check  — validate a single URL
# ══════════════════════════════════════════════════════════════════════════════
async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await guard(update, db): return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /check <url>"); return
    url = args[0]
    from .drm import classify, is_valid_url
    valid = is_valid_url(url)
    kind  = classify(url) if valid else "—"
    icon  = {"appx":"🔐","hls":"📺","dash":"📡","s3":"☁️","generic":"🌐"}.get(kind,"❓")
    await update.message.reply_text(
        f"🔍 {bold('URL Check')}\n{DIVIDER}\n"
        f"{'✅ Valid' if valid else '❌ Invalid'}\n"
        f"{icon} Type : <b>{kind}</b>\n"
        f"<code>{url[:100]}</code>",
        parse_mode=ParseMode.HTML,
    )


# ══════════════════════════════════════════════════════════════════════════════
# /quota
# ══════════════════════════════════════════════════════════════════════════════
async def cmd_quota(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await guard(update, db): return
    udb = await db.get_user(update.effective_user.id)
    if not udb: return
    await update.message.reply_text(
        f"📊 {bold('Your Quota')}\n{DIVIDER}\n"
        f"📄 Files uploaded : <b>{udb.get('total_files',0)}</b>\n"
        f"💾 Total data     : <b>{fmt_bytes(udb.get('total_bytes',0))}</b>\n"
        f"📦 Jobs submitted : <b>{udb.get('total_jobs',0)}</b>",
        parse_mode=ParseMode.HTML, reply_markup=back_main()
    )


# ══════════════════════════════════════════════════════════════════════════════
# /export  — export failed links of latest job
# ══════════════════════════════════════════════════════════════════════════════
async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await guard(update, db): return
    uid = update.effective_user.id
    job = await db.get_latest_job(uid)
    if not job:
        await update.message.reply_text("No jobs found."); return
    failed = await db.get_failed_links(job["job_id"])
    if not failed:
        await update.message.reply_text("✅ No failed links in last job."); return
    content = "\n".join(r["url"] for r in failed)
    buf     = io.BytesIO(content.encode())
    buf.name = f"failed_{job['job_id'][:8]}.txt"
    await update.message.reply_document(
        document=buf, filename=buf.name,
        caption=f"❌ {len(failed)} failed link(s) from job <code>{job['job_id'][:8]}</code>",
        parse_mode=ParseMode.HTML,
    )


# ══════════════════════════════════════════════════════════════════════════════
# /svg  — send SVG source of profile card
# ══════════════════════════════════════════════════════════════════════════════
async def cmd_svg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await guard(update, db): return
    uid = update.effective_user.id
    svg = get_svg_source(uid)
    if not svg:
        await update.message.reply_text(
            "No SVG found. Use /profile first to generate your card."); return
    buf      = io.BytesIO(svg.encode())
    buf.name = f"welcome_card_{uid}.svg"
    await update.message.reply_document(
        document=buf, filename=buf.name,
        caption="🎨 Your welcome card SVG source file.",
    )


# ══════════════════════════════════════════════════════════════════════════════
# /support
# ══════════════════════════════════════════════════════════════════════════════
async def cmd_support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await guard(update, db): return
    from config.settings import SUPPORT_USERNAME
    await update.message.reply_text(
        f"🆘 {bold('Support')}\n{DIVIDER}\n"
        + (f"📩 Contact: @{SUPPORT_USERNAME}\n" if SUPPORT_USERNAME else "")
        + "📖 Use /help for documentation\n"
          "🐛 Report bugs using /feedback",
        parse_mode=ParseMode.HTML, reply_markup=back_main()
    )


# ══════════════════════════════════════════════════════════════════════════════
# /settings  — show settings menu
# ══════════════════════════════════════════════════════════════════════════════
async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await guard(update, db): return
    await update.message.reply_text(
        f"⚙️ {bold('Settings')}\n{DIVIDER}\n"
        "Configure AppX credentials, DRM keys, proxy, and notifications.",
        parse_mode=ParseMode.HTML, reply_markup=settings_menu()
    )


# ══════════════════════════════════════════════════════════════════════════════
# /notify  /language
# ══════════════════════════════════════════════════════════════════════════════
async def cmd_notify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await guard(update, db): return
    udb = await db.get_user(update.effective_user.id)
    cur = bool(udb.get("notify_done", 1)) if udb else True
    await update.message.reply_text(
        f"🔔 Notifications are currently <b>{'ON' if cur else 'OFF'}</b>.",
        parse_mode=ParseMode.HTML, reply_markup=notify_kb(cur)
    )

async def cmd_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await guard(update, db): return
    await update.message.reply_text(
        "🌐 Select your language:", reply_markup=lang_kb()
    )


# ══════════════════════════════════════════════════════════════════════════════
# /feedback  conversation
# ══════════════════════════════════════════════════════════════════════════════
async def cmd_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db: Database = context.bot_data["db"]
    if not await guard(update, db): return ConversationHandler.END
    await update.message.reply_text(
        "📝 Type your feedback message (or /cancel to abort):"
    )
    return WAIT_FEEDBACK

async def _feedback_recv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db: Database = context.bot_data["db"]
    text = (update.message.text or "").strip()
    uid  = update.effective_user.id
    await db.add_log("INFO", f"FEEDBACK from {uid}: {text[:200]}", uid)
    for adm_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=adm_id,
                text=f"📩 Feedback from <code>{uid}</code>:\n{text[:500]}",
                parse_mode=ParseMode.HTML,
            )
        except Exception: pass
    await update.message.reply_text(
        "✅ Feedback sent to admins. Thank you!", reply_markup=back_main()
    )
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
# /login  /cookie  /keys  /proxy  — settings conversations
# ══════════════════════════════════════════════════════════════════════════════
async def cmd_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db: Database = context.bot_data["db"]
    if not await guard(update, db): return ConversationHandler.END
    await update.message.reply_text("🔑 Enter AppX <b>email</b>:", parse_mode=ParseMode.HTML)
    return WAIT_LOGIN_EMAIL

async def _login_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["appx_email"] = (update.message.text or "").strip()
    await update.message.reply_text("🔒 Enter AppX <b>password</b>:", parse_mode=ParseMode.HTML)
    return WAIT_LOGIN_PASS

async def _login_pass(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db: Database = context.bot_data["db"]
    email = context.user_data.pop("appx_email", "")
    pwd   = (update.message.text or "").strip()
    m     = await update.message.reply_text("⏳ Logging in…")
    import aiohttp
    async with aiohttp.ClientSession() as s:
        cookie = await appx_login(s, email, pwd)
    if cookie:
        await db.save_cookie(cookie, email, "auto-login", update.effective_user.id)
        await m.edit_text("✅ Login successful! Cookie saved.", reply_markup=back_main())
    else:
        await m.edit_text("❌ Login failed. Check credentials.", reply_markup=settings_menu())
    return ConversationHandler.END

async def cmd_cookie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db: Database = context.bot_data["db"]
    if not await guard(update, db): return ConversationHandler.END
    await update.message.reply_text(
        "🍪 Paste your AppX cookie string:\n"
        "Example: <code>token=eyJhbGciOi…</code>",
        parse_mode=ParseMode.HTML
    )
    return WAIT_COOKIE

async def _cookie_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db: Database = context.bot_data["db"]
    c = (update.message.text or "").strip()
    if len(c) < 10:
        await update.message.reply_text("⚠️ Invalid cookie. Try again."); return WAIT_COOKIE
    await db.save_cookie(c, "manual", "manual", update.effective_user.id)
    await update.message.reply_text("✅ Cookie saved!", reply_markup=back_main())
    return ConversationHandler.END

async def cmd_keys(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db: Database = context.bot_data["db"]
    if not await guard(update, db): return ConversationHandler.END
    await update.message.reply_text(
        "🔐 Enter DRM KID:KEY pairs (one per line):\n"
        "Example: <code>abc123:def456</code>",
        parse_mode=ParseMode.HTML
    )
    return WAIT_DRM_KEYS

async def _keys_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db: Database = context.bot_data["db"]
    uid  = update.effective_user.id
    text = (update.message.text or "").strip()
    n    = 0
    for line in text.splitlines():
        line = line.strip()
        if ":" in line:
            k, v = line.split(":", 1)
            await db.add_drm_key(k.strip(), v.strip(), "user", uid)
            n += 1
    await update.message.reply_text(
        f"✅ {n} DRM key(s) saved!", reply_markup=back_main()
    )
    return ConversationHandler.END

async def cmd_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db: Database = context.bot_data["db"]
    if not await guard(update, db): return ConversationHandler.END
    await update.message.reply_text(
        "📡 Enter HTTP proxy URL:\n"
        "Example: <code>http://user:pass@host:port</code>",
        parse_mode=ParseMode.HTML
    )
    return WAIT_PROXY

async def _proxy_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db: Database = context.bot_data["db"]
    proxy = (update.message.text or "").strip()
    await db.set_config("http_proxy", proxy)
    from config import settings
    settings.HTTP_PROXY = proxy or None
    await update.message.reply_text(
        f"✅ Proxy set: <code>{proxy}</code>", parse_mode=ParseMode.HTML, reply_markup=back_main()
    )
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
# TXT file upload conversation
# ══════════════════════════════════════════════════════════════════════════════
async def handle_doc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db: Database = context.bot_data["db"]
    if not await guard(update, db): return ConversationHandler.END
    doc = update.message.document
    if not doc or not (doc.file_name or "").lower().endswith(".txt"):
        await update.message.reply_text("⚠️ Please send a <b>.txt</b> file.", parse_mode=ParseMode.HTML)
        return ConversationHandler.END
    uid = update.effective_user.id
    if await db.get_active_job(uid):
        await update.message.reply_text("⚠️ You have an active job. Use /cancel first."); return ConversationHandler.END

    try:
        f    = await doc.get_file()
        tmp  = os.path.join(TEMP_DIR, f"txt_{uid}_{doc.file_id}.txt")
        os.makedirs(TEMP_DIR, exist_ok=True)
        await f.download_to_drive(tmp)
    except Exception as e:
        await update.message.reply_text(f"❌ Download failed: {e}"); return ConversationHandler.END

    try:
        with open(tmp, "r", encoding="utf-8", errors="ignore") as fh:
            raw = [l.strip() for l in fh]
        os.remove(tmp)
    except Exception as e:
        await update.message.reply_text(f"❌ Read failed: {e}"); return ConversationHandler.END

    urls = [l for l in raw if l and not l.startswith("#") and is_valid_url(l)]
    skipped = len([l for l in raw if l and not l.startswith("#") and not is_valid_url(l)])
    if not urls:
        await update.message.reply_text("⚠️ No valid URLs found in the file."); return ConversationHandler.END

    context.user_data["pending_urls"]  = urls
    context.user_data["source_name"]   = doc.file_name or "upload.txt"

    note = f"  _(⚠️ {skipped} invalid lines skipped)_" if skipped else ""
    await update.message.reply_text(
        f"📂 {bold('File Parsed!')}\n{DIVIDER}\n"
        f"🔗 Valid URLs: <b>{len(urls)}</b>{note}\n\n"
        f"📍 {bold('Choose starting link:')}",
        parse_mode=ParseMode.HTML,
        reply_markup=start_index_kb(len(urls)),
    )
    return WAIT_IDX


async def _idx_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q    = update.callback_query; await q.answer()
    data = q.data
    if data == "sidx:cancel":
        context.user_data.pop("pending_urls", None)
        await q.edit_message_text("❌ Cancelled.", reply_markup=back_main())
        return ConversationHandler.END
    if data == "sidx:custom":
        urls = context.user_data.get("pending_urls", [])
        await q.edit_message_text(f"✏️ Enter a number (1–{len(urls)}):")
        return WAIT_CUSTOM_IDX
    try:
        n = int(data.split(":")[1])
    except Exception:
        await q.answer("Invalid.", show_alert=True); return WAIT_IDX
    return await _launch(update, context, n)


async def _custom_idx(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    urls = context.user_data.get("pending_urls", [])
    try:
        n = int((update.message.text or "").strip())
        if n < 1 or n > len(urls): raise ValueError()
    except ValueError:
        await update.message.reply_text(f"⚠️ Enter 1–{len(urls)}."); return WAIT_CUSTOM_IDX
    return await _launch(update, context, n)


async def _launch(update: Update, context: ContextTypes.DEFAULT_TYPE, start: int) -> int:
    db: Database = context.bot_data["db"]
    urls = context.user_data.pop("pending_urls", [])
    src  = context.user_data.pop("source_name", "")
    uid  = update.effective_user.id; chat_id = update.effective_chat.id
    if update.callback_query:
        msg = await update.callback_query.edit_message_text(
            f"🚀 {bold(f'Starting from link {start}…')}", parse_mode=ParseMode.HTML)
    else:
        msg = await update.message.reply_text(
            f"🚀 {bold(f'Starting from link {start}…')}", parse_mode=ParseMode.HTML)
    await start_job(context.bot, db, uid, chat_id, urls, start-1, msg.message_id, src)
    return ConversationHandler.END

async def _cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("pending_urls", None)
    if update.message: await update.message.reply_text("❌ Cancelled.", reply_markup=back_main())
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
# Inline callback router
# ══════════════════════════════════════════════════════════════════════════════
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; data = q.data or ""
    db: Database = context.bot_data["db"]
    if not await guard(update, db): return
    uid     = update.effective_user.id
    chat_id = update.effective_chat.id
    is_adm  = await db.is_admin(uid)

    # ── navigation ───────────────────────────────────────────────────────────
    if data == "main_menu":
        await q.answer()
        await q.edit_message_text(
            _welcome(update.effective_user.first_name or "there", is_adm),
            parse_mode=ParseMode.HTML, reply_markup=main_menu(is_adm)
        )
    elif data == "help":
        await q.answer()
        await q.edit_message_text(HELP, parse_mode=ParseMode.HTML, reply_markup=back_main())
    elif data == "guide_upload":
        await q.answer()
        await q.edit_message_text(
            f"📤 {bold('Upload Guide')}\n{DIVIDER}\n"
            "1. Create a <code>.txt</code> file\n"
            "2. One URL per line (https://…)\n"
            "3. Lines starting with <code>#</code> are ignored\n"
            "4. Send the file to this chat\n"
            "5. Choose starting link number\n"
            "6. Use inline buttons: ⏸ Pause ▶️ Resume ⏹ Cancel",
            parse_mode=ParseMode.HTML, reply_markup=back_main()
        )
    elif data == "settings":
        await q.answer()
        await q.edit_message_text(
            f"⚙️ {bold('Settings')}", parse_mode=ParseMode.HTML, reply_markup=settings_menu()
        )
    elif data == "my_status":
        await q.answer()
        job = await db.get_active_job(uid) or await db.get_latest_job(uid)
        if not job:
            await q.edit_message_text("ℹ️ No jobs yet.", reply_markup=back_main()); return
        await q.edit_message_text(
            _job_txt(job), parse_mode=ParseMode.HTML,
            reply_markup=job_controls(job["job_id"], paused=job["status"]=="paused")
        )
    elif data == "my_logs":
        await q.answer()
        rows = await db.get_logs(uid=uid, limit=20)
        if not rows:
            await q.edit_message_text("📭 No logs.", reply_markup=back_main()); return
        lines = [f"📋 {bold('Your Logs')}\n{DIVIDER}"]
        for r in reversed(rows):
            ic = {"INFO":"ℹ️","ERROR":"❌","WARNING":"⚠️"}.get(r["level"],"•")
            lines.append(f"{ic} <code>{r['created_at'][:16]}</code>  {r['message'][:60]}")
        await q.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=back_main())
    elif data == "my_history":
        await q.answer()
        jobs = await db.get_user_jobs(uid, 8)
        if not jobs:
            await q.edit_message_text("No history.", reply_markup=back_main()); return
        ic = {"running":"⚡","completed":"✅","paused":"⏸","cancelled":"⏹"}
        lines = [f"📁 {bold('History')}\n{DIVIDER}"]
        for j in jobs:
            lines.append(f"{ic.get(j['status'],'❓')} <code>{j['job_id'][:8]}</code>"
                         f"  {j['current_index']}/{j['total_links']}"
                         f"  <i>{j['created_at'][:10]}</i>")
        await q.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=back_main())
    elif data == "my_profile":
        await q.answer("Sending profile card…")
        udb = await db.get_user(uid)
        if udb:
            u = update.effective_user
            try:
                png = generate_welcome_card(
                    u.first_name or "User", u.username or "", uid,
                    udb.get("joined_at", ""), udb.get("total_jobs", 0),
                    udb.get("total_files", 0), udb.get("total_bytes", 0), is_adm
                )
                if png:
                    await context.bot.send_photo(
                        chat_id=chat_id, photo=io.BytesIO(png),
                        caption=f"👤 {bold(u.first_name or 'User')}",
                        parse_mode=ParseMode.HTML, reply_markup=back_main()
                    )
                    return
            except Exception: pass
        await q.edit_message_text("❌ Could not generate card.", reply_markup=back_main())
    elif data == "bot_info":
        await q.answer()
        s = await db.get_stats()
        await q.edit_message_text(
            f"📡 {bold('Bot Info')}\n{DIVIDER}\n"
            f"🤖 {BOT_NAME} v3.0\n"
            f"👥 Users: {s['total_users']}\n"
            f"📄 Files: {s['total_files']}\n"
            f"🔑 DRM Keys: {s['drm_keys']}",
            parse_mode=ParseMode.HTML, reply_markup=back_main()
        )

    # ── job controls ─────────────────────────────────────────────────────────
    elif data.startswith("pause:"):
        jid = data.split(":",1)[1]
        await pause_job(jid); await q.answer("⏸ Pausing…")
    elif data.startswith("resume:"):
        jid = data.split(":",1)[1]
        if is_running(jid) and await resume_in_place(jid):
            await q.answer("▶️ Resumed!")
        else:
            job  = await db.get_job(jid)
            if not job: await q.answer("Job not found.", show_alert=True); return
            urls = await db.get_links(jid)
            await resume_from_db(context.bot, db, jid, job["user_id"], job["chat_id"], urls, job["progress_msg_id"])
            await q.answer("▶️ Resumed!")
    elif data.startswith("cancel:"):
        jid = data.split(":",1)[1]
        await cancel_job(jid, db); await q.answer("⏹ Cancelling…")
    elif data.startswith("status:"):
        jid = data.split(":",1)[1]
        job = await db.get_job(jid)
        if not job: await q.answer("Not found.", show_alert=True); return
        await q.answer()
        try:
            await q.edit_message_text(
                _job_txt(job), parse_mode=ParseMode.HTML,
                reply_markup=job_controls(jid, paused=job["status"]=="paused")
            )
        except Exception: pass
    elif data.startswith("logs:"):
        jid  = data.split(":",1)[1]
        await q.answer()
        rows = await db.get_logs(job_id=jid, limit=20)
        lines = [f"📋 {bold('Job Logs')}\n{DIVIDER}"]
        for r in reversed(rows):
            ic = {"INFO":"ℹ️","ERROR":"❌"}.get(r["level"],"•")
            lines.append(f"{ic} {r['message'][:80]}")
        await q.edit_message_text(
            "\n".join(lines) if len(lines)>1 else "No logs.",
            parse_mode=ParseMode.HTML, reply_markup=job_controls(jid)
        )
    elif data.startswith("report:"):
        jid    = data.split(":",1)[1]
        await q.answer("Generating report…")
        job    = await db.get_job(jid)
        failed = await db.get_failed_links(jid) if job else []
        lines  = [f"📊 Job Report: {jid[:8]}", f"Total: {job['total_links'] if job else '?'}",
                  f"Done: {job['completed_links'] if job else '?'}",
                  f"Failed: {len(failed)}", "", "# Failed URLs:"]
        for r in failed: lines.append(r["url"])
        buf      = io.BytesIO("\n".join(lines).encode())
        buf.name = f"report_{jid[:8]}.txt"
        await context.bot.send_document(
            chat_id=chat_id, document=buf, filename=buf.name,
            caption=f"📊 Job report for <code>{jid[:8]}</code>",
            parse_mode=ParseMode.HTML
        )
    elif data.startswith("retry_failed:"):
        await q.answer("Retry not yet implemented — export and re-upload the failed list.", show_alert=True)

    # ── settings callbacks ────────────────────────────────────────────────────
    elif data == "cfg_notify":
        udb = await db.get_user(uid)
        cur = bool(udb.get("notify_done",1)) if udb else True
        await q.answer()
        await q.edit_message_text(
            f"🔔 Notifications: <b>{'ON' if cur else 'OFF'}</b>",
            parse_mode=ParseMode.HTML, reply_markup=notify_kb(cur)
        )
    elif data.startswith("notify:"):
        val = data.split(":")[1] == "on"
        await db.set_notify(uid, val)
        await q.answer(f"🔔 Notifications {'enabled' if val else 'disabled'}.")
        await q.edit_message_text(
            f"🔔 Notifications: <b>{'ON' if val else 'OFF'}</b>",
            parse_mode=ParseMode.HTML, reply_markup=notify_kb(val)
        )
    elif data == "cfg_lang":
        await q.answer()
        await q.edit_message_text("🌐 Select language:", reply_markup=lang_kb())
    elif data.startswith("lang:"):
        lang = data.split(":")[1]
        await db.set_user_language(uid, lang)
        await q.answer(f"Language set to {lang}.")
        await q.edit_message_text("✅ Language updated.", reply_markup=back_main())

    # ── admin panel ───────────────────────────────────────────────────────────
    elif data == "admin_panel":
        if not is_adm: await q.answer("⛔ Admin only.", show_alert=True); return
        await q.answer()
        await q.edit_message_text(
            f"🛡️ {bold('Admin Panel')}", parse_mode=ParseMode.HTML, reply_markup=admin_menu()
        )
    elif data == "adm_stats":
        if not is_adm: await q.answer("⛔", show_alert=True); return
        await q.answer(); s = await db.get_stats()
        await q.edit_message_text(
            f"📊 {bold('Statistics')}\n{DIVIDER}\n"
            f"👥 Users   : {s['total_users']} (banned: {s['banned_users']})\n"
            f"🛡️ Admins  : {s['admin_users']}\n"
            f"📦 Jobs    : {s['total_jobs']} (running: {s['running_jobs']}, paused: {s['paused_jobs']})\n"
            f"✅ Done    : {s['done_jobs']}\n"
            f"📄 Files   : {s['total_files']}\n"
            f"❌ Failed  : {s['total_failed']}\n"
            f"💾 Data    : {fmt_bytes(s['total_bytes'])}\n"
            f"🔑 DRM Keys: {s['drm_keys']}",
            parse_mode=ParseMode.HTML, reply_markup=admin_menu()
        )
    elif data in ("adm_users","adm_banned","adm_admins"):
        if not is_adm: await q.answer("⛔", show_alert=True); return
        await q.answer()
        await q.edit_message_text(
            f"👥 {bold('User Management')}", parse_mode=ParseMode.HTML, reply_markup=users_menu()
        )
    elif data == "adm_jobs":
        if not is_adm: await q.answer("⛔", show_alert=True); return
        await q.answer(); jobs = await db.get_all_jobs(15)
        ic = {"running":"⚡","completed":"✅","paused":"⏸","cancelled":"⏹"}
        lines = [f"📦 {bold('Recent Jobs')}\n{DIVIDER}"]
        for j in jobs:
            lines.append(f"{ic.get(j['status'],'❓')} <code>{j['job_id'][:8]}</code>"
                         f"  u={j['user_id']}  {j['current_index']}/{j['total_links']}"
                         f"  ✅{j['completed_links']} ❌{j['failed_links']}")
        await q.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=admin_menu())
    elif data == "adm_drm":
        if not is_adm: await q.answer("⛔", show_alert=True); return
        await q.answer()
        await q.edit_message_text(f"🔑 {bold('DRM Keys')}", parse_mode=ParseMode.HTML, reply_markup=drm_menu())
    elif data == "adm_drm_list":
        if not is_adm: await q.answer("⛔", show_alert=True); return
        await q.answer(); keys = await db.list_drm_keys()
        if not keys:
            await q.edit_message_text("No DRM keys.", reply_markup=drm_menu()); return
        lines = [f"🔑 {bold('DRM Keys')}\n{DIVIDER}"]
        for k in keys:
            lines.append(f"• <code>{k['kid'][:16]}…</code>  {k.get('label','')}")
        await q.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=drm_menu())
    elif data == "adm_cookies":
        if not is_adm: await q.answer("⛔", show_alert=True); return
        await q.answer(); cks = await db.list_cookies()
        lines = [f"🍪 {bold('Cookies')}\n{DIVIDER}"]
        for c in cks:
            act = "✅" if c["active"] else "○"
            lines.append(f"{act} <code>{c['email'] or 'manual'}</code>  {c.get('label','')}  {c['created_at'][:10]}")
        await q.edit_message_text("\n".join(lines) if len(lines)>1 else "No cookies.",
                                   parse_mode=ParseMode.HTML, reply_markup=admin_menu())
    elif data == "adm_logs":
        if not is_adm: await q.answer("⛔", show_alert=True); return
        await q.answer(); rows = await db.get_logs(limit=25)
        lines = [f"📋 {bold('Global Logs')}\n{DIVIDER}"]
        for r in reversed(rows):
            ic = {"INFO":"ℹ️","ERROR":"❌","WARNING":"⚠️"}.get(r["level"],"•")
            lines.append(f"{ic} <code>{r['created_at'][:16]}</code> u={r.get('user_id','?')}  {r['message'][:50]}")
        await q.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=admin_menu())
    elif data == "adm_config":
        if not is_adm: await q.answer("⛔", show_alert=True); return
        await q.answer()
        await q.edit_message_text(f"⚙️ {bold('Bot Config')}", parse_mode=ParseMode.HTML, reply_markup=config_menu())
    elif data == "adm_toggle_maint":
        if not is_adm: await q.answer("⛔", show_alert=True); return
        from config import settings as cfg
        cfg.MAINTENANCE_MODE = not cfg.MAINTENANCE_MODE
        val = "ON" if cfg.MAINTENANCE_MODE else "OFF"
        await db.set_config("maintenance_mode", str(cfg.MAINTENANCE_MODE).lower())
        await q.answer(f"🔧 Maintenance {val}.")
        await q.edit_message_text(f"🔧 Maintenance mode: <b>{val}</b>",
                                   parse_mode=ParseMode.HTML, reply_markup=config_menu())
    elif data == "adm_toggle_adminonly":
        if not is_adm: await q.answer("⛔", show_alert=True); return
        from config import settings as cfg
        cfg.ADMIN_ONLY_MODE = not cfg.ADMIN_ONLY_MODE
        val = "ON" if cfg.ADMIN_ONLY_MODE else "OFF"
        await db.set_config("admin_only_mode", str(cfg.ADMIN_ONLY_MODE).lower())
        await q.answer(f"🔒 Admin-only mode {val}.")
        await q.edit_message_text(f"🔒 Admin-only mode: <b>{val}</b>",
                                   parse_mode=ParseMode.HTML, reply_markup=config_menu())
    elif data == "adm_view_config":
        if not is_adm: await q.answer("⛔", show_alert=True); return
        await q.answer(); cfg_data = await db.get_all_config()
        lines = [f"⚙️ {bold('Current Config')}\n{DIVIDER}"]
        for k, v in cfg_data.items():
            lines.append(f"• <code>{k}</code> = <code>{v}</code>")
        if not cfg_data: lines.append("No config entries.")
        await q.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=config_menu())
    elif data == "adm_reload":
        if not is_adm: await q.answer("⛔", show_alert=True); return
        # Re-apply DB config to runtime settings
        from config import settings as cfg
        proxy = await db.get_config("http_proxy", "")
        cfg.HTTP_PROXY = proxy or None
        maint = await db.get_config("maintenance_mode", "false")
        cfg.MAINTENANCE_MODE = maint.lower() == "true"
        await q.answer("✅ Settings reloaded.")
    else:
        await q.answer()
