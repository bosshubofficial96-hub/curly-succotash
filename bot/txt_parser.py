"""
TXT file parser — detects URLs and their associated titles.

Supported formats (can be mixed in one file):
──────────────────────────────────────────────
Format 1 — Pipe separator (title || url):
  Lecture 01 - Introduction || https://cdn.appx.co.in/...
  Chapter 2 Theory          || https://stream.example.com/video.m3u8

Format 2 — Title on line BEFORE the URL:
  Lecture 01 - Introduction
  https://cdn.appx.co.in/...

  Chapter 2 Theory
  https://cdn.appx.co.in/...

Format 3 — URL-only (filename used as title):
  https://cdn.appx.co.in/...
  https://cdn.appx.co.in/...

Comments (# at start of line) are skipped.
Empty lines act as separators and reset the pending title.
All three formats can be mixed in one file.
"""

import re
from typing import List, Tuple

_URL_RE = re.compile(r"^https?://[^\s<>\"{}|\\^`\[\]]+$")


def _is_url(line: str) -> bool:
    return bool(_URL_RE.match(line.strip()))


def _is_comment(line: str) -> bool:
    return line.strip().startswith("#")


def parse_txt(raw: str) -> List[Tuple[str, str]]:
    """
    Returns list of (url, title) tuples.
    title may be empty string if none was found.
    """
    results:      List[Tuple[str, str]] = []
    pending_title: str                   = ""

    for line in raw.splitlines():
        line = line.rstrip()

        # Skip comments
        if _is_comment(line):
            continue

        # Empty line — reset pending title
        if not line.strip():
            pending_title = ""
            continue

        # Format 1: "Title || URL" or "URL || Title"
        if "||" in line:
            parts = line.split("||", 1)
            a, b  = parts[0].strip(), parts[1].strip()
            if _is_url(a):
                url, title = a, b
            elif _is_url(b):
                url, title = b, a
            else:
                # Neither side is a URL; treat whole line as a title
                pending_title = line.strip()
                continue
            results.append((url, title))
            pending_title = ""
            continue

        # Format 2 / 3: line is a raw URL or a title
        if _is_url(line.strip()):
            results.append((line.strip(), pending_title))
            pending_title = ""   # consumed
        else:
            # Non-URL, non-comment, non-empty → candidate title for next URL
            pending_title = line.strip()

    return results


def validate(entries: List[Tuple[str, str]]) -> Tuple[List[Tuple[str, str]], List[str]]:
    """
    Returns (valid_entries, skipped_reasons).
    Also validates SSRF protection.
    """
    from .drm import is_valid_url

    valid:   List[Tuple[str, str]] = []
    skipped: List[str]             = []

    for url, title in entries:
        if is_valid_url(url):
            valid.append((url, title))
        else:
            skipped.append(f"Blocked/invalid: {url[:60]}")

    return valid, skipped
