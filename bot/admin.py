"""
Admin-only command handlers.
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from database.db import Database
from config.settings import ADMIN_IDS

logger = logging.getLogger(__name__)


def is_admin(user_id: int, db_admin: bool = False) -> bool:
    return user_id in ADMIN_IDS or db_admin


async def _require_admin(update: Update, db: Database) -> bool:
    user = update.effective_user
    if not user:
        return False
    record = await db.get_user(user.id)
    if user.id not in ADMIN_IDS and not (record and record.get("is_admin")):
        await update.message.reply_text("⛔ Admin only command.")
        return False
    return True


# ---------------------------------------------------------------------------
# /admin
# ---------------------------------------------------------------------------

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _require_admin(update, db):
        return

    text = (
        "🛡️ <b>Admin Panel</b>\n\n"
        "<b>User Management</b>\n"
        "/ban &lt;user_id&gt;      — Ban a user\n"
        "/unban &lt;user_id&gt;    — Unban a user\n"
        "/addadmin &lt;user_id&gt; — Grant admin rights\n"
        "/users              — List all users\n\n"
        "<b>Job Management</b>\n"
        "/jobs               — List recent jobs\n"
        "/killjob &lt;job_id&gt;  — Force-cancel a job\n\n"
        "<b>System</b>\n"
        "/stats              — Bot statistics\n"
        "/broadcast &lt;msg&gt;  — Broadcast to all users\n"
        "/alllogs            — Recent global logs"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /stats
# ---------------------------------------------------------------------------

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _require_admin(update, db):
        return

    s = await db.get_stats()
    text = (
        "📊 <b>Bot Statistics</b>\n\n"
        f"👤 Total users    : <b>{s['total_users']}</b>\n"
        f"✅ Active users   : <b>{s['active_users']}</b>\n"
        f"📦 Total jobs     : <b>{s['total_jobs']}</b>\n"
        f"⚡ Running jobs   : <b>{s['running_jobs']}</b>\n"
        f"📄 Files sent     : <b>{s['total_files']}</b>\n"
        f"❌ Total failures : <b>{s['total_failed']}</b>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /users
# ---------------------------------------------------------------------------

async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _require_admin(update, db):
        return

    users = await db.get_all_users()
    if not users:
        await update.message.reply_text("No users yet.")
        return

    lines = ["👥 <b>All Users</b>\n"]
    for u in users[:30]:
        name = u.get("first_name", "") or ""
        uname = f"@{u['username']}" if u.get("username") else "no username"
        banned = "🚫" if u.get("is_banned") else "✅"
        admin  = "🛡️" if u.get("is_admin") else ""
        lines.append(
            f"{banned}{admin} <code>{u['user_id']}</code>  {name}  {uname}  "
            f"jobs={u.get('total_jobs',0)} files={u.get('total_files',0)}"
        )
    if len(users) > 30:
        lines.append(f"\n… and {len(users) - 30} more")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /ban  /unban
# ---------------------------------------------------------------------------

async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _require_admin(update, db):
        return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /ban <user_id>")
        return
    uid = int(args[0])
    await db.ban_user(uid)
    await update.message.reply_text(f"🚫 User <code>{uid}</code> has been banned.", parse_mode=ParseMode.HTML)


async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _require_admin(update, db):
        return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /unban <user_id>")
        return
    uid = int(args[0])
    await db.unban_user(uid)
    await update.message.reply_text(f"✅ User <code>{uid}</code> has been unbanned.", parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /addadmin
# ---------------------------------------------------------------------------

async def cmd_addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _require_admin(update, db):
        return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /addadmin <user_id>")
        return
    uid = int(args[0])
    await db.set_admin(uid, True)
    await update.message.reply_text(f"🛡️ User <code>{uid}</code> granted admin rights.", parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /jobs
# ---------------------------------------------------------------------------

async def cmd_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _require_admin(update, db):
        return

    jobs = await db.get_all_jobs(limit=20)
    if not jobs:
        await update.message.reply_text("No jobs found.")
        return

    lines = ["📦 <b>Recent Jobs</b>\n"]
    for j in jobs:
        icon = {"running": "⚡", "completed": "✅", "paused": "⏸", "failed": "❌"}.get(j["status"], "❓")
        lines.append(
            f"{icon} <code>{j['job_id'][:8]}</code>  uid={j['user_id']}  "
            f"{j['current_index']}/{j['total_links']}  ✅{j['completed_links']} ❌{j['failed_links']}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /killjob
# ---------------------------------------------------------------------------

async def cmd_killjob(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _require_admin(update, db):
        return

    from .queue_manager import _cancel_events
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /killjob <job_id_prefix>")
        return

    prefix = args[0].lower()
    matched = [jid for jid in list(_cancel_events.keys()) if jid.lower().startswith(prefix)]
    if not matched:
        await update.message.reply_text(f"No running job matches prefix '{prefix}'.")
        return

    for jid in matched:
        from .queue_manager import cancel_job
        await cancel_job(jid, db)
    await update.message.reply_text(
        f"⏸ Cancelled {len(matched)} job(s): " + ", ".join(j[:8] for j in matched)
    )


# ---------------------------------------------------------------------------
# /alllogs
# ---------------------------------------------------------------------------

async def cmd_alllogs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _require_admin(update, db):
        return

    logs = await db.get_logs(limit=30)
    if not logs:
        await update.message.reply_text("No logs.")
        return

    lines = ["📋 <b>Global Logs</b> (last 30)\n"]
    for log in reversed(logs):
        icon = {"INFO": "ℹ️", "ERROR": "❌", "WARNING": "⚠️"}.get(log["level"], "📌")
        ts = log["created_at"][:16]
        lines.append(f"{icon} <code>{ts}</code> uid={log.get('user_id','-')}  {log['message'][:60]}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /broadcast
# ---------------------------------------------------------------------------

async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _require_admin(update, db):
        return

    if not context.args:
        await update.message.reply_text("Usage: /broadcast <message text>")
        return

    message = " ".join(context.args)
    users = await db.get_all_users()
    sent = 0
    failed = 0

    status_msg = await update.message.reply_text(f"📡 Broadcasting to {len(users)} users…")

    for user in users:
        if user.get("is_banned"):
            continue
        try:
            await context.bot.send_message(
                chat_id=user["user_id"],
                text=f"📢 <b>Broadcast</b>\n\n{message}",
                parse_mode=ParseMode.HTML,
            )
            sent += 1
        except Exception:
            failed += 1

    await status_msg.edit_text(
        f"✅ Broadcast complete!\nSent: {sent}  Failed: {failed}",
        parse_mode=ParseMode.HTML,
    )
