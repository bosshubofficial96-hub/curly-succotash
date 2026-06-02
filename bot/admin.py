"""
Admin-only commands — 30+ commands for full bot management.
"""

import io
import json
import logging
from datetime import datetime

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CommandHandler, ContextTypes

from config.settings import ADMIN_IDS
from database.db import Database
from .fonts import DIVIDER, bold
from .keyboards import admin_menu, back_admin, back_main, config_menu, drm_menu, users_menu
from .utils import fmt_bytes

logger = logging.getLogger(__name__)


async def _req(update: Update, db: Database) -> bool:
    uid = update.effective_user.id if update.effective_user else 0
    if not await db.is_admin(uid):
        await update.message.reply_text("⛔ Admin only command.")
        return False
    return True


# ── /admin ─────────────────────────────────────────────────────────────────────
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    await update.message.reply_text(
        f"🛡️ {bold('Admin Panel')}\n{DIVIDER}\n"
        "Full administrative control panel.",
        parse_mode=ParseMode.HTML, reply_markup=admin_menu()
    )


# ── /stats ─────────────────────────────────────────────────────────────────────
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    s = await db.get_stats()
    await update.message.reply_text(
        f"📊 {bold('Bot Statistics')}\n{DIVIDER}\n"
        f"👥 Total users   : <b>{s['total_users']}</b>  (active: {s['active_users']})\n"
        f"🚫 Banned        : <b>{s['banned_users']}</b>\n"
        f"🛡️ Admins        : <b>{s['admin_users']}</b>\n"
        f"📦 Total jobs    : <b>{s['total_jobs']}</b>\n"
        f"⚡ Running       : <b>{s['running_jobs']}</b>  Paused: {s['paused_jobs']}\n"
        f"✅ Completed     : <b>{s['done_jobs']}</b>  Cancelled: {s['cancelled_jobs']}\n"
        f"📄 Files sent    : <b>{s['total_files']}</b>\n"
        f"❌ Total failed  : <b>{s['total_failed']}</b>\n"
        f"💾 Data sent     : <b>{fmt_bytes(s['total_bytes'])}</b>\n"
        f"🔑 DRM keys      : <b>{s['drm_keys']}</b>\n"
        f"📋 Log entries   : <b>{s['total_logs']}</b>",
        parse_mode=ParseMode.HTML, reply_markup=back_admin()
    )


# ── /users ─────────────────────────────────────────────────────────────────────
async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    users = await db.get_all_users(limit=25)
    lines = [f"👥 {bold('All Users')} ({len(users)})\n{DIVIDER}"]
    for u in users:
        bk  = "🚫" if u.get("is_banned") else "✅"
        ad  = "🛡️" if u.get("is_admin")  else ""
        lines.append(
            f"{bk}{ad} <code>{u['user_id']}</code>  {u.get('first_name','')}"
            f"  @{u.get('username','—')}  j={u.get('total_jobs',0)} f={u.get('total_files',0)}"
        )
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=users_menu()
    )


# ── /ban  /unban ───────────────────────────────────────────────────────────────
async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /ban <user_id>"); return
    uid = int(args[0])
    reason = " ".join(args[1:]) if len(args) > 1 else "No reason given"
    await db.ban_user(uid)
    await db.add_log("WARNING", f"User {uid} banned by admin. Reason: {reason}",
                      update.effective_user.id)
    await update.message.reply_text(
        f"🚫 User <code>{uid}</code> banned.\nReason: {reason}",
        parse_mode=ParseMode.HTML, reply_markup=back_admin()
    )

async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /unban <user_id>"); return
    await db.unban_user(int(args[0]))
    await update.message.reply_text(
        f"✅ User <code>{args[0]}</code> unbanned.",
        parse_mode=ParseMode.HTML, reply_markup=back_admin()
    )


# ── /addadmin  /removeadmin ────────────────────────────────────────────────────
async def cmd_addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /addadmin <user_id>"); return
    uid = int(args[0])
    await db.set_admin(uid, True)
    await update.message.reply_text(
        f"🛡️ User <code>{uid}</code> is now an admin.",
        parse_mode=ParseMode.HTML, reply_markup=back_admin()
    )

async def cmd_removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /removeadmin <user_id>"); return
    uid = int(args[0])
    if uid in ADMIN_IDS:
        await update.message.reply_text(
            "⚠️ Cannot remove hardcoded admins (ADMIN_IDS in .env)."
        ); return
    await db.set_admin(uid, False)
    await update.message.reply_text(
        f"✅ Admin rights removed from <code>{uid}</code>.",
        parse_mode=ParseMode.HTML, reply_markup=back_admin()
    )


# ── /whitelist  /unwhitelist ───────────────────────────────────────────────────
async def cmd_whitelist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /whitelist <user_id>"); return
    uid = int(args[0])
    await db.whitelist_user(uid, True)
    await update.message.reply_text(
        f"✅ User <code>{uid}</code> whitelisted.",
        parse_mode=ParseMode.HTML, reply_markup=back_admin()
    )

async def cmd_unwhitelist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /unwhitelist <user_id>"); return
    await db.whitelist_user(int(args[0]), False)
    await update.message.reply_text(
        f"✅ Whitelist removed from <code>{args[0]}</code>.",
        parse_mode=ParseMode.HTML, reply_markup=back_admin()
    )


# ── /userinfo ──────────────────────────────────────────────────────────────────
async def cmd_userinfo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /userinfo <user_id>"); return
    try: uid = int(args[0])
    except ValueError:
        results = await db.search_user(args[0])
        if not results:
            await update.message.reply_text("No user found."); return
        uid = results[0]["user_id"]
    u = await db.get_user(uid)
    if not u:
        await update.message.reply_text("User not found."); return
    jobs = await db.get_user_jobs(uid, 5)
    lines = [
        f"👤 {bold('User Info')}\n{DIVIDER}",
        f"🆔 ID       : <code>{u['user_id']}</code>",
        f"👤 Name     : {u.get('first_name','')} {u.get('last_name','')}",
        f"📛 Username : @{u.get('username','—')}",
        f"🛡️ Admin    : {'Yes' if u.get('is_admin') else 'No'}",
        f"🚫 Banned   : {'Yes' if u.get('is_banned') else 'No'}",
        f"📅 Joined   : {u.get('joined_at','?')[:10]}",
        f"👁️ Last seen: {u.get('last_seen','?')[:16]}",
        f"📦 Jobs     : {u.get('total_jobs',0)}",
        f"📄 Files    : {u.get('total_files',0)}",
        f"💾 Data     : {fmt_bytes(u.get('total_bytes',0))}",
        f"📝 Notes    : {u.get('notes','—')[:80]}",
    ]
    if jobs:
        lines.append(f"\n{bold('Recent Jobs:')}:")
        for j in jobs:
            ic = {"running":"⚡","completed":"✅","paused":"⏸","cancelled":"⏹"}.get(j["status"],"❓")
            lines.append(f"  {ic} <code>{j['job_id'][:8]}</code>  {j['current_index']}/{j['total_links']}")
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=back_admin()
    )


# ── /note  — add note to a user ───────────────────────────────────────────────
async def cmd_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /note <user_id> <text>"); return
    try: uid = int(args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID."); return
    note = " ".join(args[1:])
    await db.add_note(uid, note, update.effective_user.id)
    await update.message.reply_text(
        f"📝 Note added to <code>{uid}</code>: {note}",
        parse_mode=ParseMode.HTML, reply_markup=back_admin()
    )


# ── /jobs ──────────────────────────────────────────────────────────────────────
async def cmd_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    status = context.args[0] if context.args else None
    jobs   = await db.get_all_jobs(limit=20, status=status)
    if not jobs:
        await update.message.reply_text(f"No jobs{' with status '+status if status else ''}.")
        return
    ic = {"running":"⚡","completed":"✅","paused":"⏸","cancelled":"⏹","failed":"❌"}
    lines = [f"📦 {bold('Jobs')} ({len(jobs)})\n{DIVIDER}"]
    for j in jobs:
        lines.append(
            f"{ic.get(j['status'],'❓')} <code>{j['job_id'][:8]}</code>  u={j['user_id']}"
            f"  {j['current_index']}/{j['total_links']}  ✅{j['completed_links']} ❌{j['failed_links']}"
        )
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=back_admin()
    )


# ── /killjob ───────────────────────────────────────────────────────────────────
async def cmd_killjob(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    if not context.args:
        await update.message.reply_text("Usage: /killjob <job_id_prefix>"); return
    from .queue_manager import _cancels, cancel_job
    prefix  = context.args[0].lower()
    matched = [jid for jid in list(_cancels) if jid.lower().startswith(prefix)]
    if not matched:
        await update.message.reply_text(f"No running job matches '{prefix}'."); return
    for jid in matched:
        await cancel_job(jid, db)
    await update.message.reply_text(
        f"⏹ Killed {len(matched)} job(s): " + ", ".join(j[:8] for j in matched),
        reply_markup=back_admin()
    )


# ── /killall  — cancel all running jobs ───────────────────────────────────────
async def cmd_killall(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    from .queue_manager import _cancels, cancel_job
    jids = list(_cancels.keys())
    for jid in jids:
        await cancel_job(jid, db)
    await update.message.reply_text(
        f"⏹ Killed {len(jids)} job(s).", reply_markup=back_admin()
    )


# ── /broadcast ────────────────────────────────────────────────────────────────
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    if not context.args:
        await update.message.reply_text("Usage: /broadcast <message>"); return
    msg   = " ".join(context.args)
    users = await db.get_all_users()
    sent  = fail = 0
    sm    = await update.message.reply_text(f"📡 Broadcasting to {len(users)} users…")
    for u in users:
        if u.get("is_banned"): continue
        try:
            await context.bot.send_message(
                chat_id=u["user_id"],
                text=f"📢 {bold('Broadcast')}\n{msg}",
                parse_mode=ParseMode.HTML,
            )
            sent += 1
        except Exception:
            fail += 1
    await db.log_broadcast(msg, sent, fail, update.effective_user.id)
    await sm.edit_text(
        f"✅ Broadcast done!\nSent: {sent}  Failed: {fail}",
        parse_mode=ParseMode.HTML, reply_markup=back_admin()
    )


# ── /announce  — send to channel ─────────────────────────────────────────────
async def cmd_announce(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    from config.settings import CHANNEL_ID
    if not CHANNEL_ID:
        await update.message.reply_text("CHANNEL_ID not set in .env."); return
    if not context.args:
        await update.message.reply_text("Usage: /announce <message>"); return
    msg = " ".join(context.args)
    try:
        await context.bot.send_message(
            chat_id=CHANNEL_ID, text=msg, parse_mode=ParseMode.HTML
        )
        await update.message.reply_text("✅ Announcement sent.", reply_markup=back_admin())
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: {e}")


# ── /setkey  — add DRM key ────────────────────────────────────────────────────
async def cmd_setkey(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /setkey <kid> <key> [label]"); return
    kid = args[0].lower(); key = args[1].lower()
    label = " ".join(args[2:]) if len(args) > 2 else ""
    await db.add_drm_key(kid, key, label, update.effective_user.id)
    from config import settings as cfg
    cfg.DRM_KEYS[kid] = key
    await update.message.reply_text(
        f"✅ DRM key stored:\n<code>{kid}</code>",
        parse_mode=ParseMode.HTML, reply_markup=back_admin()
    )


# ── /delkey ────────────────────────────────────────────────────────────────────
async def cmd_delkey(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    if not context.args:
        await update.message.reply_text("Usage: /delkey <kid>"); return
    kid = context.args[0].lower()
    n   = await db.del_drm_key(kid)
    from config import settings as cfg
    cfg.DRM_KEYS.pop(kid, None)
    await update.message.reply_text(
        f"{'✅ Key deleted.' if n else '❌ Key not found.'}",
        reply_markup=back_admin()
    )


# ── /listkeys ─────────────────────────────────────────────────────────────────
async def cmd_listkeys(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    keys = await db.list_drm_keys()
    if not keys:
        await update.message.reply_text("No DRM keys stored.", reply_markup=back_admin()); return
    lines = [f"🔑 {bold('DRM Keys')} ({len(keys)})\n{DIVIDER}"]
    for k in keys:
        lines.append(
            f"• KID: <code>{k['kid'][:20]}</code>\n"
            f"  KEY: <code>{k['key'][:20]}…</code>\n"
            f"  Label: {k.get('label','—')}  Added: {k['created_at'][:10]}"
        )
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=drm_menu()
    )


# ── /setcookie ────────────────────────────────────────────────────────────────
async def cmd_setcookie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    if not context.args:
        await update.message.reply_text(
            "Usage: /setcookie <cookie_string> [label]\n"
            "Example: /setcookie token=abc123 my_account"
        ); return
    cookie = context.args[0]
    label  = " ".join(context.args[1:]) if len(context.args) > 1 else "admin-set"
    await db.save_cookie(cookie, "admin", label, update.effective_user.id)
    await update.message.reply_text(
        f"✅ Cookie saved (label: {label}).", reply_markup=back_admin()
    )


# ── /getcookie ────────────────────────────────────────────────────────────────
async def cmd_getcookie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    cks = await db.list_cookies()
    if not cks:
        await update.message.reply_text("No cookies.", reply_markup=back_admin()); return
    lines = [f"🍪 {bold('Cookies')}\n{DIVIDER}"]
    for c in cks:
        act = "✅ Active" if c["active"] else "○"
        lines.append(f"{act}  <code>{c['email'] or 'manual'}</code>  {c.get('label','—')}  {c['created_at'][:10]}")
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=back_admin()
    )


# ── /maintenance ──────────────────────────────────────────────────────────────
async def cmd_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    from config import settings as cfg
    arg = (context.args[0].lower() if context.args else None)
    if arg == "on":    cfg.MAINTENANCE_MODE = True
    elif arg == "off": cfg.MAINTENANCE_MODE = False
    else:              cfg.MAINTENANCE_MODE = not cfg.MAINTENANCE_MODE
    val = "ON" if cfg.MAINTENANCE_MODE else "OFF"
    await db.set_config("maintenance_mode", str(cfg.MAINTENANCE_MODE).lower())
    await update.message.reply_text(
        f"🔧 Maintenance mode: <b>{val}</b>", parse_mode=ParseMode.HTML, reply_markup=back_admin()
    )


# ── /alllogs  /errorlogs ──────────────────────────────────────────────────────
async def cmd_alllogs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    rows = await db.get_logs(limit=30)
    lines = [f"📋 {bold('Global Logs')}\n{DIVIDER}"]
    for r in reversed(rows):
        ic = {"INFO":"ℹ️","ERROR":"❌","WARNING":"⚠️"}.get(r["level"],"•")
        lines.append(f"{ic} <code>{r['created_at'][:16]}</code> u={r.get('user_id','?')}  {r['message'][:55]}")
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=back_admin()
    )

async def cmd_errorlogs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    rows = await db.get_logs(level="ERROR", limit=25)
    lines = [f"❌ {bold('Error Logs')}\n{DIVIDER}"]
    for r in reversed(rows):
        lines.append(f"❌ <code>{r['created_at'][:16]}</code> u={r.get('user_id','?')}  {r['message'][:60]}")
    await update.message.reply_text(
        "\n".join(lines) if len(lines)>1 else "✅ No errors.",
        parse_mode=ParseMode.HTML, reply_markup=back_admin()
    )


# ── /clearlogs ────────────────────────────────────────────────────────────────
async def cmd_clearlogs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    uid_arg = context.args[0] if context.args and context.args[0].isdigit() else None
    n = await db.clear_logs(uid=int(uid_arg) if uid_arg else None)
    await update.message.reply_text(
        f"🗑️ Cleared {n} log entries" + (f" for user {uid_arg}" if uid_arg else ""),
        reply_markup=back_admin()
    )


# ── /setconfig  /getconfig ────────────────────────────────────────────────────
async def cmd_setconfig(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /setconfig <key> <value>"); return
    key = context.args[0]; val = " ".join(context.args[1:])
    await db.set_config(key, val)
    await update.message.reply_text(
        f"✅ Config set: <code>{key}</code> = <code>{val}</code>",
        parse_mode=ParseMode.HTML, reply_markup=back_admin()
    )

async def cmd_getconfig(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    if context.args:
        val = await db.get_config(context.args[0], "NOT SET")
        await update.message.reply_text(
            f"<code>{context.args[0]}</code> = <code>{val}</code>",
            parse_mode=ParseMode.HTML
        )
    else:
        all_cfg = await db.get_all_config()
        lines   = [f"⚙️ {bold('Config')}\n{DIVIDER}"]
        for k, v in all_cfg.items():
            lines.append(f"• <code>{k}</code> = <code>{v}</code>")
        if not all_cfg: lines.append("No config entries.")
        await update.message.reply_text(
            "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=back_admin()
        )


# ── /exportdb  — export full DB as JSON ──────────────────────────────────────
async def cmd_exportdb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    s = await db.get_stats()
    users = await db.get_all_users(limit=500)
    data  = {
        "exported_at": datetime.utcnow().isoformat(),
        "stats": s,
        "users": [{"id":u["user_id"],"username":u.get("username",""),
                   "files":u.get("total_files",0)} for u in users],
    }
    buf      = io.BytesIO(json.dumps(data, indent=2).encode())
    buf.name = f"export_{datetime.utcnow().strftime('%Y%m%d')}.json"
    await update.message.reply_document(
        document=buf, filename=buf.name,
        caption="📤 Database export",
    )


# ── /topusers ─────────────────────────────────────────────────────────────────
async def cmd_topusers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    users = await db.get_all_users(limit=200)
    top   = sorted(users, key=lambda x: x.get("total_files",0), reverse=True)[:10]
    lines = [f"🏆 {bold('Top Users by Files')}\n{DIVIDER}"]
    for i, u in enumerate(top, 1):
        lines.append(
            f"{i}. <code>{u['user_id']}</code>  {u.get('first_name','')}  "
            f"📄 {u.get('total_files',0)}  💾 {fmt_bytes(u.get('total_bytes',0))}"
        )
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=back_admin()
    )


# ── /searchuser ───────────────────────────────────────────────────────────────
async def cmd_searchuser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    if not context.args:
        await update.message.reply_text("Usage: /searchuser <query>"); return
    results = await db.search_user(" ".join(context.args))
    if not results:
        await update.message.reply_text("No users found."); return
    lines = [f"🔍 {bold('Search Results')} ({len(results)})\n{DIVIDER}"]
    for u in results:
        lines.append(
            f"• <code>{u['user_id']}</code>  {u.get('first_name','')}  "
            f"@{u.get('username','—')}  f={u.get('total_files',0)}"
        )
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=back_admin()
    )


# ── /monitor  — real-time running jobs ────────────────────────────────────────
async def cmd_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    from .queue_manager import _tasks, _pauses
    running = [(jid, t) for jid, t in _tasks.items() if not t.done()]
    lines = [f"⚡ {bold('Live Monitor')}\n{DIVIDER}"]
    if not running:
        lines.append("No jobs currently running.")
    else:
        for jid, t in running:
            paused = _pauses.get(jid, None)
            state  = "⏸ PAUSED" if (paused and paused.is_set()) else "⚡ RUNNING"
            lines.append(f"{state}  <code>{jid[:8]}</code>  task={t.get_name()}")
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=back_admin()
    )


# ── /reload  — reload settings from DB ───────────────────────────────────────
async def cmd_reload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    from config import settings as cfg
    proxy = await db.get_config("http_proxy", "")
    cfg.HTTP_PROXY = proxy or None
    maint = await db.get_config("maintenance_mode", "false")
    cfg.MAINTENANCE_MODE = maint.lower() == "true"
    ao = await db.get_config("admin_only_mode", "false")
    cfg.ADMIN_ONLY_MODE = ao.lower() == "true"
    await update.message.reply_text("✅ Settings reloaded from database.", reply_markup=back_admin())


# ── /debug ────────────────────────────────────────────────────────────────────
async def cmd_debug(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not await _req(update, db): return
    import sys
    from config import settings as cfg
    from .queue_manager import _tasks, _pauses, _cancels
    await update.message.reply_text(
        f"🔧 {bold('Debug Info')}\n{DIVIDER}\n"
        f"Python: {sys.version.split()[0]}\n"
        f"Running tasks: {len([t for t in _tasks.values() if not t.done()])}\n"
        f"Pause events: {len(_pauses)}\n"
        f"Cancel events: {len(_cancels)}\n"
        f"DRM keys loaded: {len(cfg.DRM_KEYS)}\n"
        f"HTTP_PROXY: {cfg.HTTP_PROXY or 'none'}\n"
        f"MAINTENANCE: {cfg.MAINTENANCE_MODE}\n"
        f"ADMIN_ONLY: {cfg.ADMIN_ONLY_MODE}",
        parse_mode=ParseMode.HTML, reply_markup=back_admin()
    )
