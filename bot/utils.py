"""Logging setup and shared helpers."""

import logging
import logging.handlers
import os
from config.settings import LOG_DIR


def setup_logging() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)-26s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)

    for fname, level in [("bot.log", logging.INFO), ("errors.log", logging.ERROR)]:
        fh = logging.handlers.RotatingFileHandler(
            os.path.join(LOG_DIR, fname),
            maxBytes=10*1024*1024, backupCount=5, encoding="utf-8"
        )
        fh.setLevel(level)
        fh.setFormatter(fmt)
        root.addHandler(fh)

    for lib in ("httpx","aiohttp","telegram","httpcore","urllib3"):
        logging.getLogger(lib).setLevel(logging.WARNING)


def fmt_bytes(n: int) -> str:
    if n < 1024:       return f"{n} B"
    if n < 1 << 20:    return f"{n/1024:.1f} KB"
    if n < 1 << 30:    return f"{n/(1<<20):.1f} MB"
    return f"{n/(1<<30):.2f} GB"
