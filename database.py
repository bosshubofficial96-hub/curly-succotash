"""
database.py - Asynchronous SQLite database layer using aiosqlite.
Handles all database operations for users, jobs, queue, and checkpoint/resume functionality.
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path

from aiosqlite import connect, Connection

from config import Config


class Database:
    def __init__(self, db_url: str):
        # db_url format: "sqlite+aiosqlite:///bot_data.db"
        self.db_path = db_url.replace("sqlite+aiosqlite:///", "")
        self.conn: Optional[Connection] = None

    async def initialize(self) -> None:
        """Create tables and indexes if they don't exist."""
        self.conn = await connect(self.db_path)
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA foreign_keys=ON")

        # Users table
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                is_admin BOOLEAN DEFAULT 0,
                is_banned BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP
            )
        """)

        # Jobs table (each uploaded .txt file becomes a job)
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                original_filename TEXT,
                total_links INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        """)

        # Queue items (individual URLs from the .txt file)
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS queue_items (
                item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                url TEXT NOT NULL,
                position INTEGER NOT NULL,
                status TEXT DEFAULT 'pending',
                retry_count INTEGER DEFAULT 0,
                error_message TEXT,
                local_file_path TEXT,
                file_size INTEGER,
                mime_type TEXT,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
            )
        """)

        # Checkpoints (for resume functionality)
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS checkpoints (
                user_id INTEGER PRIMARY KEY,
                job_id INTEGER NOT NULL,
                last_completed_position INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        """)

        # Processing logs
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS processing_logs (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                job_id INTEGER,
                item_id INTEGER,
                log_type TEXT,
                message TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Statistics (daily aggregates)
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS stats_daily (
                date DATE PRIMARY KEY,
                total_users INTEGER DEFAULT 0,
                total_jobs INTEGER DEFAULT 0,
                total_downloads_success INTEGER DEFAULT 0,
                total_downloads_failed INTEGER DEFAULT 0,
                total_bytes_downloaded INTEGER DEFAULT 0
            )
        """)

        # Create indexes for performance
        await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_items_job_id ON queue_items(job_id)")
        await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_items_status ON queue_items(status)")
        await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_user_id ON jobs(user_id)")
        await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_checkpoints_user_id ON checkpoints(user_id)")
        await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON processing_logs(timestamp)")

        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    # ==================== User Management ====================
    async def register_user(self, user_id: int, username: str = None, first_name: str = None, last_name: str = None) -> None:
        """Register or update user."""
        is_admin = user_id in Config.ADMIN_IDS
        await self.conn.execute("""
            INSERT INTO users (user_id, username, first_name, last_name, is_admin, last_active)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                last_active = excluded.last_active
        """, (user_id, username, first_name, last_name, is_admin, datetime.utcnow()))
        await self.conn.commit()

    async def is_user_banned(self, user_id: int) -> bool:
        cursor = await self.conn.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return row is not None and row[0] == 1

    async def ban_user(self, user_id: int) -> None:
        await self.conn.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (user_id,))
        await self.conn.commit()

    async def unban_user(self, user_id: int) -> None:
        await self.conn.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (user_id,))
        await self.conn.commit()

    async def get_all_users(self) -> List[Dict]:
        cursor = await self.conn.execute("SELECT user_id, username, first_name, last_name, is_admin, is_banned, created_at FROM users")
        rows = await cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in rows]

    # ==================== Jobs Management ====================
    async def create_job(self, user_id: int, original_filename: str, total_links: int) -> int:
        cursor = await self.conn.execute("""
            INSERT INTO jobs (user_id, original_filename, total_links, status)
            VALUES (?, ?, ?, 'pending')
        """, (user_id, original_filename, total_links))
        await self.conn.commit()
        return cursor.lastrowid

    async def get_job(self, job_id: int) -> Optional[Dict]:
        cursor = await self.conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        columns = [desc[0] for desc in cursor.description]
        return dict(zip(columns, row))

    async def update_job_status(self, job_id: int, status: str) -> None:
        completed_at = datetime.utcnow() if status == "completed" else None
        await self.conn.execute("""
            UPDATE jobs SET status = ?, completed_at = ? WHERE job_id = ?
        """, (status, completed_at, job_id))
        await self.conn.commit()

    async def get_user_jobs(self, user_id: int, limit: int = 10) -> List[Dict]:
        cursor = await self.conn.execute("""
            SELECT * FROM jobs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?
        """, (user_id, limit))
        rows = await cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in rows]

    # ==================== Queue Items Management ====================
    async def add_queue_items(self, job_id: int, urls: List[str]) -> None:
        """Insert multiple URLs into queue_items with sequential positions."""
        cursor = await self.conn.execute("SELECT COALESCE(MAX(position), 0) FROM queue_items WHERE job_id = ?", (job_id,))
        max_pos = (await cursor.fetchone())[0]
        for idx, url in enumerate(urls):
            position = max_pos + idx + 1
            await self.conn.execute("""
                INSERT INTO queue_items (job_id, url, position, status)
                VALUES (?, ?, ?, 'pending')
            """, (job_id, url, position))
        await self.conn.commit()

    async def get_next_pending_item(self, job_id: int) -> Optional[Dict]:
        """Get the earliest pending item for the job (sequential)."""
        cursor = await self.conn.execute("""
            SELECT * FROM queue_items
            WHERE job_id = ? AND status = 'pending'
            ORDER BY position ASC
            LIMIT 1
        """, (job_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        columns = [desc[0] for desc in cursor.description]
        return dict(zip(columns, row))

    async def update_item_status(
        self, item_id: int, status: str, error_message: str = None,
        local_file_path: str = None, file_size: int = None, mime_type: str = None
    ) -> None:
        await self.conn.execute("""
            UPDATE queue_items
            SET status = ?,
                error_message = COALESCE(?, error_message),
                local_file_path = COALESCE(?, local_file_path),
                file_size = COALESCE(?, file_size),
                mime_type = COALESCE(?, mime_type),
                completed_at = CASE WHEN ? IN ('completed', 'failed', 'skipped') THEN ? ELSE completed_at END
            WHERE item_id = ?
        """, (status, error_message, local_file_path, file_size, mime_type, status, datetime.utcnow(), item_id))
        await self.conn.commit()

    async def increment_retry(self, item_id: int) -> int:
        cursor = await self.conn.execute("""
            UPDATE queue_items SET retry_count = retry_count + 1
            WHERE item_id = ?
            RETURNING retry_count
        """, (item_id,))
        row = await cursor.fetchone()
        await self.conn.commit()
        return row[0] if row else 0

    async def get_job_statistics(self, job_id: int) -> Dict:
        cursor = await self.conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
                SUM(CASE WHEN status = 'skipped' THEN 1 ELSE 0 END) as skipped,
                SUM(CASE WHEN status IN ('pending', 'downloading', 'uploading') THEN 1 ELSE 0 END) as pending
            FROM queue_items
            WHERE job_id = ?
        """, (job_id,))
        row = await cursor.fetchone()
        return {
            "total": row[0] or 0,
            "completed": row[1] or 0,
            "failed": row[2] or 0,
            "skipped": row[3] or 0,
            "pending": row[4] or 0,
        }

    # ==================== Checkpoints (Resume) ====================
    async def save_checkpoint(self, user_id: int, job_id: int, last_completed_position: int) -> None:
        await self.conn.execute("""
            INSERT INTO checkpoints (user_id, job_id, last_completed_position, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                job_id = excluded.job_id,
                last_completed_position = excluded.last_completed_position,
                updated_at = excluded.updated_at
        """, (user_id, job_id, last_completed_position, datetime.utcnow()))
        await self.conn.commit()

    async def get_checkpoint(self, user_id: int) -> Optional[Dict]:
        cursor = await self.conn.execute("SELECT * FROM checkpoints WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        columns = [desc[0] for desc in cursor.description]
        return dict(zip(columns, row))

    async def clear_checkpoint(self, user_id: int) -> None:
        await self.conn.execute("DELETE FROM checkpoints WHERE user_id = ?", (user_id,))
        await self.conn.commit()

    # ==================== Logging ====================
    async def add_log(self, user_id: int, job_id: int, item_id: int, log_type: str, message: str) -> None:
        await self.conn.execute("""
            INSERT INTO processing_logs (user_id, job_id, item_id, log_type, message)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, job_id, item_id, log_type, message))
        await self.conn.commit()

    async def get_recent_logs(self, user_id: int = None, limit: int = 100) -> List[Dict]:
        if user_id:
            cursor = await self.conn.execute("""
                SELECT * FROM processing_logs WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?
            """, (user_id, limit))
        else:
            cursor = await self.conn.execute("SELECT * FROM processing_logs ORDER BY timestamp DESC LIMIT ?", (limit,))
        rows = await cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in rows]

    async def get_error_logs(self, limit: int = 50) -> List[Dict]:
        cursor = await self.conn.execute("""
            SELECT * FROM processing_logs WHERE log_type = 'error' ORDER BY timestamp DESC LIMIT ?
        """, (limit,))
        rows = await cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in rows]

    # ==================== Statistics ====================
    async def update_daily_stats(self) -> None:
        today = datetime.utcnow().date()
        cursor = await self.conn.execute("SELECT COUNT(DISTINCT user_id) FROM users")
        total_users = (await cursor.fetchone())[0]
        cursor = await self.conn.execute("SELECT COUNT(*) FROM jobs WHERE date(created_at) = ?", (today,))
        total_jobs = (await cursor.fetchone())[0]
        cursor = await self.conn.execute("""
            SELECT COUNT(*), COALESCE(SUM(file_size), 0) FROM queue_items
            WHERE status = 'completed' AND date(completed_at) = ?
        """, (today,))
        downloads_success, bytes_downloaded = await cursor.fetchone()
        cursor = await self.conn.execute("SELECT COUNT(*) FROM queue_items WHERE status = 'failed' AND date(completed_at) = ?", (today,))
        downloads_failed = (await cursor.fetchone())[0]

        await self.conn.execute("""
            INSERT INTO stats_daily (date, total_users, total_jobs, total_downloads_success, total_downloads_failed, total_bytes_downloaded)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                total_users = excluded.total_users,
                total_jobs = excluded.total_jobs,
                total_downloads_success = excluded.total_downloads_success,
                total_downloads_failed = excluded.total_downloads_failed,
                total_bytes_downloaded = excluded.total_bytes_downloaded
        """, (today, total_users, total_jobs, downloads_success, downloads_failed, bytes_downloaded))
        await self.conn.commit()
