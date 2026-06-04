"""
TXT file parser — detects URLs and their associated titles.

Supported formats (all can be mixed in one file):
─────────────────────────────────────────────────
Format 1 — Colon separator (PRIMARY, new format):
  Lecture 01 - Introduction:https://cdn.appx.co.in/paid_course4/file.pdf
  Chapter 2 Theory:https://stream.example.com/video.m3u8

Format 2 — Pipe separator (legacy, still supported):
  Lecture 01 - Introduction||https://cdn.appx.co.in/...

Format 3 — Title on line BEFORE the URL:
  Lecture 01 - Introduction
  https://cdn.appx.co.in/...

Format 4 — URL-only (filename used as title):
  https://cdn.appx.co.in/...

Comments: lines starting with # are ignored.
Empty lines reset the pending title buffer.
"""

import re
from typing import List, Tuple

_URL_RE = re.compile(r"https?://[^\s<>\"{}\\^`\[\]]+")


def _find_url(line: str):
    """Return (url, start_pos) or None if no URL found in line."""
    m = _URL_RE.search(line)
    if m:
        return m.group(0), m.start()
    return None, -1


def _is_only_url(line: str) -> bool:
    """True when the entire stripped line is a URL."""
    stripped = line.strip()
    m = _URL_RE.fullmatch(stripped)
    return bool(m)


def parse_txt(raw: str) -> List[Tuple[str, str]]:
    """
    Returns list of (url, title) tuples.
    title is an empty string when none was detected.
    """
    results:      List[Tuple[str, str]] = []
    pending_title: str                   = ""

    for line in raw.splitlines():
        line = line.rstrip()

        # Skip comments
        if line.lstrip().startswith("#"):
            continue

        # Empty line → reset pending title
        if not line.strip():
            pending_title = ""
            continue

        url, pos = _find_url(line)

        if url is None:
            # No URL on this line → it must be a standalone title
            pending_title = line.strip()
            continue

        # URL found — extract title from the part BEFORE the URL
        before = line[:pos].strip()

        # Strip common separator characters from the end of the title part
        # Handles: "Title:", "Title |", "Title ||", "Title - "
        before = re.sub(r"[\s|:\-]+$", "", before).strip()

        title = before if before else pending_title
        results.append((url, title))
        pending_title = ""   # consume pending title

    return results


def validate(
    entries: List[Tuple[str, str]]
) -> Tuple[List[Tuple[str, str]], List[str]]:
    """
    Returns (valid_entries, skipped_reasons).
    Blocks private-IP / localhost URLs (SSRF protection).
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
