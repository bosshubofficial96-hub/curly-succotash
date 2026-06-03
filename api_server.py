"""
aiohttp REST web API — v3 FIXED.
Added: /api/bypass — own bypass resolver endpoint.
"""

import json
import logging
import os
from datetime import datetime
from typing import Callable

from aiohttp import web

from config.settings import API_HOST, API_PORT, API_SECRET, BOT_NAME
from database.db import Database

logger = logging.getLogger(__name__)


# ── Middleware ────────────────────────────────────────────────────────────────
@web.middleware
async def auth_mw(request: web.Request, handler: Callable) -> web.Response:
    if request.path in ("/", "/health"):
        return await handler(request)
    secret = request.headers.get("X-API-Secret") or request.rel_url.query.get("secret")
    if secret != API_SECRET:
        raise web.HTTPUnauthorized(
            text=json.dumps({"error": "Unauthorized — set X-API-Secret header"}),
            content_type="application/json",
        )
    return await handler(request)


@web.middleware
async def cors_mw(request: web.Request, handler: Callable) -> web.Response:
    if request.method == "OPTIONS":
        return web.Response(headers={
            "Access-Control-Allow-Origin":  "*",
            "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type,X-API-Secret",
        })
    try:
        resp = await handler(request)
    except web.HTTPException as exc:
        resp = exc
    resp.headers["Access-Control-Allow-Origin"]  = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,DELETE,OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type,X-API-Secret"
    return resp


def _j(data) -> web.Response:
    return web.Response(text=json.dumps(data, default=str, indent=2),
                        content_type="application/json")

def _err(msg: str, status: int = 400) -> web.Response:
    return web.Response(text=json.dumps({"error": msg}),
                        content_type="application/json", status=status)


# ── Dashboard ─────────────────────────────────────────────────────────────────
async def health(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    s = await db.get_stats()
    return web.Response(content_type="text/html", text=f"""<!DOCTYPE html>
<html><head>
<title>{BOT_NAME}</title>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',Arial,sans-serif;background:#0F0F1A;color:#e0e0ff}}
.top{{background:linear-gradient(90deg,#6C63FF,#9B8FFF);padding:32px 40px}}
.top h1{{font-size:2rem}}.top p{{opacity:.7;margin-top:6px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:16px;padding:28px 40px}}
.card{{background:#1A1A2E;border-radius:12px;padding:18px;border:1px solid #6C63FF44}}
.card .n{{font-size:2rem;font-weight:700;color:#9B8FFF}}
.card .l{{font-size:.82rem;color:#888;margin-top:4px}}
.ep{{background:#1A1A2E;border-radius:8px;padding:9px 14px;margin-bottom:5px;font-size:.88rem;border-left:3px solid #6C63FF}}
.ep .m{{color:#FF6584;font-weight:700;display:inline-block;width:65px}}
.ep .p{{color:#9B8FFF;font-family:monospace}}
.sec{{padding:0 40px 36px}}.sec h2{{color:#9B8FFF;margin-bottom:12px}}
.note{{background:#1A1A2E55;border-radius:8px;padding:11px 16px;margin:0 40px 20px;font-size:.83rem;color:#888}}
</style></head><body>
<div class="top"><h1>🤖 {BOT_NAME}</h1><p>Advanced DRM Bypass Bot — Web Management API v3.1</p></div>
<div class="grid">
  <div class="card"><div class="n">{s['total_users']}</div><div class="l">👥 Total Users</div></div>
  <div class="card"><div class="n">{s['total_jobs']}</div><div class="l">📦 Total Jobs</div></div>
  <div class="card"><div class="n">{s['running_jobs']}</div><div class="l">⚡ Running</div></div>
  <div class="card"><div class="n">{s['total_files']}</div><div class="l">📄 Files Sent</div></div>
  <div class="card"><div class="n">{s['drm_keys']}</div><div class="l">🔑 DRM Keys</div></div>
  <div class="card"><div class="n">{s['banned_users']}</div><div class="l">🚫 Banned</div></div>
</div>
<div class="note">🔐 All /api/* endpoints require <code>X-API-Secret</code> header (set <code>API_SECRET</code> in .env)</div>
<div class="sec"><h2>📡 API Endpoints</h2>
{"".join(f'<div class="ep"><span class="m">{m}</span><span class="p">{p}</span> — {d}</div>'
for m,p,d in [
  ("GET","/api/stats","Bot statistics"),
  ("GET","/api/users","User list"),
  ("POST","/api/ban","Ban user {user_id}"),
  ("POST","/api/unban","Unban user {user_id}"),
  ("GET","/api/jobs","Job list"),
  ("POST","/api/killjob","Kill job {job_id}"),
  ("GET","/api/drm","DRM key list"),
  ("POST","/api/drm","Add key {kid,key}"),
  ("DELETE","/api/drm/{kid}","Delete key"),
  ("GET","/api/cookies","Cookie list"),
  ("POST","/api/cookies","Add cookie {cookie}"),
  ("GET","/api/logs","Log entries"),
  ("POST","/api/broadcast","Broadcast {message}"),
  ("GET","/api/config","Config store"),
  ("POST","/api/config","Set config {key,value}"),
  ("POST","/api/maintenance","Toggle maintenance"),
  ("POST","/api/bypass","🔓 Resolve/bypass a URL {url,cookie?}"),
])}
</div>
<p style="padding:0 40px 28px;color:#2a2a4a;font-size:.78rem">
Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
</p></body></html>""")


# ── Bypass API — own URL resolver ─────────────────────────────────────────────
async def api_bypass(request: web.Request) -> web.Response:
    """
    POST /api/bypass
    Body: {"url": "https://...", "cookie": "token=...", "drm_keys": {"kid":"key"}}
    Returns: {"ok": true, "url": "...", "strategy": "...", "headers": {...}}
    """
    body   = await request.json()
    url    = body.get("url","").strip()
    cookie = body.get("cookie","") or ""
    drm_kv = body.get("drm_keys", {}) or {}

    if not url:
        return _err("url is required")

    from bot.drm import DRMResolver, classify, is_valid_url, merged_drm_keys
    if not is_valid_url(url):
        return _err("Invalid or blocked URL")

    db: Database = request.app["db"]
    if not cookie:
        cookie = await db.get_active_cookie() or ""

    import aiohttp
    from config.settings import HTTP_PROXY
    try:
        merged = await merged_drm_keys(db)
        merged.update(drm_kv)

        async with aiohttp.ClientSession() as session:
            resolver = DRMResolver(session, cookie=cookie, drm_keys=merged, proxy=HTTP_PROXY)
            resolved, headers, kind = await resolver.resolve(url)

        # Determine which strategy succeeded
        strategy = "unknown"
        if resolved == url:
            strategy = "direct (original URL)"
        elif resolved and "URLPrefix" not in (resolved or ""):
            strategy = "cdn-bypass"
        else:
            strategy = "fallback"

        # Don't expose internal headers with auth tokens to clients
        safe_headers = {k: v for k, v in headers.items()
                        if k.lower() not in ("authorization", "cookie")}

        return _j({
            "ok":       True,
            "url":      resolved or url,
            "original": url,
            "strategy": strategy,
            "kind":     kind,
            "headers":  safe_headers,
        })
    except Exception as e:
        logger.error("bypass API error: %s", e)
        return _err(str(e), status=500)


# ── Standard endpoints ────────────────────────────────────────────────────────
async def api_stats(r: web.Request) -> web.Response:
    return _j({"ok": True, "data": await r.app["db"].get_stats()})

async def api_users(r: web.Request) -> web.Response:
    limit = int(r.rel_url.query.get("limit",100))
    users = await r.app["db"].get_all_users(limit=limit)
    return _j({"ok": True, "count": len(users), "users": users})

async def api_ban(r: web.Request) -> web.Response:
    b = await r.json(); uid = b.get("user_id")
    if not uid: return _err("user_id required")
    await r.app["db"].ban_user(int(uid))
    return _j({"ok": True, "banned": uid})

async def api_unban(r: web.Request) -> web.Response:
    b = await r.json(); uid = b.get("user_id")
    if not uid: return _err("user_id required")
    await r.app["db"].unban_user(int(uid))
    return _j({"ok": True, "unbanned": uid})

async def api_jobs(r: web.Request) -> web.Response:
    status = r.rel_url.query.get("status")
    limit  = int(r.rel_url.query.get("limit",50))
    jobs   = await r.app["db"].get_all_jobs(limit=limit, status=status)
    return _j({"ok": True, "count": len(jobs), "jobs": jobs})

async def api_killjob(r: web.Request) -> web.Response:
    b = await r.json(); jid = b.get("job_id","")
    from bot.queue_manager import cancel_job, _cancels
    matched = [j for j in list(_cancels) if j.startswith(jid)]
    for j in matched: await cancel_job(j, r.app["db"])
    return _j({"ok": True, "killed": len(matched)})

async def api_drm_get(r: web.Request) -> web.Response:
    return _j({"ok": True, "keys": await r.app["db"].list_drm_keys()})

async def api_drm_add(r: web.Request) -> web.Response:
    b = await r.json()
    kid, key, label = b.get("kid","").lower(), b.get("key","").lower(), b.get("label","api")
    if not kid or not key: return _err("kid and key required")
    await r.app["db"].add_drm_key(kid, key, label, 0)
    from config import settings as cfg; cfg.DRM_KEYS[kid] = key
    return _j({"ok": True, "kid": kid})

async def api_drm_del(r: web.Request) -> web.Response:
    kid = r.match_info["kid"].lower()
    n   = await r.app["db"].del_drm_key(kid)
    from config import settings as cfg; cfg.DRM_KEYS.pop(kid, None)
    return _j({"ok": True, "deleted": n > 0})

async def api_cookies_get(r: web.Request) -> web.Response:
    return _j({"ok": True, "cookies": await r.app["db"].list_cookies()})

async def api_cookies_add(r: web.Request) -> web.Response:
    b = await r.json()
    cookie = b.get("cookie","")
    if not cookie: return _err("cookie required")
    await r.app["db"].save_cookie(cookie, b.get("email","api"), b.get("label","api"), 0)
    return _j({"ok": True})

async def api_logs(r: web.Request) -> web.Response:
    uid   = r.rel_url.query.get("user_id")
    level = r.rel_url.query.get("level")
    limit = int(r.rel_url.query.get("limit",50))
    rows  = await r.app["db"].get_logs(
        uid=int(uid) if uid else None, level=level, limit=limit,
    )
    return _j({"ok": True, "count": len(rows), "logs": rows})

async def api_broadcast(r: web.Request) -> web.Response:
    b = await r.json(); msg = b.get("message","")
    if not msg: return _err("message required")
    bot   = r.app["bot"]
    users = await r.app["db"].get_all_users()
    sent = fail = 0
    for u in users:
        if u.get("is_banned"): continue
        try:
            await bot.send_message(chat_id=u["user_id"], text=f"📢 {msg}", parse_mode="HTML")
            sent += 1
        except Exception: fail += 1
    await r.app["db"].log_broadcast(msg, sent, fail, 0)
    return _j({"ok": True, "sent": sent, "failed": fail})

async def api_config_get(r: web.Request) -> web.Response:
    return _j({"ok": True, "config": await r.app["db"].get_all_config()})

async def api_config_set(r: web.Request) -> web.Response:
    b = await r.json(); key = b.get("key",""); val = b.get("value","")
    if not key: return _err("key required")
    await r.app["db"].set_config(key, str(val))
    return _j({"ok": True, "key": key, "value": val})

async def api_maintenance(r: web.Request) -> web.Response:
    b = await r.json()
    from config import settings as cfg
    enable = b.get("enable")
    cfg.MAINTENANCE_MODE = bool(enable) if enable is not None else not cfg.MAINTENANCE_MODE
    await r.app["db"].set_config("maintenance_mode", str(cfg.MAINTENANCE_MODE).lower())
    return _j({"ok": True, "maintenance": cfg.MAINTENANCE_MODE})


# ── App factory ────────────────────────────────────────────────────────────────
def create_app(db: Database, bot=None) -> web.Application:
    app = web.Application(middlewares=[cors_mw, auth_mw])
    app["db"]  = db
    app["bot"] = bot

    app.router.add_get   ("/",                health)
    app.router.add_get   ("/health",          health)
    app.router.add_get   ("/api/stats",       api_stats)
    app.router.add_get   ("/api/users",       api_users)
    app.router.add_post  ("/api/ban",         api_ban)
    app.router.add_post  ("/api/unban",       api_unban)
    app.router.add_get   ("/api/jobs",        api_jobs)
    app.router.add_post  ("/api/killjob",     api_killjob)
    app.router.add_get   ("/api/drm",         api_drm_get)
    app.router.add_post  ("/api/drm",         api_drm_add)
    app.router.add_delete("/api/drm/{kid}",   api_drm_del)
    app.router.add_get   ("/api/cookies",     api_cookies_get)
    app.router.add_post  ("/api/cookies",     api_cookies_add)
    app.router.add_get   ("/api/logs",        api_logs)
    app.router.add_post  ("/api/broadcast",   api_broadcast)
    app.router.add_get   ("/api/config",      api_config_get)
    app.router.add_post  ("/api/config",      api_config_set)
    app.router.add_post  ("/api/maintenance", api_maintenance)
    app.router.add_post  ("/api/bypass",      api_bypass)   # ← own bypass API

    return app


async def start_api(db: Database, bot=None) -> web.AppRunner:
    app    = create_app(db, bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site   = web.TCPSite(runner, API_HOST, API_PORT)
    await site.start()
    logger.info("Web API → http://%s:%s/", API_HOST, API_PORT)
    return runner
