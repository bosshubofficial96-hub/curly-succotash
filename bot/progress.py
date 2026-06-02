"""
Progress tracking and formatted message builder for Telegram updates.
"""

import time
from typing import Optional


def format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / 1024 ** 2:.1f} MB"
    return f"{size_bytes / 1024 ** 3:.2f} GB"


def format_eta(seconds: float) -> str:
    if seconds < 0 or seconds > 86400 * 7:
        return "?"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def progress_bar(done: int, total: int, width: int = 12) -> str:
    if total == 0:
        return "░" * width
    filled = int(width * done / total)
    filled = min(filled, width)
    return "█" * filled + "░" * (width - filled)


class JobProgress:
    """Tracks timing and progress for one processing job."""

    def __init__(self, total: int, start_index: int = 0):
        self.total = total
        self.current = start_index
        self.completed = 0
        self.failed = 0
        self.start_time = time.monotonic()
        self.phase = "idle"          # idle | downloading | uploading
        self.dl_done = 0
        self.dl_total = 0
        self.file_start = time.monotonic()

    def update_download(self, done: int, total: int) -> None:
        self.dl_done = done
        self.dl_total = total
        self.phase = "downloading"

    def finish_item(self, success: bool) -> None:
        if success:
            self.completed += 1
        else:
            self.failed += 1
        self.current += 1
        self.phase = "idle"
        self.dl_done = 0
        self.dl_total = 0

    def eta_seconds(self) -> float:
        elapsed = time.monotonic() - self.start_time
        done = self.completed + self.failed
        if done == 0:
            return -1
        rate = done / elapsed           # items per second
        remaining = self.total - self.current
        if rate == 0:
            return -1
        return remaining / rate

    def build_message(self, filename: str = "", job_id: str = "") -> str:
        bar_job = progress_bar(self.current, self.total)
        pct_job = int(100 * self.current / self.total) if self.total else 0
        eta = self.eta_seconds()

        lines = [
            "📦 <b>AppX Uploader Bot</b>",
            "",
            f"🔗 Processing: <b>{self.current}/{self.total}</b>  [{bar_job}] {pct_job}%",
        ]

        if filename:
            short = filename[:40] + "…" if len(filename) > 40 else filename
            lines.append(f"📄 File: <code>{short}</code>")

        if self.phase == "downloading" and self.dl_total > 0:
            bar_dl = progress_bar(self.dl_done, self.dl_total)
            pct_dl = int(100 * self.dl_done / self.dl_total)
            lines.append(
                f"⬇️  Downloading: {format_size(self.dl_done)}/{format_size(self.dl_total)}"
                f"  [{bar_dl}] {pct_dl}%"
            )
        elif self.phase == "uploading":
            lines.append("⬆️  Uploading to Telegram…")

        lines += [
            "",
            f"✅ Completed : <b>{self.completed}</b>",
            f"❌ Failed    : <b>{self.failed}</b>",
        ]

        if eta >= 0:
            lines.append(f"⏱  ETA        : <b>{format_eta(eta)}</b>")

        if job_id:
            lines.append(f"\n🆔 Job: <code>{job_id[:8]}</code>")

        return "\n".join(lines)
