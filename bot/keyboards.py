"""All inline keyboard layouts — v4.4 (AppX V2 bypass button fix)."""

from typing import Optional
from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M


# ── Main menu ──────────────────────────────────────────────────────────────────
def main_menu(is_admin: bool = False) -> M:
    rows = [
        [B("📤 Upload .txt",    callback_data="guide_upload"),
         B("📊 My Status",      callback_data="my_status")],
        [B("📋 My Logs",        callback_data="my_logs"),
         B("📁 My History",     callback_data="my_history")],
        [B("👤 My Profile",     callback_data="my_profile"),
         B("⚙️ Settings",       callback_data="settings")],
        [B("❓ Help",           callback_data="help"),
         B("📡 Bot Info",       callback_data="bot_info")],
    ]
    if is_admin:
        rows.append([B("🛡️ Admin Panel", callback_data="admin_panel")])
    return M(rows)


# ── Job controls ───────────────────────────────────────────────────────────────
def job_controls(job_id: str, paused: bool = False) -> M:
    toggle = (B("▶️ Resume", callback_data=f"resume:{job_id}") if paused
              else B("⏸ Pause", callback_data=f"pause:{job_id}"))
    return M([
        [toggle,
         B("⏹ Cancel",          callback_data=f"cancel:{job_id}")],
        [B("📊 Refresh",         callback_data=f"status:{job_id}"),
         B("📋 Logs",            callback_data=f"logs:{job_id}")],
        [B("🔁 Retry Failed",    callback_data=f"retry_failed:{job_id}"),
         B("📩 Export Report",   callback_data=f"report:{job_id}")],
    ])


# ── Start index picker ─────────────────────────────────────────────────────────
def start_index_kb(total: int) -> M:
    picks = sorted({1, max(1, total // 4), max(1, total // 2),
                    max(1, 3 * total // 4), total})
    row   = [B(f"#{p}", callback_data=f"sidx:{p}") for p in picks]
    return M([
        row,
        [B("✏️ Custom number",  callback_data="sidx:custom")],
        [B("❌ Cancel",         callback_data="sidx:cancel")],
    ])


# ── Settings menu ──────────────────────────────────────────────────────────────
def settings_menu() -> M:
    return M([
        [B("🔑 AppX Login",     callback_data="cfg_login"),
         B("🍪 Set Cookie",     callback_data="cfg_cookie")],
        [B("🔐 DRM Keys",       callback_data="cfg_drm"),
         B("📡 HTTP Proxy",     callback_data="cfg_proxy")],
        [B("🔔 Notifications",  callback_data="cfg_notify"),
         B("🌐 Language",       callback_data="cfg_lang")],
        [B("🔙 Main Menu",      callback_data="main_menu")],
    ])


# ── Admin panel ────────────────────────────────────────────────────────────────
def admin_menu() -> M:
    return M([
        [B("📊 Statistics",     callback_data="adm_stats"),
         B("👥 Users",          callback_data="adm_users")],
        [B("📦 All Jobs",       callback_data="adm_jobs"),
         B("🔑 DRM Keys",       callback_data="adm_drm")],
        [B("🍪 Cookies",        callback_data="adm_cookies"),
         B("📋 Global Logs",    callback_data="adm_logs")],
        [B("📢 Broadcast",      callback_data="adm_broadcast"),
         B("⚙️ Bot Config",     callback_data="adm_config")],
        [B("🔧 Maintenance",    callback_data="adm_maint"),
         B("📤 Export Data",    callback_data="adm_export")],
        [B("🔙 Main Menu",      callback_data="main_menu")],
    ])


# ── Users management sub-menu ──────────────────────────────────────────────────
def users_menu() -> M:
    return M([
        [B("🔍 Search User",    callback_data="adm_user_search"),
         B("🚫 Banned List",    callback_data="adm_banned")],
        [B("⭐ Admins List",    callback_data="adm_admins"),
         B("📊 Top Users",      callback_data="adm_top_users")],
        [B("🔙 Admin Panel",    callback_data="admin_panel")],
    ])


# ── DRM key sub-menu ───────────────────────────────────────────────────────────
def drm_menu() -> M:
    return M([
        [B("➕ Add Key",        callback_data="adm_drm_add"),
         B("📋 List Keys",      callback_data="adm_drm_list")],
        [B("🗑️ Delete Key",     callback_data="adm_drm_del"),
         B("📥 Import Bulk",    callback_data="adm_drm_bulk")],
        [B("🔙 Admin Panel",    callback_data="admin_panel")],
    ])


# ── Config sub-menu ────────────────────────────────────────────────────────────
def config_menu() -> M:
    return M([
        [B("🔧 Maintenance ON/OFF", callback_data="adm_toggle_maint"),
         B("🔒 Admin-Only Mode",    callback_data="adm_toggle_adminonly")],
        [B("📝 Set Bot Name",       callback_data="adm_set_botname"),
         B("🎨 Set Theme Color",    callback_data="adm_set_color")],
        [B("⚙️ View All Config",    callback_data="adm_view_config"),
         B("🔄 Reload Settings",    callback_data="adm_reload")],
        [B("🔙 Admin Panel",        callback_data="admin_panel")],
    ])


# ── Back buttons ──────────────────────────────────────────────────────────────
def back_main()     -> M: return M([[B("🔙 Main Menu",   callback_data="main_menu")]])
def back_admin()    -> M: return M([[B("🔙 Admin Panel", callback_data="admin_panel")]])
def back_settings() -> M: return M([[B("🔙 Settings",   callback_data="settings")]])


# ── Confirm ───────────────────────────────────────────────────────────────────
def confirm(yes: str, no: str, yl: str = "✅ Yes", nl: str = "❌ No") -> M:
    return M([[B(yl, callback_data=yes), B(nl, callback_data=no)]])


# ── Failed report ─────────────────────────────────────────────────────────────
def failed_kb(job_id: str) -> M:
    return M([
        [B("🔁 Retry Failed", callback_data=f"retry_failed:{job_id}"),
         B("📋 View Logs",    callback_data=f"logs:{job_id}")],
        [B("📩 Export Report", callback_data=f"report:{job_id}"),
         B("🔙 Main Menu",    callback_data="main_menu")],
    ])


# ── Language picker ───────────────────────────────────────────────────────────
def lang_kb() -> M:
    return M([
        [B("🇺🇸 English",  callback_data="lang:en"),
         B("🇮🇳 Hindi",    callback_data="lang:hi")],
        [B("🇧🇩 Bengali",  callback_data="lang:bn"),
         B("🇵🇰 Urdu",     callback_data="lang:ur")],
        [B("🔙 Settings",  callback_data="settings")],
    ])


# ── Notification toggle ───────────────────────────────────────────────────────
def notify_kb(current: bool) -> M:
    lbl = "🔕 Turn OFF" if current else "🔔 Turn ON"
    return M([
        [B(lbl, callback_data=f"notify:{'off' if current else 'on'}")],
        [B("🔙 Settings", callback_data="settings")],
    ])


# ── Channel inline buttons ────────────────────────────────────────────────────

def bypass_link_kb(url: str, label: str = "🔗 Open Bypass Link") -> M:
    """Single URL button — only use when URL is actually publicly accessible."""
    return M([[B(label, url=url)]])


def channel_bypass_kb(
    bypass_url: str,
    bot_username: str = "",
    jid: str = "",
    is_v2: bool = False,
) -> Optional[M]:
    """
    Keyboard for the bypass-link notification sent to channels.

    For V2 (is_v2=True):
      - Shows "📥 Bypass & Get File" deep-link to bot (no public URL exists)
      - Shows "🤖 Get via Bot" generic link as fallback

    For standard AppX (is_v2=False):
      - Shows "🔗 Open Bypass Link" URL button if bypass_url is set
      - Shows "🤖 Get Files via Bot" generic bot link
    """
    rows = []
    uname = (bot_username or "").lstrip("@")

    if is_v2:
        # For V2: primary button is deep-link with job ID so bot knows which file
        if uname and jid:
            rows.append([B("📥 Bypass & Get File",
                           url=f"https://t.me/{uname}?start=getfile_{jid}")])
        elif uname:
            rows.append([B("📥 Get File via Bot",
                           url=f"https://t.me/{uname}")])
    else:
        # For standard AppX: show the bypass URL if it exists
        if bypass_url:
            rows.append([B("🔗 Open Bypass Link", url=bypass_url)])
        if uname:
            rows.append([B("🤖 Get Files via Bot",
                           url=f"https://t.me/{uname}")])

    return M(rows) if rows else None


def channel_file_kb(bypass_url: str = "", bot_username: str = "") -> Optional[M]:
    """
    Keyboard attached to the ACTUAL FILE message sent to a channel.

    bypass_url = "" for AppX V2 (no public URL).
    Shows only "Get More Files" bot link for V2.
    """
    rows = []
    if bypass_url:
        rows.append([B("🔗 Direct Download Link", url=bypass_url)])
    if bot_username:
        uname = bot_username.lstrip("@")
        rows.append([B("🤖 Get More Files",
                       url=f"https://t.me/{uname}")])
    return M(rows) if rows else None


def v2_download_kb(bot_username: str = "", jid: str = "") -> Optional[M]:
    """
    Keyboard for V2 encrypted video bypass messages.
    Shows a functional deep-link button that tells the bot which job to re-send.
    """
    uname = (bot_username or "").lstrip("@")
    rows = []
    if uname and jid:
        rows.append([B("📥 Bypass & Get File",
                       url=f"https://t.me/{uname}?start=getfile_{jid}")])
    elif uname:
        rows.append([B("📥 Download via Bot",
                       url=f"https://t.me/{uname}")])
    return M(rows) if rows else None
