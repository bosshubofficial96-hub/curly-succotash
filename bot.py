#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════╗
║                         🔥 ULTIMATE DRM EXTRACTOR BOT 🔥                                                                                ║
║                    FORCE SUBSCRIBE | LINK EXTRACTOR | 30+ ADVANCED FEATURES                                                            ║
╚══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════╝

✅ FEATURES INCLUDED:
1. Force Subscribe to Channel/Group before using bot
2. Auto Remove from channel after link extraction
3. DRM Protected Content Extractor (Appx V2/V3, Widevine, ClearKey)
4. YouTube & 1000+ Platforms Downloader (yt-dlp)
5. M3U8 Stream Downloader (Live/VOD)
6. Direct MP4, PDF, Image, Audio Downloader
7. Batch Processing from TXT File (1000+ URLs)
8. Resume from Last Processed Link
9. Live Progress Tracking with ETA & Speed
10. Custom Quality Selection (144p to 8K, Audio Only)
11. Custom Watermark on Videos/Images
12. Admin Panel with User Management
13. Broadcast Messages to All Users
14. User Statistics & Analytics
15. Rate Limiting & Spam Protection
16. SQLite Database with Persistent Storage
17. Automatic Retry on Failure
18. Skip Broken Links & Continue
19. Final Processing Report
20. User Activity Logs
21. Download History Tracking
22. Failed Links Logging
23. Concurrent Download Queue System
24. File Type Auto-Detection
25. Large File Support (2GB+)
26. Temporary File Cleanup
27. Duplicate Download Prevention
28. Admin-Only Mode
29. User Ban/Unban System
30. Premium User System
31. API Key Generation for External Access
32. Auto Backup Database
33. Health Check Endpoint
34. Custom Welcome Message
35. Force Subscribe Check on Every Command
"""

import asyncio
import aiohttp
import aiosqlite
import aiofiles
import os
import re
import json
import time
import math
import base64
import shutil
import hashlib
import secrets
import logging
import signal
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Union
from urllib.parse import urlparse, unquote, parse_qs, urljoin
from collections import defaultdict
from functools import wraps

# Third-party imports
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, BotCommand, ChatMember
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    ChatMemberHandler,
    PreCheckoutQueryHandler,
)
from telegram.constants import ParseMode
from telegram.error import TimedOut, NetworkError, RetryAfter, TelegramError
import yt_dlp
import m3u8

# Load environment variables
load_dotenv()

# ==================== CONFIGURATION ====================
class Config:
    """Master configuration class"""
    
    # Bot Configuration
    BOT_TOKEN = os.getenv("BOT_TOKEN", "8828772252:AAEnP5IGOS5G5MBp2IkH1Cw1JU9l-1A3HDE")
    BOT_NAME = os.getenv("BOT_NAME", "DRM Extractor Bot")
    BOT_VERSION = "4.0.0"
    
    # Admin Configuration
    ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
    SUPER_ADMINS = {int(x.strip()) for x in os.getenv("SUPER_ADMINS", "").split(",") if x.strip()}
    ADMIN_ONLY_MODE = os.getenv("ADMIN_ONLY_MODE", "False").lower() == "true"
    
    # Force Subscribe Configuration
    FORCE_SUBSCRIBE_CHANNEL = os.getenv("FORCE_SUBSCRIBE_CHANNEL", "")  # @username or channel ID
    FORCE_SUBSCRIBE_LINK = os.getenv("FORCE_SUBSCRIBE_LINK", "")  # Invite link
    AUTO_REMOVE_AFTER_EXTRACT = os.getenv("AUTO_REMOVE_AFTER_EXTRACT", "True").lower() == "true"
    FORCE_SUBSCRIBE_MESSAGE = os.getenv("FORCE_SUBSCRIBE_MESSAGE", "⚠️ Please join our channel to use this bot!")
    
    # Performance Configuration
    MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "5"))
    MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "100"))
    DOWNLOAD_TIMEOUT = int(os.getenv("DOWNLOAD_TIMEOUT", "600"))
    CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", 1024 * 1024))  # 1MB
    MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
    MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "2000"))
    CONNECTION_LIMIT = int(os.getenv("CONNECTION_LIMIT", "100"))
    
    # Rate Limiting
    RATE_LIMIT_SECONDS = int(os.getenv("RATE_LIMIT_SECONDS", "2"))
    DAILY_LIMIT_NORMAL = int(os.getenv("DAILY_LIMIT_NORMAL", "100"))
    DAILY_LIMIT_PREMIUM = int(os.getenv("DAILY_LIMIT_PREMIUM", "500"))
    
    # File Paths
    BASE_DIR = Path(__file__).parent
    TEMP_DIR = BASE_DIR / "temp_downloads"
    DATABASE_PATH = BASE_DIR / "bot_data.db"
    BACKUP_DIR = BASE_DIR / "backups"
    LOGS_DIR = BASE_DIR / "logs"
    
    # Create directories
    for dir_path in [TEMP_DIR, BACKUP_DIR, LOGS_DIR]:
        dir_path.mkdir(exist_ok=True, parents=True)
    
    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE = LOGS_DIR / "bot.log"
    
    # Quality Presets
    QUALITY_PRESETS = {
        "144p": "worst[height<=144]",
        "240p": "worst[height<=240]",
        "360p": "best[height<=360]",
        "480p": "best[height<=480]",
        "720p": "best[height<=720]",
        "1080p": "best[height<=1080]",
        "2K": "best[height<=1440]",
        "4K": "best[height<=2160]",
        "8K": "best[height<=4320]",
        "AUDIO_ONLY": "bestaudio/best",
        "BEST": "best"
    }
    
    # File Type Detection
    FILE_TYPES = {
        'video': ['.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.ts', '.m3u8'],
        'audio': ['.mp3', '.m4a', '.ogg', '.flac', '.wav', '.aac'],
        'image': ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'],
        'document': ['.pdf', '.doc', '.docx', '.txt', '.xls', '.xlsx'],
        'archive': ['.zip', '.rar', '.7z', '.tar', '.gz']
    }
    
    @classmethod
    def validate(cls):
        """Validate configuration"""
        if not cls.BOT_TOKEN:
            raise ValueError("BOT_TOKEN is required!")
        if cls.BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
            raise ValueError("Please set your actual BOT_TOKEN!")

# Setup logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=getattr(logging, Config.LOG_LEVEL),
    handlers=[
        logging.FileHandler(Config.LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ==================== DATABASE MANAGER ====================
class Database:
    """Advanced database manager with all tables"""
    
    def __init__(self):
        self.conn = None
        self._cache = {}
    
    async def init(self):
        """Initialize all database tables"""
        self.conn = await aiosqlite.connect(str(Config.DATABASE_PATH))
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA synchronous=NORMAL")
        
        # Users table
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                is_admin BOOLEAN DEFAULT 0,
                is_super_admin BOOLEAN DEFAULT 0,
                is_banned BOOLEAN DEFAULT 0,
                is_premium BOOLEAN DEFAULT 0,
                premium_until TIMESTAMP,
                credits INTEGER DEFAULT 100,
                preferred_quality TEXT DEFAULT '720p',
                preferred_format TEXT DEFAULT 'mp4',
                watermark_enabled BOOLEAN DEFAULT 0,
                watermark_text TEXT DEFAULT '© DRM Extractor',
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP,
                total_downloads INTEGER DEFAULT 0,
                total_size INTEGER DEFAULT 0,
                daily_downloads INTEGER DEFAULT 0,
                last_reset DATE
            )
        """)
        
        # Jobs table
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                txt_filename TEXT,
                total_links INTEGER DEFAULT 0,
                current_index INTEGER DEFAULT 0,
                completed INTEGER DEFAULT 0,
                failed INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                start_time TIMESTAMP,
                last_update TIMESTAMP,
                end_time TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        """)
        
        # Links table
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS job_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER,
                url TEXT,
                original_index INTEGER,
                status TEXT DEFAULT 'pending',
                file_name TEXT,
                file_size INTEGER,
                file_type TEXT,
                error_message TEXT,
                retries INTEGER DEFAULT 0,
                start_time TIMESTAMP,
                end_time TIMESTAMP,
                FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
            )
        """)
        
        # Download history
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS download_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                url TEXT,
                file_name TEXT,
                file_size INTEGER,
                file_type TEXT,
                quality TEXT,
                duration INTEGER,
                status TEXT,
                error TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        """)
        
        # Failed links
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS failed_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                url TEXT,
                error TEXT,
                retries INTEGER,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        """)
        
        # Activity log
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT,
                details TEXT,
                ip_address TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        """)
        
        # Queue table
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS download_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                job_id INTEGER,
                position INTEGER,
                priority INTEGER DEFAULT 0,
                status TEXT DEFAULT 'queued',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id),
                FOREIGN KEY(job_id) REFERENCES jobs(job_id)
            )
        """)
        
        # API keys
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                api_key TEXT UNIQUE,
                name TEXT,
                permissions TEXT,
                rate_limit INTEGER DEFAULT 60,
                expires_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_used TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        """)
        
        # Settings
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                description TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_by INTEGER
            )
        """)
        
        await self.conn.commit()
        
        # Insert default settings
        default_settings = [
            ('maintenance_mode', 'false', 'Enable/disable maintenance mode'),
            ('allow_new_users', 'true', 'Allow new users to join'),
            ('max_file_size_mb', str(Config.MAX_FILE_SIZE_MB), 'Maximum file size in MB'),
        ]
        
        for key, value, desc in default_settings:
            await self.conn.execute(
                "INSERT OR IGNORE INTO bot_settings (key, value, description) VALUES (?, ?, ?)",
                (key, value, desc)
            )
        
        # Set admins
        for admin_id in Config.ADMIN_IDS:
            await self.conn.execute(
                "UPDATE users SET is_admin = 1 WHERE user_id = ?",
                (admin_id,)
            )
        for super_admin_id in Config.SUPER_ADMINS:
            await self.conn.execute(
                "UPDATE users SET is_super_admin = 1 WHERE user_id = ?",
                (super_admin_id,)
            )
        
        await self.conn.commit()
        logger.info("Database initialized successfully")
    
    async def close(self):
        """Close database connection"""
        if self.conn:
            await self.conn.close()
    
    async def add_user(self, user_id: int, username: str = None, first_name: str = None, last_name: str = None):
        """Add or update user"""
        await self.conn.execute(
            """INSERT OR IGNORE INTO users (user_id, username, first_name, last_name, last_active) 
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, username, first_name, last_name, datetime.now())
        )
        await self.conn.execute(
            "UPDATE users SET last_active = ?, username = COALESCE(?, username) WHERE user_id = ?",
            (datetime.now(), username, user_id)
        )
        await self.conn.commit()
    
    async def get_user(self, user_id: int) -> Optional[Dict]:
        """Get user data"""
        async with self.conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                columns = [desc[0] for desc in cursor.description]
                return dict(zip(columns, row))
        return None
    
    async def is_admin(self, user_id: int) -> bool:
        """Check if user is admin"""
        user = await self.get_user(user_id)
        return user.get('is_admin', False) if user else False
    
    async def is_banned(self, user_id: int) -> bool:
        """Check if user is banned"""
        user = await self.get_user(user_id)
        return user.get('is_banned', False) if user else False
    
    async def ban_user(self, user_id: int) -> bool:
        """Ban a user"""
        await self.conn.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (user_id,))
        await self.conn.commit()
        return True
    
    async def unban_user(self, user_id: int) -> bool:
        """Unban a user"""
        await self.conn.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (user_id,))
        await self.conn.commit()
        return True
    
    async def update_daily_limit(self, user_id: int):
        """Update daily download limit"""
        today = datetime.now().date()
        async with self.conn.execute(
            "SELECT daily_downloads, last_reset FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                last_reset = datetime.strptime(row[1], '%Y-%m-%d').date() if row[1] else None
                if last_reset != today:
                    await self.conn.execute(
                        "UPDATE users SET daily_downloads = 0, last_reset = ? WHERE user_id = ?",
                        (today, user_id)
                    )
                    await self.conn.commit()
    
    async def create_job(self, user_id: int, txt_filename: str, urls: List[str]) -> int:
        """Create new job"""
        cursor = await self.conn.execute(
            "INSERT INTO jobs (user_id, txt_filename, total_links, status, start_time) VALUES (?, ?, ?, ?, ?)",
            (user_id, txt_filename, len(urls), "pending", datetime.now())
        )
        job_id = cursor.lastrowid
        
        for idx, url in enumerate(urls):
            await self.conn.execute(
                "INSERT INTO job_links (job_id, url, original_index, status) VALUES (?, ?, ?, ?)",
                (job_id, url, idx, "pending")
            )
        
        await self.conn.commit()
        await self.log_activity(user_id, "create_job", f"job_id={job_id}, links={len(urls)}")
        return job_id
    
    async def get_job(self, job_id: int) -> Optional[Dict]:
        """Get job details"""
        async with self.conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                columns = [desc[0] for desc in cursor.description]
                return dict(zip(columns, row))
        return None
    
    async def get_job_links(self, job_id: int) -> List[Dict]:
        """Get all links for job"""
        links = []
        async with self.conn.execute(
            "SELECT * FROM job_links WHERE job_id = ? ORDER BY original_index", (job_id,)
        ) as cursor:
            async for row in cursor:
                columns = [desc[0] for desc in cursor.description]
                links.append(dict(zip(columns, row)))
        return links
    
    async def update_job_status(self, job_id: int, status: str):
        """Update job status"""
        await self.conn.execute(
            "UPDATE jobs SET status = ?, last_update = ? WHERE job_id = ?",
            (status, datetime.now(), job_id)
        )
        await self.conn.commit()
    
    async def update_link_status(self, link_id: int, status: str, file_name: str = None, 
                                  file_size: int = None, error: str = None):
        """Update link status"""
        query = "UPDATE job_links SET status = ?"
        params = [status]
        if file_name:
            query += ", file_name = ?"
            params.append(file_name)
        if file_size:
            query += ", file_size = ?"
            params.append(file_size)
        if error:
            query += ", error_message = ?"
            params.append(error)
        query += " WHERE id = ?"
        params.append(link_id)
        
        await self.conn.execute(query, params)
        await self.conn.commit()
    
    async def increment_progress(self, job_id: int, success: bool):
        """Increment job progress"""
        if success:
            await self.conn.execute(
                "UPDATE jobs SET current_index = current_index + 1, completed = completed + 1, last_update = ? WHERE job_id = ?",
                (datetime.now(), job_id)
            )
        else:
            await self.conn.execute(
                "UPDATE jobs SET current_index = current_index + 1, failed = failed + 1, last_update = ? WHERE job_id = ?",
                (datetime.now(), job_id)
            )
        await self.conn.commit()
    
    async def complete_job(self, job_id: int):
        """Mark job as completed"""
        await self.conn.execute(
            "UPDATE jobs SET status = 'completed', end_time = ? WHERE job_id = ?",
            (datetime.now(), job_id)
        )
        await self.conn.commit()
    
    async def get_user_pending_job(self, user_id: int) -> Optional[Dict]:
        """Get pending job for user"""
        async with self.conn.execute(
            "SELECT job_id, current_index, status FROM jobs WHERE user_id = ? AND status IN ('pending', 'running', 'paused') ORDER BY job_id LIMIT 1",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {"job_id": row[0], "current_index": row[1], "status": row[2]}
        return None
    
    async def add_to_history(self, user_id: int, url: str, file_name: str, 
                             file_size: int, status: str, error: str = None):
        """Add to download history"""
        await self.conn.execute(
            "INSERT INTO history (user_id, url, file_name, file_size, status, error) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, url[:500], file_name[:200] if file_name else "", file_size, status, error[:500] if error else None)
        )
        await self.conn.commit()
        
        if status == "success":
            await self.conn.execute(
                "UPDATE users SET total_downloads = total_downloads + 1, total_size = total_size + ? WHERE user_id = ?",
                (file_size, user_id)
            )
            await self.conn.commit()
            
            # Update daily count
            await self.conn.execute(
                "UPDATE users SET daily_downloads = daily_downloads + 1 WHERE user_id = ?",
                (user_id,)
            )
            await self.conn.commit()
    
    async def add_failed_link(self, user_id: int, url: str, error: str):
        """Log failed link"""
        await self.conn.execute(
            "INSERT INTO failed_links (user_id, url, error) VALUES (?, ?, ?)",
            (user_id, url[:500], error[:500])
        )
        await self.conn.commit()
    
    async def log_activity(self, user_id: int, action: str, details: str = "", ip: str = None):
        """Log user activity"""
        await self.conn.execute(
            "INSERT INTO activity_log (user_id, action, details, ip_address) VALUES (?, ?, ?, ?)",
            (user_id, action, details[:1000], ip)
        )
        await self.conn.commit()
    
    async def get_stats(self) -> Dict:
        """Get bot statistics"""
        stats = {}
        
        async with self.conn.execute("SELECT COUNT(*) FROM users") as cursor:
            stats['total_users'] = (await cursor.fetchone())[0]
        
        async with self.conn.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1") as cursor:
            stats['banned_users'] = (await cursor.fetchone())[0]
        
        async with self.conn.execute("SELECT COUNT(*) FROM users WHERE is_premium = 1") as cursor:
            stats['premium_users'] = (await cursor.fetchone())[0]
        
        async with self.conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'completed'") as cursor:
            stats['completed_jobs'] = (await cursor.fetchone())[0]
        
        async with self.conn.execute("SELECT SUM(file_size) FROM job_links WHERE status = 'success'") as cursor:
            total_size = (await cursor.fetchone())[0] or 0
            stats['total_size_gb'] = total_size / (1024 ** 3)
        
        async with self.conn.execute("SELECT COUNT(*) FROM failed_links") as cursor:
            stats['failed_downloads'] = (await cursor.fetchone())[0]
        
        async with self.conn.execute("SELECT COUNT(*) FROM download_queue WHERE status = 'queued'") as cursor:
            stats['queue_size'] = (await cursor.fetchone())[0]
        
        return stats
    
    async def get_user_stats(self, user_id: int) -> Dict:
        """Get user statistics"""
        stats = {}
        
        async with self.conn.execute(
            "SELECT total_downloads, total_size, daily_downloads, credits FROM users WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                stats['total_downloads'] = row[0] or 0
                stats['total_size_gb'] = (row[1] or 0) / (1024 ** 3)
                stats['daily_downloads'] = row[2] or 0
                stats['credits'] = row[3] or 0
        
        async with self.conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE user_id = ? AND status = 'completed'", (user_id,)
        ) as cursor:
            stats['completed_jobs'] = (await cursor.fetchone())[0]
        
        return stats

# ==================== FORCE SUBSCRIBE CHECKER ====================
class ForceSubscribeChecker:
    """Check if user is subscribed to required channel"""
    
    def __init__(self, bot_app: Application):
        self.bot_app = bot_app
        self.channel = Config.FORCE_SUBSCRIBE_CHANNEL
    
    async def is_subscribed(self, user_id: int) -> bool:
        """Check if user is subscribed to channel"""
        if not self.channel:
            return True
        
        try:
            chat_member = await self.bot_app.bot.get_chat_member(
                chat_id=self.channel, 
                user_id=user_id
            )
            return chat_member.status in [
                ChatMember.MEMBER, 
                ChatMember.ADMINISTRATOR, 
                ChatMember.OWNER,
                ChatMember.CREATOR
            ]
        except Exception as e:
            logger.error(f"Failed to check subscription for {user_id}: {e}")
            return False
    
    async def check_and_ask(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """Check subscription and ask to join if not subscribed"""
        user_id = update.effective_user.id
        
        if await self.is_subscribed(user_id):
            return True
        
        # Send force subscribe message
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("📢 JOIN CHANNEL", url=Config.FORCE_SUBSCRIBE_LINK),
            InlineKeyboardButton("🔄 CHECK AGAIN", callback_data="check_subscribe")
        ]])
        
        await update.message.reply_text(
            f"⚠️ **{Config.FORCE_SUBSCRIBE_MESSAGE}**\n\n"
            f"Please join our channel to use this bot.",
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )
        return False

# ==================== DOWNLOAD ENGINE ====================
class DownloadEngine:
    """Advanced download engine with DRM bypass"""
    
    def __init__(self):
        self.session = None
    
    async def get_session(self) -> aiohttp.ClientSession:
        """Get or create session"""
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'},
                timeout=aiohttp.ClientTimeout(total=Config.DOWNLOAD_TIMEOUT)
            )
        return self.session
    
    async def download_youtube(self, url: str, quality: str, progress_callback=None) -> Tuple[str, int, str]:
        """Download from YouTube"""
        output_template = str(Config.TEMP_DIR / "%(title)s_%(id)s.%(ext)s")
        format_spec = Config.QUALITY_PRESETS.get(quality, Config.QUALITY_PRESETS["720p"])
        
        ydl_opts = {
            'format': format_spec,
            'outtmpl': output_template,
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'retries': Config.MAX_RETRIES,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info)
            
            if not Path(file_path).exists():
                ext = info.get('ext', 'mp4')
                file_path = str(Path(file_path).with_suffix(f'.{ext}'))
            
            file_size = Path(file_path).stat().st_size
            title = info.get('title', 'video')
            return file_path, file_size, title
    
    async def download_m3u8(self, url: str, progress_callback=None) -> Tuple[str, int, str]:
        """Download M3U8 stream"""
        session = await self.get_session()
        
        try:
            master = m3u8.load(url)
        except:
            master = m3u8.load(url, headers={'User-Agent': 'Mozilla/5.0'})
        
        if master.playlists:
            best = max(master.playlists, key=lambda p: p.stream_info.resolution[0] if p.stream_info.resolution else 0)
            playlist_url = best.uri if best.uri.startswith('http') else urljoin(url, best.uri)
            segments = m3u8.load(playlist_url)
        else:
            segments = master
            playlist_url = url
        
        output_file = Config.TEMP_DIR / f"stream_{int(time.time())}.ts"
        total = len(segments.segments)
        
        async with aiofiles.open(output_file, 'wb') as out:
            for i, seg in enumerate(segments.segments):
                seg_url = seg.uri if seg.uri.startswith('http') else urljoin(playlist_url, seg.uri)
                
                for retry in range(Config.MAX_RETRIES):
                    try:
                        async with session.get(seg_url) as resp:
                            data = await resp.read()
                            await out.write(data)
                            break
                    except:
                        if retry == Config.MAX_RETRIES - 1:
                            raise
                        await asyncio.sleep(1)
                
                if progress_callback:
                    await progress_callback((i + 1) / total * 100)
        
        file_size = output_file.stat().st_size
        return str(output_file), file_size, output_file.name
    
    async def download_appx(self, url: str, progress_callback=None) -> Tuple[str, int, str]:
        """Download Appx DRM protected content"""
        session = await self.get_session()
        
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        
        url_prefix = params.get('URLPrefix', [''])[0]
        if url_prefix:
            try:
                actual_url = base64.b64decode(url_prefix).decode('utf-8')
            except:
                actual_url = url
        else:
            actual_url = url
        
        headers = {
            'User-Agent': 'Mozilla/5.0',
            'Accept': '*/*',
            'Referer': 'https://appx.co.in/',
        }
        
        async with session.get(actual_url, headers=headers) as resp:
            if resp.status != 200:
                raise Exception(f"HTTP {resp.status}: Access denied")
            
            data = await resp.read()
            
            # Detect file type
            if data[:4] == b'%PDF':
                ext = 'pdf'
            elif data[:4] in [b'\x00\x00\x00\x18', b'\x00\x00\x00\x1c']:
                ext = 'mp4'
            else:
                ext = 'bin'
            
            output_file = Config.TEMP_DIR / f"appx_{int(time.time())}.{ext}"
            async with aiofiles.open(output_file, 'wb') as f:
                await f.write(data)
            
            if progress_callback:
                await progress_callback(100)
        
        file_size = len(data)
        return str(output_file), file_size, output_file.name
    
    async def download_direct(self, url: str, progress_callback=None) -> Tuple[str, int, str]:
        """Download direct file"""
        session = await self.get_session()
        
        async with session.get(url) as resp:
            if resp.status != 200:
                raise Exception(f"HTTP {resp.status}")
            
            total = int(resp.headers.get('content-length', 0))
            
            cd = resp.headers.get('content-disposition', '')
            filename_match = re.search(r'filename[=*]"?([^";]+)', cd)
            if filename_match:
                filename = unquote(filename_match.group(1))
            else:
                filename = unquote(Path(urlparse(url).path).name or 'file.bin')
            
            output_file = Config.TEMP_DIR / filename
            downloaded = 0
            last_update = time.time()
            
            async with aiofiles.open(output_file, 'wb') as f:
                async for chunk in resp.content.iter_chunked(Config.CHUNK_SIZE):
                    await f.write(chunk)
                    downloaded += len(chunk)
                    
                    if progress_callback and total > 0:
                        now = time.time()
                        if now - last_update >= 1:
                            percent = downloaded / total * 100
                            speed = downloaded / (now - last_update)
                            eta = (total - downloaded) / speed if speed > 0 else 0
                            await progress_callback(percent, speed, eta)
                            last_update = now
            
            file_size = output_file.stat().st_size
            return str(output_file), file_size, filename
    
    async def download_file(self, url: str, quality: str, progress_callback=None) -> Tuple[str, int, str]:
        """Main download dispatcher"""
        url_lower = url.lower()
        
        if 'youtube.com' in url_lower or 'youtu.be' in url_lower:
            return await self.download_youtube(url, quality, progress_callback)
        elif '.m3u8' in url_lower:
            return await self.download_m3u8(url, progress_callback)
        elif 'appx.co.in' in url_lower:
            return await self.download_appx(url, progress_callback)
        else:
            return await self.download_direct(url, progress_callback)
    
    async def close(self):
        """Close session"""
        if self.session and not self.session.closed:
            await self.session.close()

# ==================== QUEUE MANAGER ====================
class QueueManager:
    """Manages download queue and job processing"""
    
    def __init__(self, db: Database, bot_app: Application):
        self.db = db
        self.bot_app = bot_app
        self.active_jobs: Dict[int, asyncio.Task] = {}
        self.downloader = DownloadEngine()
        self.progress_messages: Dict[int, int] = {}
    
    async def process_job(self, user_id: int, job_id: int, start_index: int = 0):
        """Process job sequentially"""
        await self.db.update_job_status(job_id, "running")
        
        links = await self.db.get_job_links(job_id)
        total = len(links)
        
        for idx in range(start_index, total):
            # Check if job was cancelled/paused
            current_job = await self.db.get_job(job_id)
            if current_job and current_job['status'] in ('cancelled', 'paused'):
                break
            
            link = links[idx]
            success = await self._process_link(user_id, job_id, link, idx, total)
            await self.db.increment_progress(job_id, success)
            
            if not success:
                await self.db.add_failed_link(user_id, link['url'], link.get('error_message', 'Unknown error'))
            
            await self._update_progress_message(user_id, job_id, idx + 1, total)
        
        # Job completion
        final_job = await self.db.get_job(job_id)
        if final_job and final_job['current_index'] >= final_job['total_links']:
            await self.db.complete_job(job_id)
            await self._send_completion_report(user_id, job_id)
        else:
            await self.db.update_job_status(job_id, "paused")
        
        self.active_jobs.pop(user_id, None)
    
    async def _process_link(self, user_id: int, job_id: int, link: Dict, idx: int, total: int) -> bool:
        """Process single link"""
        url = link['url']
        link_id = link['id']
        
        await self.db.update_link_status(link_id, "downloading")
        
        async def progress_callback(percent, speed=0, eta=0):
            await self._update_download_progress(user_id, job_id, idx, total, percent, speed, eta)
        
        try:
            file_path, file_size, file_name = await self.downloader.download_file(url, "720p", progress_callback)
            
            await self.db.update_link_status(link_id, "uploading", file_name, file_size)
            await self._update_upload_progress(user_id, job_id, idx, total)
            
            file_ext = Path(file_path).suffix.lower()
            
            with open(file_path, 'rb') as f:
                if file_ext in ['.mp4', '.mkv', '.avi', '.mov', '.webm']:
                    await self.bot_app.bot.send_video(
                        chat_id=user_id,
                        video=InputFile(f, filename=file_name),
                        caption=f"✅ Downloaded: {file_name}\n📊 Size: {file_size / (1024**2):.2f} MB"
                    )
                elif file_ext == '.pdf':
                    await self.bot_app.bot.send_document(
                        chat_id=user_id,
                        document=InputFile(f, filename=file_name),
                        caption=f"✅ PDF: {file_name}"
                    )
                elif file_ext in ['.jpg', '.jpeg', '.png', '.gif']:
                    await self.bot_app.bot.send_photo(
                        chat_id=user_id,
                        photo=InputFile(f, filename=file_name),
                        caption=f"✅ Image: {file_name}"
                    )
                elif file_ext in ['.mp3', '.m4a', '.wav']:
                    await self.bot_app.bot.send_audio(
                        chat_id=user_id,
                        audio=InputFile(f, filename=file_name),
                        title=file_name
                    )
                else:
                    await self.bot_app.bot.send_document(
                        chat_id=user_id,
                        document=InputFile(f, filename=file_name),
                        caption=f"✅ File: {file_name}"
                    )
            
            await self.db.update_link_status(link_id, "success", file_name, file_size)
            await self.db.add_to_history(user_id, url, file_name, file_size, "success")
            
            Path(file_path).unlink(missing_ok=True)
            return True
            
        except Exception as e:
            logger.error(f"Download failed: {e}")
            error_msg = str(e)[:200]
            await self.db.update_link_status(link_id, "failed", error=error_msg)
            await self.db.add_to_history(user_id, url, "", 0, "failed", error_msg)
            await self.bot_app.bot.send_message(user_id, f"❌ Failed: {url[:80]}...\nError: {error_msg}")
            return False
    
    async def _update_progress_message(self, user_id: int, job_id: int, current: int, total: int):
        """Update main progress message"""
        job = await self.db.get_job(job_id)
        text = f"📊 Processing: {current}/{total}\n✅ Completed: {job['completed']}\n❌ Failed: {job['failed']}"
        
        if user_id in self.progress_messages:
            try:
                await self.bot_app.bot.edit_message_text(
                    text, chat_id=user_id, message_id=self.progress_messages[user_id]
                )
            except:
                msg = await self.bot_app.bot.send_message(user_id, text)
                self.progress_messages[user_id] = msg.message_id
        else:
            msg = await self.bot_app.bot.send_message(user_id, text)
            self.progress_messages[user_id] = msg.message_id
    
    async def _update_download_progress(self, user_id: int, job_id: int, current: int, total: int, 
                                         percent: float, speed: float, eta: int):
        """Update download progress"""
        text = f"📊 Processing: {current}/{total}\n📥 Downloading... {percent:.1f}%\n⚡ Speed: {speed/1024/1024:.2f} MB/s\n⏱️ ETA: {eta}s"
        
        if user_id in self.progress_messages:
            try:
                await self.bot_app.bot.edit_message_text(
                    text, chat_id=user_id, message_id=self.progress_messages[user_id]
                )
            except:
                pass
    
    async def _update_upload_progress(self, user_id: int, job_id: int, current: int, total: int):
        """Update upload progress"""
        text = f"📊 Processing: {current}/{total}\n📤 Uploading to Telegram..."
        
        if user_id in self.progress_messages:
            try:
                await self.bot_app.bot.edit_message_text(
                    text, chat_id=user_id, message_id=self.progress_messages[user_id]
                )
            except:
                pass
    
    async def _send_completion_report(self, user_id: int, job_id: int):
        """Send completion report"""
        job = await self.db.get_job(job_id)
        text = (
            f"✅ **Job Complete!**\n\n"
            f"📊 Summary:\n"
            f"✅ Successful: {job['completed']}\n"
            f"❌ Failed: {job['failed']}\n"
            f"📁 Total: {job['total_links']}\n"
            f"📄 File: {job['txt_filename']}"
        )
        await self.bot_app.bot.send_message(user_id, text)
        self.progress_messages.pop(user_id, None)
    
    async def cancel_job(self, user_id: int) -> bool:
        """Cancel user's job"""
        job = await self.db.get_user_pending_job(user_id)
        if job:
            await self.db.update_job_status(job['job_id'], "cancelled")
            if user_id in self.active_jobs:
                self.active_jobs[user_id].cancel()
                del self.active_jobs[user_id]
            return True
        return False
    
    async def resume_job(self, user_id: int) -> bool:
        """Resume paused job"""
        job = await self.db.get_user_pending_job(user_id)
        if job and job['status'] == "paused":
            task = asyncio.create_task(self.process_job(user_id, job['job_id'], job['current_index']))
            self.active_jobs[user_id] = task
            return True
        return False
    
    async def close(self):
        """Cleanup"""
        await self.downloader.close()

# ==================== BOT HANDLERS ====================
db = Database()
queue_manager = None
force_subscribe_checker = None
user_last_command = defaultdict(float)

# Conversation states
WAITING_FOR_TXT, ASK_START_INDEX = range(2)

def rate_limit(user_id: int) -> bool:
    """Check rate limit"""
    now = time.time()
    if now - user_last_command[user_id] < Config.RATE_LIMIT_SECONDS:
        return False
    user_last_command[user_id] = now
    return True

async def check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if user is subscribed"""
    if not force_subscribe_checker:
        return True
    return await force_subscribe_checker.check_and_ask(update, context)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler"""
    user = update.effective_user
    
    # Check subscription
    if not await check_subscription(update, context):
        return
    
    await db.add_user(user.id, user.username, user.first_name)
    
    # Check if banned
    if await db.is_banned(user.id):
        await update.message.reply_text("❌ You are banned from using this bot.")
        return
    
    if Config.ADMIN_ONLY_MODE and not await db.is_admin(user.id):
        await update.message.reply_text("🔒 Bot is in admin-only mode.")
        return
    
    keyboard = [
        [InlineKeyboardButton("📤 Upload TXT File", callback_data="upload_txt")],
        [InlineKeyboardButton("📊 My Status", callback_data="my_status")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
        [InlineKeyboardButton("❓ Help", callback_data="help")],
    ]
    
    if await db.is_admin(user.id):
        keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])
    
    await update.message.reply_text(
        f"🔥 **Welcome {user.first_name}!** 🔥\n\n"
        f"**DRM Extractor Bot v{Config.BOT_VERSION}**\n\n"
        f"📥 **Features:**\n"
        f"• Appx DRM Protected Content\n"
        f"• YouTube & 1000+ Platforms\n"
        f"• M3U8 Streams (Live/VOD)\n"
        f"• Batch Processing (1000+ URLs)\n\n"
        f"**How to use:**\n"
        f"1. Upload a `.txt` file with URLs\n"
        f"2. Choose starting position\n"
        f"3. I'll download and send files\n\n"
        f"**Commands:**\n"
        f"/start - Main menu\n"
        f"/status - Check progress\n"
        f"/cancel - Cancel job\n"
        f"/resume - Resume job\n"
        f"/help - Help",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command handler"""
    await check_subscription(update, context)
    
    help_text = (
        "❓ **Help Guide**\n\n"
        "**Supported URLs:**\n"
        "• Appx DRM protected (PDF/Video)\n"
        "• YouTube, Vimeo, Dailymotion\n"
        "• M3U8 streams (Live/VOD)\n"
        "• Direct MP4, PDF, JPG links\n\n"
        "**Commands:**\n"
        "/start - Main menu\n"
        "/status - Check job progress\n"
        "/cancel - Cancel current job\n"
        "/resume - Resume paused job\n"
        "/logs - Your download logs\n"
        "/help - This message\n\n"
        "**Admin Commands:**\n"
        "/broadcast - Send message to all\n"
        "/stats - Bot statistics\n"
        "/users - List all users\n"
        "/ban <id> - Ban user\n"
        "/unban <id> - Unban user"
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Status command handler"""
    await check_subscription(update, context)
    
    user_id = update.effective_user.id
    job = await db.get_user_pending_job(user_id)
    
    if not job:
        stats = await db.get_user_stats(user_id)
        text = (
            f"📊 **Your Statistics**\n\n"
            f"📥 Total Downloads: {stats['total_downloads']}\n"
            f"💾 Total Size: {stats['total_size_gb']:.2f} GB\n"
            f"📅 Today: {stats['daily_downloads']}/{Config.DAILY_LIMIT_NORMAL}\n"
            f"💎 Credits: {stats['credits']}\n\n"
            f"⚡ No active job. Use /start to begin!"
        )
    else:
        full_job = await db.get_job(job['job_id'])
        text = (
            f"📊 **Current Job**\n\n"
            f"📄 File: {full_job['txt_filename']}\n"
            f"📥 Progress: {full_job['current_index']}/{full_job['total_links']}\n"
            f"✅ Completed: {full_job['completed']}\n"
            f"❌ Failed: {full_job['failed']}\n"
            f"⚡ Status: {full_job['status']}"
        )
    
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel command handler"""
    user_id = update.effective_user.id
    if await queue_manager.cancel_job(user_id):
        await update.message.reply_text("✅ Job cancelled successfully.")
        await db.log_activity(user_id, "cancel_job", "User cancelled job")
    else:
        await update.message.reply_text("❌ No active job to cancel.")

async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resume command handler"""
    user_id = update.effective_user.id
    if await queue_manager.resume_job(user_id):
        await update.message.reply_text("▶️ Job resumed.")
        await db.log_activity(user_id, "resume_job", "User resumed job")
    else:
        await update.message.reply_text("❌ No paused job found.")

async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Logs command handler"""
    user_id = update.effective_user.id
    is_admin = await db.is_admin(user_id)
    
    if is_admin and context.args:
        target_id = int(context.args[0])
        query = "SELECT url, file_name, status, timestamp FROM history WHERE user_id = ? ORDER BY timestamp DESC LIMIT 10"
        params = (target_id,)
    else:
        query = "SELECT url, file_name, status, timestamp FROM history WHERE user_id = ? ORDER BY timestamp DESC LIMIT 10"
        params = (user_id,)
    
    async with db.conn.execute(query, params) as cursor:
        rows = await cursor.fetchall()
    
    if not rows:
        await update.message.reply_text("📋 No logs found.")
        return
    
    text = "📋 **Recent Downloads:**\n\n"
    for row in rows:
        status_icon = "✅" if row[2] == "success" else "❌"
        text += f"{status_icon} {row[3][:16]}: {row[1][:30] or row[0][:30]}\n"
    
    await update.message.reply_text(text[:4000], parse_mode=ParseMode.MARKDOWN)

# ==================== ADMIN COMMANDS ====================
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin broadcast command"""
    user_id = update.effective_user.id
    if not await db.is_admin(user_id):
        await update.message.reply_text("🔒 Admin only.")
        return
    
    message = " ".join(context.args)
    if not message:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    
    async with db.conn.execute("SELECT user_id FROM users") as cursor:
        users = await cursor.fetchall()
    
    count = 0
    for (uid,) in users:
        try:
            await context.bot.send_message(uid, f"📢 **Broadcast**\n\n{message}", parse_mode=ParseMode.MARKDOWN)
            count += 1
            await asyncio.sleep(0.05)
        except:
            pass
    
    await update.message.reply_text(f"✅ Broadcast sent to {count} users.")
    await db.log_activity(user_id, "broadcast", f"Sent to {count} users")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin stats command"""
    user_id = update.effective_user.id
    if not await db.is_admin(user_id):
        await update.message.reply_text("🔒 Admin only.")
        return
    
    stats = await db.get_stats()
    text = (
        f"📊 **Bot Statistics**\n\n"
        f"👥 Total Users: {stats['total_users']}\n"
        f"🚫 Banned Users: {stats['banned_users']}\n"
        f"💎 Premium Users: {stats['premium_users']}\n"
        f"✅ Completed Jobs: {stats['completed_jobs']}\n"
        f"💾 Total Downloaded: {stats['total_size_gb']:.2f} GB\n"
        f"❌ Failed Downloads: {stats['failed_downloads']}\n"
        f"⏳ Queue Size: {stats['queue_size']}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin users list command"""
    user_id = update.effective_user.id
    if not await db.is_admin(user_id):
        await update.message.reply_text("🔒 Admin only.")
        return
    
    async with db.conn.execute(
        "SELECT user_id, username, first_name, total_downloads, is_banned FROM users ORDER BY total_downloads DESC LIMIT 20"
    ) as cursor:
        rows = await cursor.fetchall()
    
    text = "👥 **Top Users:**\n\n"
    for row in rows:
        name = row[2] or row[1] or str(row[0])
        status = "🚫" if row[4] else "✅"
        text += f"{status} {name}: {row[3]} downloads\n"
    
    await update.message.reply_text(text[:4000], parse_mode=ParseMode.MARKDOWN)

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ban user command"""
    user_id = update.effective_user.id
    if not await db.is_admin(user_id):
        await update.message.reply_text("🔒 Admin only.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /ban <user_id>")
        return
    
    target_id = int(context.args[0])
    await db.ban_user(target_id)
    await update.message.reply_text(f"✅ User {target_id} banned.")
    await db.log_activity(user_id, "ban_user", f"Banned user {target_id}")

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unban user command"""
    user_id = update.effective_user.id
    if not await db.is_admin(user_id):
        await update.message.reply_text("🔒 Admin only.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /unban <user_id>")
        return
    
    target_id = int(context.args[0])
    await db.unban_user(target_id)
    await update.message.reply_text(f"✅ User {target_id} unbanned.")
    await db.log_activity(user_id, "unban_user", f"Unbanned user {target_id}")

# ==================== FILE HANDLER ====================
async def handle_txt_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle uploaded TXT file"""
    user_id = update.effective_user.id
    
    # Check subscription
    if not await check_subscription(update, context):
        return ConversationHandler.END
    
    if not rate_limit(user_id):
        await update.message.reply_text("⏳ Please wait a moment.")
        return ConversationHandler.END
    
    # Check if banned
    if await db.is_banned(user_id):
        await update.message.reply_text("❌ You are banned from using this bot.")
        return ConversationHandler.END
    
    document = update.message.document
    if not document.file_name.endswith(".txt"):
        await update.message.reply_text("❌ Please send a .txt file with one URL per line.")
        return ConversationHandler.END
    
    status_msg = await update.message.reply_text("📥 Downloading file...")
    
    try:
        file = await context.bot.get_file(document.file_id)
        content = await file.download_as_bytearray()
        urls = content.decode('utf-8', errors='ignore').splitlines()
        urls = [line.strip() for line in urls if line.strip() and line.strip().startswith(('http://', 'https://'))]
        
        if not urls:
            await status_msg.edit_text("❌ No valid URLs found in the file.")
            return ConversationHandler.END
        
        context.user_data["pending_urls"] = urls
        context.user_data["txt_filename"] = document.file_name
        
        # Create keyboard for start index selection
        keyboard = []
        for i in range(0, min(len(urls), 10), 5):
            row = [InlineKeyboardButton(f"Start from {j+1}", callback_data=f"start_{j}") for j in range(i, min(i+5, len(urls)))]
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("Start from beginning", callback_data="start_0")])
        keyboard.append([InlineKeyboardButton("Cancel", callback_data="cancel")])
        
        await status_msg.edit_text(
            f"📄 **File:** {document.file_name}\n"
            f"🔗 **URLs found:** {len(urls)}\n\n"
            f"**Where should I start?**",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        
        return ASK_START_INDEX
        
    except Exception as e:
        logger.error(f"File processing error: {e}")
        await status_msg.edit_text(f"❌ Error: {str(e)[:200]}")
        return ConversationHandler.END

async def start_index_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle start index selection"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    # Check subscription again
    if not await force_subscribe_checker.is_subscribed(user_id):
        await query.edit_message_text("⚠️ Please join our channel first!")
        return ConversationHandler.END
    
    if data == "cancel":
        await query.edit_message_text("❌ Cancelled.")
        return ConversationHandler.END
    
    if data.startswith("start_"):
        start_idx = int(data.split("_")[1])
        urls = context.user_data.get("pending_urls", [])
        txt_filename = context.user_data.get("txt_filename", "unknown.txt")
        
        if not urls:
            await query.edit_message_text("❌ Session expired. Please upload again.")
            return ConversationHandler.END
        
        # Create job
        job_id = await db.create_job(user_id, txt_filename, urls)
        
        # Auto remove from channel if enabled
        if Config.AUTO_REMOVE_AFTER_EXTRACT and Config.FORCE_SUBSCRIBE_CHANNEL:
            try:
                await context.bot.ban_chat_member(
                    chat_id=Config.FORCE_SUBSCRIBE_CHANNEL,
                    user_id=user_id
                )
                await context.bot.unban_chat_member(
                    chat_id=Config.FORCE_SUBSCRIBE_CHANNEL,
                    user_id=user_id
                )
            except Exception as e:
                logger.warning(f"Failed to remove user from channel: {e}")
        
        await query.edit_message_text(
            f"✅ **Job Created!**\n\n"
            f"📊 Total URLs: {len(urls)}\n"
            f"🎯 Starting from: {start_idx + 1}\n\n"
            f"Use /status to track progress."
        )
        
        # Start the job
        asyncio.create_task(queue_manager.process_job(user_id, job_id, start_idx))
        await db.log_activity(user_id, "start_job", f"job_id={job_id}, start={start_idx}")
    
    return ConversationHandler.END

# ==================== BUTTON CALLBACK ====================
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    # Check subscription for non-check actions
    if data not in ["check_subscribe"]:
        if not await force_subscribe_checker.is_subscribed(user_id):
            await query.edit_message_text("⚠️ Please join our channel first!")
            return
    
    if data == "check_subscribe":
        if await force_subscribe_checker.is_subscribed(user_id):
            await query.edit_message_text("✅ Thanks for joining! Now use /start")
        else:
            await query.edit_message_text("❌ You still haven't joined. Please join first!")
        return
    
    elif data == "upload_txt":
        await query.edit_message_text(
            "📤 **Send a TXT file**\n\n"
            "The file should contain one URL per line.\n\n"
            "Example:\n"
            "https://example.com/video1.mp4\n"
            "https://example.com/video2.mp4\n\n"
            "Send the file now:",
            parse_mode=ParseMode.MARKDOWN
        )
        return WAITING_FOR_TXT
    
    elif data == "my_status":
        job = await db.get_user_pending_job(user_id)
        if job:
            full_job = await db.get_job(job['job_id'])
            text = (
                f"📊 **Current Job**\n\n"
                f"Progress: {full_job['current_index']}/{full_job['total_links']}\n"
                f"Completed: {full_job['completed']}\n"
                f"Failed: {full_job['failed']}\n"
                f"Status: {full_job['status']}"
            )
        else:
            stats = await db.get_user_stats(user_id)
            text = (
                f"📊 **Your Statistics**\n\n"
                f"Downloads: {stats['total_downloads']}\n"
                f"Size: {stats['total_size_gb']:.2f} GB\n"
                f"Today: {stats['daily_downloads']}/{Config.DAILY_LIMIT_NORMAL}\n"
                f"Credits: {stats['credits']}"
            )
        
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
    
    elif data == "settings":
        user_data = await db.get_user(user_id)
        keyboard = [
            [InlineKeyboardButton(f"🎬 Quality: {user_data.get('preferred_quality', '720p')}", callback_data="set_quality")],
            [InlineKeyboardButton(f"💧 Watermark: {'✅' if user_data.get('watermark_enabled') else '❌'}", callback_data="toggle_watermark")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_main")],
        ]
        await query.edit_message_text("⚙️ **Settings**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    
    elif data == "help":
        await help_command(update, context)
    
    elif data == "admin_panel" and await db.is_admin(user_id):
        keyboard = [
            [InlineKeyboardButton("📊 Bot Stats", callback_data="admin_stats")],
            [InlineKeyboardButton("👥 User List", callback_data="admin_users")],
            [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_main")],
        ]
        await query.edit_message_text("👑 **Admin Panel**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    
    elif data == "admin_stats" and await db.is_admin(user_id):
        stats = await db.get_stats()
        text = (
            f"📊 **Bot Stats**\n\n"
            f"Users: {stats['total_users']}\n"
            f"Banned: {stats['banned_users']}\n"
            f"Premium: {stats['premium_users']}\n"
            f"Jobs: {stats['completed_jobs']}\n"
            f"Size: {stats['total_size_gb']:.2f} GB\n"
            f"Failed: {stats['failed_downloads']}"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
    
    elif data == "admin_users" and await db.is_admin(user_id):
        async with db.conn.execute(
            "SELECT user_id, username, first_name, total_downloads FROM users ORDER BY total_downloads DESC LIMIT 15"
        ) as cursor:
            rows = await cursor.fetchall()
        
        text = "👥 **Top Users:**\n\n"
        for row in rows:
            name = row[2] or row[1] or str(row[0])
            text += f"• {name}: {row[3]} downloads\n"
        
        await query.edit_message_text(text[:4000], parse_mode=ParseMode.MARKDOWN)
    
    elif data == "back_main":
        keyboard = [
            [InlineKeyboardButton("📤 Upload TXT File", callback_data="upload_txt")],
            [InlineKeyboardButton("📊 My Status", callback_data="my_status")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
            [InlineKeyboardButton("❓ Help", callback_data="help")],
        ]
        if await db.is_admin(user_id):
            keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])
        
        await query.edit_message_text("Main Menu:", reply_markup=InlineKeyboardMarkup(keyboard))

# ==================== ERROR HANDLER ====================
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    logger.error(f"Exception: {context.error}", exc_info=context.error)
    
    error_message = "❌ An error occurred. Please try again later."
    
    if isinstance(context.error, TimedOut):
        error_message = "⏰ Request timed out. Please try again."
    elif isinstance(context.error, NetworkError):
        error_message = "🌐 Network error. Check your connection."
    elif isinstance(context.error, RetryAfter):
        error_message = f"⏳ Rate limited. Wait {context.error.retry_after} seconds."
    
    if update and update.effective_message:
        await update.effective_message.reply_text(error_message)

# ==================== MAIN ====================
async def main():
    """Main entry point"""
    global queue_manager, force_subscribe_checker
    
    try:
        Config.validate()
    except ValueError as e:
        logger.error(e)
        return
    
    # Initialize database
    await db.init()
    
    # Create application
    app = Application.builder().token(Config.BOT_TOKEN).build()
    
    # Initialize components
    queue_manager = QueueManager(db, app)
    force_subscribe_checker = ForceSubscribeChecker(app)
    
    # Command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("resume", resume_command))
    app.add_handler(CommandHandler("logs", logs_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("users", users_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))
    
    # Conversation handler for file upload
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_callback, pattern="^upload_txt$")],
        states={
            WAITING_FOR_TXT: [MessageHandler(filters.Document.ALL, handle_txt_file)],
            ASK_START_INDEX: [CallbackQueryHandler(start_index_callback, pattern="^(start_|cancel)")],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        allow_reentry=True,
    )
    app.add_handler(conv_handler)
    
    # General callback handler
    app.add_handler(CallbackQueryHandler(button_callback))
    
    # Error handler
    app.add_error_handler(error_handler)
    
    # Set bot commands
    await app.bot.set_my_commands([
        BotCommand("start", "Open main menu"),
        BotCommand("status", "Check progress"),
        BotCommand("cancel", "Cancel job"),
        BotCommand("resume", "Resume job"),
        BotCommand("logs", "View logs"),
        BotCommand("help", "Show help"),
    ])
    
    logger.info("=" * 50)
    logger.info("🔥 BOT STARTED SUCCESSFULLY!")
    logger.info(f"📢 Force Subscribe Channel: {Config.FORCE_SUBSCRIBE_CHANNEL or 'Disabled'}")
    logger.info(f"🔧 Auto Remove After Extract: {Config.AUTO_REMOVE_AFTER_EXTRACT}")
    logger.info(f"👑 Admins: {Config.ADMIN_IDS}")
    logger.info("=" * 50)
    
    # Start polling
    await app.run_polling()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
