"""
config.py - Central configuration management for the Telegram bot.
Loads environment variables from .env file and provides a Config class.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from the current directory
load_dotenv()


class Config:
    """
    All configuration settings for the bot.
    Values are read from environment variables with fallback defaults.
    """

    # ==================== Telegram Bot Settings ====================
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is not set in environment variables or .env file")

    # Admin user IDs (comma-separated list of integers)
    ADMIN_IDS_RAW: str = os.getenv("ADMIN_IDS", "")
    ADMIN_IDS: list[int] = []
    if ADMIN_IDS_RAW:
        try:
            ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip()]
        except ValueError:
            raise ValueError("ADMIN_IDS must be comma-separated integers (e.g., '123456789,987654321')")
    if not ADMIN_IDS:
        raise ValueError("ADMIN_IDS is not set or empty")

    # ==================== Database Settings ====================
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./bot_data.db")

    # ==================== Download Settings ====================
    DOWNLOAD_TIMEOUT: int = int(os.getenv("DOWNLOAD_TIMEOUT", "300"))  # seconds
    MAX_FILE_SIZE_MB: int = int(os.getenv("MAX_FILE_SIZE_MB", "50"))   # Telegram bot limit is 50MB
    MAX_FILE_SIZE_BYTES: int = MAX_FILE_SIZE_MB * 1024 * 1024
    MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "3"))
    RETRY_BACKOFF_FACTOR: int = 2  # exponential backoff: 2, 4, 8 seconds
    CHUNK_SIZE: int = 1024 * 1024  # 1 MB chunks for streaming downloads

    # ==================== Rate Limiting ====================
    RATE_LIMIT_REQUESTS: int = int(os.getenv("RATE_LIMIT_REQUESTS", "5"))   # max requests
    RATE_LIMIT_PERIOD: int = int(os.getenv("RATE_LIMIT_PERIOD", "60"))      # per X seconds

    # ==================== File Paths ====================
    BASE_DIR: Path = Path(__file__).parent.resolve()
    USER_DATA_DIR: Path = Path(os.getenv("USER_DATA_DIR", "./temp_downloads"))
    LOG_DIR: Path = Path(os.getenv("LOG_DIR", "./logs"))

    # ==================== Processor Settings ====================
    PROCESSOR_SLEEP_INTERVAL: int = 1          # seconds between queue checks
    PROGRESS_UPDATE_INTERVAL: int = 2          # seconds between sending progress to user
    MAX_CONCURRENT_DOWNLOADS: int = 1          # sequential processing (as required)

    # ==================== Security Settings ====================
    ALLOWED_URL_SCHEMES: set = {"http", "https"}
    BLOCKED_EXTENSIONS: set = {".exe", ".bat", ".sh", ".msi", ".vbs", ".scr", ".ps1"}
    MAX_URL_LENGTH: int = 2048
    URL_VALIDATION_TIMEOUT: int = 5  # seconds for HEAD request

    # ==================== Logging Settings ====================
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
    LOG_RETENTION_DAYS: int = int(os.getenv("LOG_RETENTION_DAYS", "7"))
    LOG_FORMAT: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    LOG_DATE_FORMAT: str = "%Y-%m-%d %H:%M:%S"

    # ==================== Google Drive Settings ====================
    GOOGLE_DRIVE_ENABLED: bool = os.getenv("GOOGLE_DRIVE_ENABLED", "false").lower() == "true"
    GOOGLE_DRIVE_FOLDER_ID: str = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
    GOOGLE_DRIVE_CREDENTIALS_FILE: Path = Path(os.getenv("GOOGLE_DRIVE_CREDENTIALS_FILE", "./credentials.json"))
    GOOGLE_DRIVE_TOKEN_FILE: Path = Path(os.getenv("GOOGLE_DRIVE_TOKEN_FILE", "./token.json"))
    GOOGLE_DRIVE_SHARE_EMAIL: str = os.getenv("GOOGLE_DRIVE_SHARE_EMAIL", "")  # email to share uploaded files with

    # ==================== HTTP Headers for DRM Bypass ====================
    # Default headers for downloads
    DEFAULT_HEADERS: dict = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    }

    # Domain-specific headers for DRM/subscription bypass attempts
    # Extend this for other domains as needed
    DOMAIN_HEADERS: dict = {
        "static-db-v2.appx.co.in": {
            "Referer": "https://static-db-v2.appx.co.in/",
            "Origin": "https://static-db-v2.appx.co.in",
        },
        # Add more domains here (e.g., for other DRM-protected sites)
    }

    @classmethod
    def get_headers_for_url(cls, url: str) -> dict:
        """Return merged headers for a given URL, including domain-specific overrides."""
        from urllib.parse import urlparse
        headers = cls.DEFAULT_HEADERS.copy()
        domain = urlparse(url).netloc
        if domain in cls.DOMAIN_HEADERS:
            headers.update(cls.DOMAIN_HEADERS[domain])
        return headers

    @classmethod
    def ensure_directories(cls) -> None:
        """Create required directories if they don't exist."""
        cls.USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        cls.LOG_DIR.mkdir(parents=True, exist_ok=True)
        # For Google Drive, ensure credential files path exists (but not create files)
        if cls.GOOGLE_DRIVE_ENABLED:
            cls.GOOGLE_DRIVE_CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def validate(cls) -> bool:
        """Validate critical configuration."""
        if not cls.BOT_TOKEN:
            return False
        if not cls.ADMIN_IDS:
            return False
        if cls.MAX_FILE_SIZE_MB > 50:
            raise ValueError("MAX_FILE_SIZE_MB cannot exceed 50 due to Telegram bot limits")
        if cls.DOWNLOAD_TIMEOUT < 1:
            raise ValueError("DOWNLOAD_TIMEOUT must be positive")
        if cls.GOOGLE_DRIVE_ENABLED and not cls.GOOGLE_DRIVE_CREDENTIALS_FILE.exists():
            print(f"Warning: Google Drive credentials file not found at {cls.GOOGLE_DRIVE_CREDENTIALS_FILE}")
            # Don't raise, just warn; authentication can still be attempted later
        return True


# Auto-create directories when config is imported (if not main)
if __name__ != "__main__":
    Config.ensure_directories()
