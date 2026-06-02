"""
utils.py - Helper functions for URL validation, file size formatting,
time estimation, directory management, and common utilities.
"""

import re
import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from config import Config


def validate_url(url: str) -> bool:
    """
    Validate URL format, scheme, and length.
    Returns True if valid.
    """
    if not url or len(url) > Config.MAX_URL_LENGTH:
        return False
    
    try:
        parsed = urlparse(url)
        if parsed.scheme not in Config.ALLOWED_URL_SCHEMES:
            return False
        if not parsed.netloc:
            return False
        # Check for blocked extensions (optional)
        path_lower = parsed.path.lower()
        for ext in Config.BLOCKED_EXTENSIONS:
            if path_lower.endswith(ext):
                return False
        return True
    except Exception:
        return False


def format_file_size(size_bytes: int) -> str:
    """Convert bytes to human-readable format."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def estimate_remaining_time(current: int, total: int, elapsed_seconds: float, avg_time_per_item: float = None) -> Optional[int]:
    """
    Estimate remaining time in seconds based on progress and average time per item.
    """
    if current == 0 or total == 0 or current >= total:
        return None
    
    if avg_time_per_item is not None:
        remaining_items = total - current
        return int(avg_time_per_item * remaining_items)
    
    if elapsed_seconds > 0:
        avg = elapsed_seconds / current
        remaining_items = total - current
        return int(avg * remaining_items)
    
    return None


def sanitize_filename(filename: str) -> str:
    """Remove unsafe characters from filename."""
    return re.sub(r'[<>:"/\\|?*]', '_', filename)


def ensure_directories() -> None:
    """Create all required directories if they don't exist."""
    Config.ensure_directories()
    # Additional directories if needed
    (Config.BASE_DIR / "logs").mkdir(parents=True, exist_ok=True)


def is_url_accessible(url: str, timeout: int = Config.URL_VALIDATION_TIMEOUT) -> bool:
    """
    Quick HEAD request to check if URL is accessible.
    Note: This is sync for simplicity; for async use aiohttp directly.
    """
    import requests
    try:
        resp = requests.head(url, timeout=timeout, allow_redirects=True)
        return resp.status_code < 400
    except Exception:
        return False


def extract_domain(url: str) -> str:
    """Extract domain from URL (e.g., 'example.com')."""
    parsed = urlparse(url)
    return parsed.netloc


def get_file_extension(filename: str) -> str:
    """Return lowercase file extension including dot."""
    ext = Path(filename).suffix.lower()
    return ext


def is_within_size_limit(file_size_bytes: int) -> bool:
    """Check if file size is within Telegram's limit."""
    return file_size_bytes <= Config.MAX_FILE_SIZE_BYTES


def cleanup_old_temp_files(max_age_hours: int = 24) -> int:
    """Delete temporary files older than max_age_hours. Returns count deleted."""
    deleted = 0
    now = time.time()
    for file_path in Config.USER_DATA_DIR.glob("*"):
        if file_path.is_file():
            file_age = now - file_path.stat().st_mtime
            if file_age > max_age_hours * 3600:
                file_path.unlink()
                deleted += 1
    return deleted
