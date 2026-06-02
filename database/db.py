"""Full async SQLite database layer."""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiosqlite

logger = logging.getLogger(__name__)

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    user_id       INTEGER PRIMARY KEY,
    username      TEXT    DEFAULT '',
    first_name    TEXT    DEFAULT '',
    last_name     TEXT    DEFAULT '',
    is_banned     INTEGER DEFAULT 0,
    is_admin      INTEGER DEFAULT 0,
    is_whitelisted INTEGER DEFAULT 0,
    language      TEXT    DEFAULT 'en',
    notify_done   INTEGER DEFAULT 1,
    joined_at     TEXT    DEFAULT (datetime('now')),
    last_seen     TEXT    DEFAULT (datetime('now')),
    total_jobs    INTEGER DEFAULT 0,
    total_files   INTEGER DEFAULT 0,
    total_bytes   INTEGER DEFAULT 0,
    notes         TEXT    DEFAULT ''
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id          TEXT    PRIMARY KEY,
    user_id         INTEGER NOT NULL,
    chat_id         INTEGER NOT NULL,
    status          TEXT    DEFAULT 'pending',
    total_links     INTEGER DEFAULT 0,
    current_index   INTEGER DEFAULT 0,
    completed_links INTEGER DEFAULT 0,
    failed_links    INTEGER DEFAULT 0,
    start_index     INTEGER DEFAULT 0,
    progress_msg_id INTEGER DEFAULT 0,
    source_name     TEXT    DEFAULT '',
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now')),
    finished_at     TEXT,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT    NOT NULL,
    url         TEXT    NOT NULL,
    line_number INTEGER NOT NULL,
    status      TEXT    DEFAULT 'pending',
    filename    TEXT    DEFAULT '',
    file_size   INTEGER DEFAULT 0,
    mime_type   TEXT    DEFAULT '',
    attempts    INTEGER DEFAULT 0,
    error_msg   TEXT    DEFAULT '',
    created_at  TEXT    DEFAULT (datetime('now')),
    updated_at  TEXT    DEFAULT (datetime('now')),
    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
);

CREATE TABLE IF NOT EXISTS bot_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER,
    job_id     TEXT,
    level      TEXT,
    message    TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS rate_limits (
    user_id      INTEGER PRIMARY KEY,
    calls        INTEGER DEFAULT 0,
    window_start TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS drm_keys (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kid        TEXT    NOT NULL,
    key        TEXT    NOT NULL,
    label      TEXT    DEFAULT '',
    added_by   INTEGER,
    created_at TEXT    DEFAULT (datetime('now')),
    UNIQUE(kid)
);

CREATE TABLE IF NOT EXISTS cookies (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    cookie     TEXT    NOT NULL,
    email      TEXT    DEFAULT '',
    label      TEXT    DEFAULT '',
    added_by   INTEGER,
    active     INTEGER DEFAULT 1,
    created_at TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS bot_config (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS broadcasts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    message    TEXT,
    sent_count INTEGER DEFAULT 0,
    fail_count INTEGER DEFAULT 0,
    sent_by    INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER,
    note       TEXT,
    added_by   INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_links_job    ON links(job_id);
CREATE INDEX IF NOT EXISTS idx_links_status ON links(job_id, status);
CREATE INDEX IF NOT EXISTS idx_logs_user    ON bot_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_jobs_user    ON jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status  ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_drm_kid      ON drm_keys(kid);
"""


async def init_db(path: str) -> None:
    async with aiosqlite.connect(path) as db:
        await db.executescript(SCHEMA)
        await db.commit()
    logger.info("Database ready: %s", path)


class Database:
    def __init__(self, path: str):
        self.path = path

    # ── users ────────────────────────────────────────────────────────────────

    async def upsert_user(self, uid: int, username: str,
                           first: str, last: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                INSERT INTO users (user_id,username,first_name,last_name,last_seen)
                VALUES (?,?,?,?,datetime('now'))
                ON CONFLICT(user_id) DO UPDATE SET
                  username=excluded.username, first_name=excluded.first_name,
                  last_name=excluded.last_name, last_seen=datetime('now')
            """, (uid, username or "", first or "", last or ""))
            await db.commit()

    async def get_user(self, uid: int) -> Optional[Dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM users WHERE user_id=?", (uid,)) as c:
                r = await c.fetchone()
                return dict(r) if r else None

    async def get_all_users(self, banned: bool = None, limit: int = 200) -> List[Dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            if banned is None:
                q, p = "SELECT * FROM users ORDER BY joined_at DESC LIMIT ?", (limit,)
            else:
                q, p = ("SELECT * FROM users WHERE is_banned=? ORDER BY joined_at DESC LIMIT ?",
                         (int(banned), limit))
            async with db.execute(q, p) as c:
                return [dict(r) for r in await c.fetchall()]

    async def search_user(self, query: str) -> List[Dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            q = "%" + query.lower() + "%"
            async with db.execute("""
                SELECT * FROM users WHERE
                  lower(username) LIKE ? OR lower(first_name) LIKE ?
                  OR CAST(user_id AS TEXT) LIKE ?
                LIMIT 20
            """, (q, q, q)) as c:
                return [dict(r) for r in await c.fetchall()]

    async def ban_user(self, uid: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE users SET is_banned=1 WHERE user_id=?", (uid,))
            await db.commit()

    async def unban_user(self, uid: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE users SET is_banned=0 WHERE user_id=?", (uid,))
            await db.commit()

    async def set_admin(self, uid: int, v: bool) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE users SET is_admin=? WHERE user_id=?", (int(v), uid))
            await db.commit()

    async def whitelist_user(self, uid: int, v: bool = True) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE users SET is_whitelisted=? WHERE user_id=?", (int(v), uid))
            await db.commit()

    async def set_user_language(self, uid: int, lang: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE users SET language=? WHERE user_id=?", (lang, uid))
            await db.commit()

    async def set_notify(self, uid: int, v: bool) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE users SET notify_done=? WHERE user_id=?", (int(v), uid))
            await db.commit()

    async def add_note(self, uid: int, note: str, added_by: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO user_notes (user_id,note,added_by) VALUES (?,?,?)",
                (uid, note, added_by))
            await db.execute("UPDATE users SET notes=? WHERE user_id=?", (note[:200], uid))
            await db.commit()

    async def is_banned(self, uid: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute("SELECT is_banned FROM users WHERE user_id=?", (uid,)) as c:
                r = await c.fetchone(); return bool(r[0]) if r else False

    async def is_admin(self, uid: int) -> bool:
        from config.settings import ADMIN_IDS
        if uid in ADMIN_IDS: return True
        u = await self.get_user(uid)
        return bool(u and u.get("is_admin"))

    async def inc_stats(self, uid: int, files: int = 0, b: int = 0) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                UPDATE users SET total_files=total_files+?, total_bytes=total_bytes+?
                WHERE user_id=?""", (files, b, uid))
            await db.commit()

    # ── jobs ─────────────────────────────────────────────────────────────────

    async def create_job(self, job_id: str, uid: int, chat_id: int,
                          total: int, start: int, pmid: int,
                          source_name: str = "") -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                INSERT INTO jobs (job_id,user_id,chat_id,total_links,start_index,
                  current_index,status,progress_msg_id,source_name)
                VALUES (?,?,?,?,?,?,'running',?,?)
            """, (job_id, uid, chat_id, total, start, start, pmid, source_name))
            await db.execute(
                "UPDATE users SET total_jobs=total_jobs+1 WHERE user_id=?", (uid,))
            await db.commit()

    async def get_job(self, job_id: str) -> Optional[Dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)) as c:
                r = await c.fetchone(); return dict(r) if r else None

    async def get_active_job(self, uid: int) -> Optional[Dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT * FROM jobs WHERE user_id=? AND status IN ('running','paused')
                ORDER BY created_at DESC LIMIT 1""", (uid,)) as c:
                r = await c.fetchone(); return dict(r) if r else None

    async def get_latest_job(self, uid: int) -> Optional[Dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM jobs WHERE user_id=? ORDER BY created_at DESC LIMIT 1",
                (uid,)) as c:
                r = await c.fetchone(); return dict(r) if r else None

    async def get_user_jobs(self, uid: int, limit: int = 10) -> List[Dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM jobs WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (uid, limit)) as c:
                return [dict(r) for r in await c.fetchall()]

    async def get_all_jobs(self, limit: int = 50, status: str = None) -> List[Dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            if status:
                q = "SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC LIMIT ?"
                p = (status, limit)
            else:
                q = "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?"
                p = (limit,)
            async with db.execute(q, p) as c:
                return [dict(r) for r in await c.fetchall()]

    async def update_job(self, job_id: str, cur: int, comp: int, fail: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                UPDATE jobs SET current_index=?,completed_links=?,failed_links=?,
                  updated_at=datetime('now') WHERE job_id=?""", (cur, comp, fail, job_id))
            await db.commit()

    async def set_job_status(self, job_id: str, status: str) -> None:
        ex = ",finished_at=datetime('now')" if status in ("completed","cancelled","failed") else ""
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                f"UPDATE jobs SET status=?,updated_at=datetime('now'){ex} WHERE job_id=?",
                (status, job_id))
            await db.commit()

    # ── links ─────────────────────────────────────────────────────────────────

    async def bulk_insert_links(self, job_id: str, urls: List[str]) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.executemany(
                "INSERT INTO links (job_id,url,line_number) VALUES (?,?,?)",
                [(job_id, u, i + 1) for i, u in enumerate(urls)])
            await db.commit()

    async def get_links(self, job_id: str) -> List[str]:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT url FROM links WHERE job_id=? ORDER BY line_number", (job_id,)) as c:
                return [r[0] for r in await c.fetchall()]

    async def get_failed_links(self, job_id: str) -> List[Dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM links WHERE job_id=? AND status='failed'", (job_id,)) as c:
                return [dict(r) for r in await c.fetchall()]

    async def set_link(self, job_id: str, line: int, status: str,
                        fn: str = None, sz: int = None, mime: str = None,
                        err: str = None, att: int = None) -> None:
        cols = ["status=?", "updated_at=datetime('now')"]; vals = [status]
        for c, v in [("filename",fn),("file_size",sz),("mime_type",mime),
                     ("error_msg",err),("attempts",att)]:
            if v is not None: cols.append(f"{c}=?"); vals.append(v)
        vals += [job_id, line]
        async with aiosqlite.connect(self.path) as db:
            await db.execute(f"UPDATE links SET {','.join(cols)} WHERE job_id=? AND line_number=?", vals)
            await db.commit()

    # ── DRM keys ─────────────────────────────────────────────────────────────

    async def add_drm_key(self, kid: str, key: str, label: str, uid: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO drm_keys (kid,key,label,added_by) VALUES (?,?,?,?)",
                (kid.lower(), key.lower(), label, uid))
            await db.commit()

    async def get_drm_keys(self) -> Dict[str, str]:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute("SELECT kid,key FROM drm_keys") as c:
                return {r[0]: r[1] for r in await c.fetchall()}

    async def list_drm_keys(self) -> List[Dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM drm_keys ORDER BY created_at DESC") as c:
                return [dict(r) for r in await c.fetchall()]

    async def del_drm_key(self, kid: str) -> int:
        async with aiosqlite.connect(self.path) as db:
            c = await db.execute("DELETE FROM drm_keys WHERE kid=?", (kid.lower(),))
            await db.commit()
            return c.rowcount

    # ── cookies ──────────────────────────────────────────────────────────────

    async def save_cookie(self, cookie: str, email: str, label: str, uid: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE cookies SET active=0")
            await db.execute(
                "INSERT INTO cookies (cookie,email,label,added_by,active) VALUES (?,?,?,?,1)",
                (cookie, email, label, uid))
            await db.commit()

    async def get_active_cookie(self) -> Optional[str]:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT cookie FROM cookies WHERE active=1 ORDER BY created_at DESC LIMIT 1"
            ) as c:
                r = await c.fetchone(); return r[0] if r else None

    async def list_cookies(self) -> List[Dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT id,email,label,active,created_at FROM cookies ORDER BY created_at DESC") as c:
                return [dict(r) for r in await c.fetchall()]

    # ── bot config ────────────────────────────────────────────────────────────

    async def set_config(self, key: str, value: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO bot_config (key,value,updated_at) VALUES (?,?,datetime('now'))",
                (key, value))
            await db.commit()

    async def get_config(self, key: str, default: str = "") -> str:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute("SELECT value FROM bot_config WHERE key=?", (key,)) as c:
                r = await c.fetchone(); return r[0] if r else default

    async def get_all_config(self) -> Dict[str, str]:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute("SELECT key,value FROM bot_config") as c:
                return {r[0]: r[1] for r in await c.fetchall()}

    # ── logs ─────────────────────────────────────────────────────────────────

    async def add_log(self, level: str, msg: str,
                       uid: int = None, job_id: str = None) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO bot_logs (user_id,job_id,level,message) VALUES (?,?,?,?)",
                (uid, job_id, level, msg))
            await db.commit()

    async def get_logs(self, uid: int = None, job_id: str = None,
                        level: str = None, limit: int = 30) -> List[Dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            conds, params = [], []
            if uid:    conds.append("user_id=?");  params.append(uid)
            if job_id: conds.append("job_id=?");   params.append(job_id)
            if level:  conds.append("level=?");    params.append(level)
            where = ("WHERE " + " AND ".join(conds)) if conds else ""
            params.append(limit)
            async with db.execute(
                f"SELECT * FROM bot_logs {where} ORDER BY created_at DESC LIMIT ?",
                params) as c:
                return [dict(r) for r in await c.fetchall()]

    async def clear_logs(self, uid: int = None) -> int:
        async with aiosqlite.connect(self.path) as db:
            if uid:
                c = await db.execute("DELETE FROM bot_logs WHERE user_id=?", (uid,))
            else:
                c = await db.execute("DELETE FROM bot_logs")
            await db.commit()
            return c.rowcount

    # ── stats ────────────────────────────────────────────────────────────────

    async def get_stats(self) -> Dict[str, Any]:
        async with aiosqlite.connect(self.path) as db:
            async def sc(sql, *a):
                async with db.execute(sql, a) as c:
                    r = await c.fetchone(); return r[0] if r else 0
            return {
                "total_users":  await sc("SELECT COUNT(*) FROM users"),
                "active_users": await sc("SELECT COUNT(*) FROM users WHERE is_banned=0"),
                "banned_users": await sc("SELECT COUNT(*) FROM users WHERE is_banned=1"),
                "admin_users":  await sc("SELECT COUNT(*) FROM users WHERE is_admin=1"),
                "total_jobs":   await sc("SELECT COUNT(*) FROM jobs"),
                "running_jobs": await sc("SELECT COUNT(*) FROM jobs WHERE status='running'"),
                "paused_jobs":  await sc("SELECT COUNT(*) FROM jobs WHERE status='paused'"),
                "done_jobs":    await sc("SELECT COUNT(*) FROM jobs WHERE status='completed'"),
                "cancelled_jobs":await sc("SELECT COUNT(*) FROM jobs WHERE status='cancelled'"),
                "total_files":  await sc("SELECT COALESCE(SUM(completed_links),0) FROM jobs"),
                "total_failed": await sc("SELECT COALESCE(SUM(failed_links),0) FROM jobs"),
                "total_bytes":  await sc("SELECT COALESCE(SUM(total_bytes),0) FROM users"),
                "drm_keys":     await sc("SELECT COUNT(*) FROM drm_keys"),
                "total_logs":   await sc("SELECT COUNT(*) FROM bot_logs"),
            }

    # ── rate limit ────────────────────────────────────────────────────────────

    async def check_rate(self, uid: int, max_c: int, period: int) -> bool:
        now = datetime.utcnow().isoformat()
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT calls,window_start FROM rate_limits WHERE user_id=?", (uid,)) as c:
                row = await c.fetchone()
            if row is None:
                await db.execute(
                    "INSERT INTO rate_limits (user_id,calls,window_start) VALUES (?,1,?)",
                    (uid, now)); await db.commit(); return True
            calls, ws = row
            elapsed = (datetime.utcnow() - datetime.fromisoformat(ws)).total_seconds()
            if elapsed > period:
                await db.execute(
                    "UPDATE rate_limits SET calls=1,window_start=? WHERE user_id=?", (now, uid))
                await db.commit(); return True
            if calls >= max_c: return False
            await db.execute("UPDATE rate_limits SET calls=calls+1 WHERE user_id=?", (uid,))
            await db.commit(); return True

    # ── broadcast log ─────────────────────────────────────────────────────────

    async def log_broadcast(self, msg: str, sent: int, fail: int, uid: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO broadcasts (message,sent_count,fail_count,sent_by) VALUES (?,?,?,?)",
                (msg[:500], sent, fail, uid))
            await db.commit()
