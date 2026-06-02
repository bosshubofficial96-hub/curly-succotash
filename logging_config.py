"""
logging_config.py - Logging setup with file rotation, different log levels,
and separate log files for errors, downloads, uploads, and user activity.
"""

import logging
import logging.handlers
from pathlib import Path
from datetime import datetime, timedelta

from config import Config


def setup_logging() -> logging.Logger:
    """
    Configure and return the root logger.
    Creates separate log files for:
    - bot.log (all logs)
    - error.log (errors only)
    - download.log (download activity)
    - upload.log (upload activity)
    - user_activity.log (user commands and interactions)
    """
    # Ensure log directory exists
    log_dir = Config.LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)

    # Log format
    log_format = logging.Formatter(
        Config.LOG_FORMAT,
        datefmt=Config.LOG_DATE_FORMAT
    )

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, Config.LOG_LEVEL))

    # Remove existing handlers to avoid duplicates on re-run
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Console handler (for stdout)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_format)
    console_handler.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)

    # File handler for all logs (rotating)
    all_log_file = log_dir / "bot.log"
    all_handler = logging.handlers.RotatingFileHandler(
        all_log_file, maxBytes=10*1024*1024, backupCount=5  # 10 MB per file, 5 backups
    )
    all_handler.setFormatter(log_format)
    root_logger.addHandler(all_handler)

    # Error-only log file
    error_log_file = log_dir / "error.log"
    error_handler = logging.handlers.RotatingFileHandler(
        error_log_file, maxBytes=5*1024*1024, backupCount=3
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(log_format)
    root_logger.addHandler(error_handler)

    # Download activity logger
    download_logger = logging.getLogger("download")
    download_logger.setLevel(logging.INFO)
    download_log_file = log_dir / "download.log"
    download_handler = logging.handlers.RotatingFileHandler(
        download_log_file, maxBytes=10*1024*1024, backupCount=3
    )
    download_handler.setFormatter(log_format)
    download_logger.addHandler(download_handler)
    download_logger.propagate = False  # Don't send to root

    # Upload activity logger
    upload_logger = logging.getLogger("upload")
    upload_logger.setLevel(logging.INFO)
    upload_log_file = log_dir / "upload.log"
    upload_handler = logging.handlers.RotatingFileHandler(
        upload_log_file, maxBytes=10*1024*1024, backupCount=3
    )
    upload_handler.setFormatter(log_format)
    upload_logger.addHandler(upload_handler)
    upload_logger.propagate = False

    # User activity logger
    user_logger = logging.getLogger("user_activity")
    user_logger.setLevel(logging.INFO)
    user_log_file = log_dir / "user_activity.log"
    user_handler = logging.handlers.RotatingFileHandler(
        user_log_file, maxBytes=10*1024*1024, backupCount=3
    )
    user_handler.setFormatter(log_format)
    user_logger.addHandler(user_handler)
    user_logger.propagate = False

    # Return root logger for main use
    root_logger.info("Logging system initialized")
    return root_logger


def get_download_logger() -> logging.Logger:
    """Get logger for download events."""
    return logging.getLogger("download")


def get_upload_logger() -> logging.Logger:
    """Get logger for upload events."""
    return logging.getLogger("upload")


def get_user_activity_logger() -> logging.Logger:
    """Get logger for user activity."""
    return logging.getLogger("user_activity")


def cleanup_old_logs() -> None:
    """Delete log files older than LOG_RETENTION_DAYS."""
    if Config.LOG_RETENTION_DAYS <= 0:
        return
    cutoff = datetime.now() - timedelta(days=Config.LOG_RETENTION_DAYS)
    for log_file in Config.LOG_DIR.glob("*.log"):
        try:
            if datetime.fromtimestamp(log_file.stat().st_mtime) < cutoff:
                log_file.unlink()
        except Exception:
            pass
