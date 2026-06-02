"""
config.py - Configuration management with environment variables.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Telegram Bot Token (required)
    BOT_TOKEN = os.getenv("BOT_TOKEN", "8614102555:AAHy0mMiBDF0CYcHtDGpfNQW4nIIe1J5-Uc")
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is missing. Set it in .env or as environment variable.")

    # Admin IDs (comma-separated, required)
    ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "5968883359")
    ADMIN_IDS = []
    if ADMIN_IDS_RAW:
        try:
            ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip()]
        except ValueError:
            raise ValueError("ADMIN_IDS must contain only numbers separated by commas.")
    if not ADMIN_IDS:
        raise ValueError("ADMIN_IDS is missing or empty. Set at least one admin user ID.")

    # Database
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./bot_data.db")

    # Download settings
    DOWNLOAD_TIMEOUT = int(os.getenv("DOWNLOAD_TIMEOUT", "300"))
    MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "50"))
    MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
    MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
    RETRY_BACKOFF_FACTOR = 2
    CHUNK_SIZE = 1024 * 1024  # 1 MB

    # Rate limiting
    RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "5"))
    RATE_LIMIT_PERIOD = int(os.getenv("RATE_LIMIT_PERIOD", "60"))

    # Directories
    BASE_DIR = Path(__file__).parent.resolve()
    USER_DATA_DIR = Path(os.getenv("USER_DATA_DIR", "./temp_downloads"))
    LOG_DIR = Path(os.getenv("LOG_DIR", "./logs"))

    # Processor
    PROCESSOR_SLEEP_INTERVAL = 1
    PROGRESS_UPDATE_INTERVAL = 2

    # Security
    ALLOWED_URL_SCHEMES = {"http", "https"}
    BLOCKED_EXTENSIONS = {".exe", ".bat", ".sh", ".msi", ".vbs", ".scr", ".ps1"}
    MAX_URL_LENGTH = 2048
    URL_VALIDATION_TIMEOUT = 5

    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
    LOG_RETENTION_DAYS = int(os.getenv("LOG_RETENTION_DAYS", "7"))
    LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

    # Google Drive (optional)
    GOOGLE_DRIVE_ENABLED = os.getenv("GOOGLE_DRIVE_ENABLED", "false").lower() == "true"
    GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
    GOOGLE_DRIVE_CREDENTIALS_FILE = Path(os.getenv("GOOGLE_DRIVE_CREDENTIALS_FILE", "./credentials.json"))
    GOOGLE_DRIVE_TOKEN_FILE = Path(os.getenv("GOOGLE_DRIVE_TOKEN_FILE", "./token.json"))
    GOOGLE_DRIVE_SHARE_EMAIL = os.getenv("GOOGLE_DRIVE_SHARE_EMAIL", "")

    # HTTP Headers
    DEFAULT_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    }

    DOMAIN_HEADERS = {
        "static-db-v2.appx.co.in": {
            "Referer": "https://static-db-v2.appx.co.in/",
            "Origin": "https://static-db-v2.appx.co.in",
        },
    }

    @classmethod
    def get_headers_for_url(cls, url: str) -> dict:
        from urllib.parse import urlparse
        headers = cls.DEFAULT_HEADERS.copy()
        domain = urlparse(url).netloc
        if domain in cls.DOMAIN_HEADERS:
            headers.update(cls.DOMAIN_HEADERS[domain])
        return headers

    @classmethod
    def ensure_directories(cls):
        cls.USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        cls.LOG_DIR.mkdir(parents=True, exist_ok=True)

    @classmethod
    def validate(cls) -> bool:
        if not cls.BOT_TOKEN:
            return False
        if not cls.ADMIN_IDS:
            return False
        if cls.MAX_FILE_SIZE_MB > 50:
            raise ValueError("MAX_FILE_SIZE_MB cannot exceed 50")
        return True


# Auto-create directories on import
Config.ensure_directories()
