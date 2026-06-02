"""
aiohttp-based REST web API for remote bot management.

Endpoints:
  GET  /                    → Health / dashboard
  GET  /api/stats           → Bot statistics
  GET  /api/users           → User list
  POST /api/ban             → Ban a user
  POST /api/unban           → Unban a user
  GET  /api/jobs            → Job list
  POST /api/killjob         → Cancel a job
  GET  /api/drm             → DRM key list
  POST /api/drm             → Add DRM key
  DELETE /api/drm/<kid>     → Delete DRM key
  GET  /api/cookies         → Cookie list
  POST /api/cookies         → Add cookie
  GET  /api/logs            → Log entries
  POST /api/broadcast       → Broadcast message
  GET  /api/config          → Config key/value store
  POST /api/config          → Set config value
  POST /api/maintenance     → Toggle maintenance mode

Authentication: X-API-Secret header must match API_SECRET in .env
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Callable

from aiohttp import web

from config.settings import API_HOST, API_PORT, API_SECRET, BOT_NAME
from database.db import Database

logger = logging.getLogger(__name__)


# ── Auth middleware ────────────────────────────────────────────────────────────
@web.middleware
async def auth_middleware(request: web.Request, handler: Callable) -> web.Response:
    if request.path in ("/", "/health"):
        return await handler(request)
    secret = request.headers.get("X-API-Secret") or request.rel_url.query.get("secret")
    if secret != API_SECRET:
        raise web.HTTPUnauthorized(
            text=json.dumps({"error": "Unauthorized", "hint": "Set X-API-Secret header"}),
            content_type="application/json",
        )
    return await handler(request)


# ── CORS middleware ────────────────────────────────────────────────────────────
@web.middleware
async def cors_middleware(request: web.Request, handler: Callable) -> web.Response:
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        response = exc
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Secret"
    return response


def _j(data) -> web.Response:
    return web.Response(
        text=json.dumps(data, default=str, indent=2),
        content_type="application/json",
    )


def _err(msg: str, status: int = 400) -> web.Response:
    return web.Response(
        text=json.dumps({"error": msg}),
        content_type="application/json",
        status=status,
    )


# ── Route handlers ─────────────────────────────────────────────────────────────

async def health(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    s = await db.get_stats()
    return web.Response(
        text=f"""<!DOCTYPE html>
<html>
<head>
  <title>{BOT_NAME} — Web API</title>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #0F0F1A; color: #e0e0ff; }}
    .top {{ background: linear-gradient(90deg,#6C63FF,#9B8FFF); padding: 32px 40px; }}
    .top h1 {{ font-size: 2rem; }}
    .top p  {{ opacity: .75; margin-top: 6px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fill,minmax(200px,1fr));
             gap: 18px; padding: 32px 40px; }}
    .card {{ background: #1A1A2E; border-radius: 12px; padding: 20px;
             border: 1px solid #6C63FF44; }}
    .card .num {{ font-size: 2rem; font-weight: 700; color: #9B8FFF; }}
    .card .lbl {{ font-size: .85rem; color: #888; margin-top: 4px; }}
    .endpoints {{ padding: 0 40px 40px; }}
    .endpoints h2 {{ color: #9B8FFF; margin-bottom: 14px; }}
    .ep {{ background: #1A1A2E; border-radius: 8px; padding: 10px 16px;
           margin-bottom: 6px; font-size: .9rem; border-left: 3px solid #6C63FF; }}
    .ep .method {{ color: #FF6584; font-weight: 700; display: inline-block; width: 60px; }}
    .ep .path   {{ color: #9B8FFF; font-family: monospace; }}
    .note {{ background: #1A1A2E55; border-radius: 8px; padding: 12px 16px;
             margin: 0 40px 20px; font-size: .85rem; color: #888; }}
  </style>
</head>
<body>
  <div class="top">
    <h1>🤖 {BOT_NAME}</h1>
    <p>Advanced DRM Bypass Bot — Web Management API</p>
  </div>
  <div class="grid">
    <div class="card"><div class="num">{s['total_users']}</div><div class="lbl">👥 Total Users</div></div>
    <div class="card"><div class="num">{s['total_jobs']}</div><div class="lbl">📦 Total Jobs</div></div>
    <div class="card"><div class="num">{s['running_jobs']}</div><div class="lbl">⚡ Running</div></div>
    <div class="card"><div class="num">{s['total_files']}</div><div class="lbl">📄 Files Sent</div></div>
    <div class="card"><div class="num">{s['drm_keys']}</div><div class="lbl">🔑 DRM Keys</div></div>
    <div class="card"><div class="num">{s['banned_users']}</div><div class="lbl">🚫 Banned</div></div>
  </div>
  <div class="note">
    🔐 All /api/* endpoints require <code>X-API-Secret</code> header (set in .env as API_SECRET)
  </div>
  <div class="endpoints">
    <h2>📡 API Endpoints</h2>
    <div class="ep"><span class="method">GET</span> <span class="path">/api/stats</span> — Bot statistics</div>
    <div class="ep"><span class="method">GET</span> <span class="path">/api/users</span> — User list</div>
    <div class="ep"><span class="method">POST</span> <span class="path">/api/ban</span> — Ban user {{"user_id":…}}</div>
    <div class="ep"><span class="method">POST</span> <span class="path">/api/unban</span> — Unban user</div>
    <div class="ep"><span class="method">GET</span> <span class="path">/api/jobs</span> — Job list</div>
    <div class="ep"><span class="method">POST</span> <span class="path">/api/killjob</span> — Kill job {{"job_id":…}}</div>
    <div class="ep"><span class="method">GET</span> <span class="path">/api/drm</span> — DRM keys</div>
    <div class="ep"><span class="method">POST</span> <span class="path">/api/drm</span> — Add DRM key {{"kid":…,"key":…}}</div>
    <div class="ep"><span class="method">DELETE</span> <span class="path">/api/drm/{{kid}}</span> — Delete key</div>
    <div class="ep"><span class="method">GET</span> <span class="path">/api/cookies</span> — Cookie list</div>
    <div class="ep"><span class="method">POST</span> <span class="path">/api/cookies</span> — Add cookie {{"cookie":…}}</div>
    <div class="ep"><span class="method">GET</span> <span class="path">/api/logs</span> — Log entries</div>
    <div class="ep"><span class="method">POST</span> <span class="path">/api/broadcast</span> — Broadcast {{"message":…}}</div>
    <div class="ep"><span class="method">GET</span> <span class="path">/api/config</span> — Config store</div>
    <div class="ep"><span class="method">POST</span> <span class="path">/api/config</span> — Set config {{"key":…,"value":…}}</div>
    <div class="ep"><span class="method">POST</span> <span class="path">/api/maintenance</span> — Toggle maintenance</div>
  </div>
  <p style="padding: 0 40px 30px; color:#444; font-size:.8rem">
    Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
  </p>
</body>
</html>""",
        content_type="text/html",
    )


async def api_stats(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    return _j({"ok": True, "data": await db.get_stats()})


async def api_users(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    limit = int(request.rel_url.query.get("limit", 100))
    users = await db.get_all_users(limit=limit)
    return _j({"ok": True, "count": len(users), "users": users})


async def api_ban(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    body = await request.json()
    uid  = body.get("user_id")
    if not uid: return _err("user_id required")
    await db.ban_user(int(uid))
    return _j({"ok": True, "banned": uid})


async def api_unban(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    body = await request.json()
    uid  = body.get("user_id")
    if not uid: return _err("user_id required")
    await db.unban_user(int(uid))
    return _j({"ok": True, "unbanned": uid})


async def api_jobs(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    status = request.rel_url.query.get("status")
    limit  = int(request.rel_url.query.get("limit", 50))
    jobs   = await db.get_all_jobs(limit=limit, status=status)
    return _j({"ok": True, "count": len(jobs), "jobs": jobs})


async def api_killjob(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    body = await request.json()
    jid  = body.get("job_id","")
    from bot.queue_manager import cancel_job, _cancels
    matched = [j for j in list(_cancels) if j.startswith(jid)]
    for j in matched:
        await cancel_job(j, db)
    return _j({"ok": True, "killed": len(matched), "ids": matched})


async def api_drm_get(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    keys = await db.list_drm_keys()
    return _j({"ok": True, "count": len(keys), "keys": keys})


async def api_drm_add(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    body  = await request.json()
    kid   = body.get("kid","").lower()
    key   = body.get("key","").lower()
    label = body.get("label","api")
    if not kid or not key: return _err("kid and key required")
    await db.add_drm_key(kid, key, label, 0)
    from config import settings as cfg
    cfg.DRM_KEYS[kid] = key
    return _j({"ok": True, "kid": kid})


async def api_drm_del(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    kid = request.match_info["kid"].lower()
    n   = await db.del_drm_key(kid)
    from config import settings as cfg
    cfg.DRM_KEYS.pop(kid, None)
    return _j({"ok": True, "deleted": n > 0})


async def api_cookies_get(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    cks = await db.list_cookies()
    return _j({"ok": True, "cookies": cks})


async def api_cookies_add(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    body   = await request.json()
    cookie = body.get("cookie","")
    email  = body.get("email","api")
    label  = body.get("label","api")
    if not cookie: return _err("cookie required")
    await db.save_cookie(cookie, email, label, 0)
    return _j({"ok": True})


async def api_logs(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    uid   = request.rel_url.query.get("user_id")
    level = request.rel_url.query.get("level")
    limit = int(request.rel_url.query.get("limit", 50))
    rows  = await db.get_logs(
        uid=int(uid) if uid else None,
        level=level,
        limit=limit,
    )
    return _j({"ok": True, "count": len(rows), "logs": rows})


async def api_broadcast(request: web.Request) -> web.Response:
    db: Database  = request.app["db"]
    bot           = request.app["bot"]
    body = await request.json()
    msg  = body.get("message","")
    if not msg: return _err("message required")
    users = await db.get_all_users()
    sent  = fail = 0
    for u in users:
        if u.get("is_banned"): continue
        try:
            await bot.send_message(chat_id=u["user_id"], text=f"📢 {msg}", parse_mode="HTML")
            sent += 1
        except Exception:
            fail += 1
    await db.log_broadcast(msg, sent, fail, 0)
    return _j({"ok": True, "sent": sent, "failed": fail})


async def api_config_get(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    cfg = await db.get_all_config()
    return _j({"ok": True, "config": cfg})


async def api_config_set(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    body = await request.json()
    key  = body.get("key","")
    val  = body.get("value","")
    if not key: return _err("key required")
    await db.set_config(key, str(val))
    return _j({"ok": True, "key": key, "value": val})


async def api_maintenance(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    from config import settings as cfg
    body = await request.json()
    enable = body.get("enable")
    if enable is None:
        cfg.MAINTENANCE_MODE = not cfg.MAINTENANCE_MODE
    else:
        cfg.MAINTENANCE_MODE = bool(enable)
    await db.set_config("maintenance_mode", str(cfg.MAINTENANCE_MODE).lower())
    return _j({"ok": True, "maintenance": cfg.MAINTENANCE_MODE})


# ── App factory ────────────────────────────────────────────────────────────────
def create_app(db: Database, bot=None) -> web.Application:
    app = web.Application(middlewares=[cors_middleware, auth_middleware])
    app["db"]  = db
    app["bot"] = bot

    app.router.add_get ("/",                  health)
    app.router.add_get ("/health",            health)
    app.router.add_get ("/api/stats",         api_stats)
    app.router.add_get ("/api/users",         api_users)
    app.router.add_post("/api/ban",           api_ban)
    app.router.add_post("/api/unban",         api_unban)
    app.router.add_get ("/api/jobs",          api_jobs)
    app.router.add_post("/api/killjob",       api_killjob)
    app.router.add_get ("/api/drm",           api_drm_get)
    app.router.add_post("/api/drm",           api_drm_add)
    app.router.add_delete("/api/drm/{kid}",   api_drm_del)
    app.router.add_get ("/api/cookies",       api_cookies_get)
    app.router.add_post("/api/cookies",       api_cookies_add)
    app.router.add_get ("/api/logs",          api_logs)
    app.router.add_post("/api/broadcast",     api_broadcast)
    app.router.add_get ("/api/config",        api_config_get)
    app.router.add_post("/api/config",        api_config_set)
    app.router.add_post("/api/maintenance",   api_maintenance)

    return app


async def start_api(db: Database, bot=None) -> web.AppRunner:
    app    = create_app(db, bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site   = web.TCPSite(runner, API_HOST, API_PORT)
    await site.start()
    logger.info("Web API running: http://%s:%s", API_HOST, API_PORT)
    return runner
