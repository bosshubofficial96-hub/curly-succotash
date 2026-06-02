import sqlite3
import asyncio
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager
import aiosqlite

logger = logging.getLogger(__name__)


async def init_db(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT,
                first_name  TEXT,
                last_name   TEXT,
                is_banned   INTEGER DEFAULT 0,
                is_admin    INTEGER DEFAULT 0,
                joined_at   TEXT DEFAULT (datetime('now')),
                last_seen   TEXT DEFAULT (datetime('now')),
                total_jobs  INTEGER DEFAULT 0,
                total_files INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS jobs (
                job_id          TEXT PRIMARY KEY,
                user_id         INTEGER NOT NULL,
                status          TEXT DEFAULT 'pending',
                total_links     INTEGER DEFAULT 0,
                processed_links INTEGER DEFAULT 0,
                completed_links INTEGER DEFAULT 0,
                failed_links    INTEGER DEFAULT 0,
                start_index     INTEGER DEFAULT 0,
                current_index   INTEGER DEFAULT 0,
                created_at      TEXT DEFAULT (datetime('now')),
                updated_at      TEXT DEFAULT (datetime('now')),
                finished_at     TEXT,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS links (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id      TEXT NOT NULL,
                url         TEXT NOT NULL,
                line_number INTEGER NOT NULL,
                status      TEXT DEFAULT 'pending',
                filename    TEXT,
                file_size   INTEGER,
                mime_type   TEXT,
                attempts    INTEGER DEFAULT 0,
                error_msg   TEXT,
                created_at  TEXT DEFAULT (datetime('now')),
                updated_at  TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (job_id) REFERENCES jobs(job_id)
            );

            CREATE TABLE IF NOT EXISTS logs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER,
                job_id     TEXT,
                level      TEXT,
                message    TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS rate_limits (
                user_id    INTEGER PRIMARY KEY,
                calls      INTEGER DEFAULT 0,
                window_start TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_links_job_id  ON links(job_id);
            CREATE INDEX IF NOT EXISTS idx_links_status  ON links(job_id, status);
            CREATE INDEX IF NOT EXISTS idx_logs_user     ON logs(user_id);
            CREATE INDEX IF NOT EXISTS idx_jobs_user     ON jobs(user_id);
        """)
        await db.commit()
    logger.info("Database initialised at %s", db_path)


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path

    # ------------------------------------------------------------------ users

    async def upsert_user(self, user_id: int, username: str, first_name: str, last_name: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO users (user_id, username, first_name, last_name, last_seen)
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(user_id) DO UPDATE SET
                    username   = excluded.username,
                    first_name = excluded.first_name,
                    last_name  = excluded.last_name,
                    last_seen  = datetime('now')
            """, (user_id, username, first_name, last_name))
            await db.commit()

    async def get_user(self, user_id: int) -> Optional[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_all_users(self) -> List[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM users ORDER BY joined_at DESC") as cur:
                return [dict(r) for r in await cur.fetchall()]

    async def ban_user(self, user_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (user_id,))
            await db.commit()

    async def unban_user(self, user_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (user_id,))
            await db.commit()

    async def set_admin(self, user_id: int, is_admin: bool) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE users SET is_admin = ? WHERE user_id = ?", (int(is_admin), user_id))
            await db.commit()

    async def is_banned(self, user_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,)) as cur:
                row = await cur.fetchone()
                return bool(row[0]) if row else False

    # ------------------------------------------------------------------- jobs

    async def create_job(self, job_id: str, user_id: int, total_links: int, start_index: int = 0) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO jobs (job_id, user_id, total_links, start_index, current_index, status)
                VALUES (?, ?, ?, ?, ?, 'running')
            """, (job_id, user_id, total_links, start_index, start_index))
            await db.execute(
                "UPDATE users SET total_jobs = total_jobs + 1 WHERE user_id = ?", (user_id,)
            )
            await db.commit()

    async def get_job(self, job_id: str) -> Optional[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_user_active_job(self, user_id: int) -> Optional[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT * FROM jobs WHERE user_id = ? AND status IN ('running','paused')
                ORDER BY created_at DESC LIMIT 1
            """, (user_id,)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_user_latest_job(self, user_id: int) -> Optional[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT * FROM jobs WHERE user_id = ?
                ORDER BY created_at DESC LIMIT 1
            """, (user_id,)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def update_job_progress(self, job_id: str, current_index: int,
                                   completed: int, failed: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE jobs SET
                    current_index   = ?,
                    processed_links = ? + ?,
                    completed_links = ?,
                    failed_links    = ?,
                    updated_at      = datetime('now')
                WHERE job_id = ?
            """, (current_index, completed, failed, completed, failed, job_id))
            await db.commit()

    async def finish_job(self, job_id: str, status: str = 'completed') -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE jobs SET status = ?, finished_at = datetime('now'), updated_at = datetime('now')
                WHERE job_id = ?
            """, (status, job_id))
            await db.commit()

    async def pause_job(self, job_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE jobs SET status = 'paused', updated_at = datetime('now') WHERE job_id = ?",
                (job_id,)
            )
            await db.commit()

    async def resume_job(self, job_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE jobs SET status = 'running', updated_at = datetime('now') WHERE job_id = ?",
                (job_id,)
            )
            await db.commit()

    async def get_all_jobs(self, limit: int = 50) -> List[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ) as cur:
                return [dict(r) for r in await cur.fetchall()]

    # ------------------------------------------------------------------ links

    async def bulk_insert_links(self, job_id: str, urls: List[str]) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany(
                "INSERT INTO links (job_id, url, line_number) VALUES (?, ?, ?)",
                [(job_id, url, i + 1) for i, url in enumerate(urls)]
            )
            await db.commit()

    async def get_link(self, job_id: str, line_number: int) -> Optional[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM links WHERE job_id = ? AND line_number = ?", (job_id, line_number)
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def update_link_status(self, job_id: str, line_number: int, status: str,
                                  filename: str = None, file_size: int = None,
                                  mime_type: str = None, error_msg: str = None,
                                  attempts: int = None) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            updates = ["status = ?", "updated_at = datetime('now')"]
            params: list = [status]
            if filename is not None:
                updates.append("filename = ?");  params.append(filename)
            if file_size is not None:
                updates.append("file_size = ?"); params.append(file_size)
            if mime_type is not None:
                updates.append("mime_type = ?"); params.append(mime_type)
            if error_msg is not None:
                updates.append("error_msg = ?"); params.append(error_msg)
            if attempts is not None:
                updates.append("attempts = ?");  params.append(attempts)
            params += [job_id, line_number]
            await db.execute(
                f"UPDATE links SET {', '.join(updates)} WHERE job_id = ? AND line_number = ?",
                params
            )
            if status == 'completed':
                job = await db.execute("SELECT user_id FROM jobs WHERE job_id = ?", (job_id,))
                row = await job.fetchone()
                if row:
                    await db.execute(
                        "UPDATE users SET total_files = total_files + 1 WHERE user_id = ?", (row[0],)
                    )
            await db.commit()

    async def get_failed_links(self, job_id: str) -> List[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM links WHERE job_id = ? AND status = 'failed'", (job_id,)
            ) as cur:
                return [dict(r) for r in await cur.fetchall()]

    # ------------------------------------------------------------------- logs

    async def add_log(self, level: str, message: str,
                      user_id: int = None, job_id: str = None) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO logs (user_id, job_id, level, message) VALUES (?, ?, ?, ?)",
                (user_id, job_id, level, message)
            )
            await db.commit()

    async def get_logs(self, user_id: int = None, job_id: str = None,
                       limit: int = 50) -> List[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if job_id:
                q = "SELECT * FROM logs WHERE job_id = ? ORDER BY created_at DESC LIMIT ?"
                p = (job_id, limit)
            elif user_id:
                q = "SELECT * FROM logs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?"
                p = (user_id, limit)
            else:
                q = "SELECT * FROM logs ORDER BY created_at DESC LIMIT ?"
                p = (limit,)
            async with db.execute(q, p) as cur:
                return [dict(r) for r in await cur.fetchall()]

    # ------------------------------------------------------------------ stats

    async def get_stats(self) -> Dict:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM users") as cur:
                total_users = (await cur.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM users WHERE is_banned = 0") as cur:
                active_users = (await cur.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM jobs") as cur:
                total_jobs = (await cur.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM jobs WHERE status = 'running'") as cur:
                running_jobs = (await cur.fetchone())[0]
            async with db.execute("SELECT SUM(completed_links) FROM jobs") as cur:
                total_files = (await cur.fetchone())[0] or 0
            async with db.execute("SELECT SUM(failed_links) FROM jobs") as cur:
                total_failed = (await cur.fetchone())[0] or 0
        return {
            "total_users": total_users,
            "active_users": active_users,
            "total_jobs": total_jobs,
            "running_jobs": running_jobs,
            "total_files": total_files,
            "total_failed": total_failed,
        }

    # ----------------------------------------------------------- rate limiting

    async def check_rate_limit(self, user_id: int, max_calls: int, period: int) -> bool:
        """Returns True if allowed, False if rate-limited."""
        now = datetime.utcnow().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT calls, window_start FROM rate_limits WHERE user_id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                await db.execute(
                    "INSERT INTO rate_limits (user_id, calls, window_start) VALUES (?, 1, ?)",
                    (user_id, now)
                )
                await db.commit()
                return True
            calls, window_start = row
            window_dt = datetime.fromisoformat(window_start)
            elapsed = (datetime.utcnow() - window_dt).total_seconds()
            if elapsed > period:
                await db.execute(
                    "UPDATE rate_limits SET calls = 1, window_start = ? WHERE user_id = ?",
                    (now, user_id)
                )
                await db.commit()
                return True
            if calls >= max_calls:
                return False
            await db.execute(
                "UPDATE rate_limits SET calls = calls + 1 WHERE user_id = ?", (user_id,)
            )
            await db.commit()
            return True
