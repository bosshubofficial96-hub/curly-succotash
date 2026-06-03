"""Progress tracker + styled message builder."""

import time
from .fonts import bold, smallcaps, DIVIDER


def _fmt(n: int) -> str:
    if n < 1024:       return f"{n} B"
    if n < 1 << 20:    return f"{n/1024:.1f} KB"
    if n < 1 << 30:    return f"{n/(1<<20):.1f} MB"
    return f"{n/(1<<30):.2f} GB"

def _eta(s: float) -> str:
    if s < 0 or s > 86400*7: return "—"
    h, r = divmod(int(s), 3600); m, s = divmod(r, 60)
    if h: return f"{h}h {m}m {s}s"
    if m: return f"{m}m {s}s"
    return f"{s}s"

def _bar(done: int, total: int, w: int = 10) -> str:
    if not total: return "░" * w
    f = min(int(w * done / total), w)
    return "█" * f + "░" * (w - f)

def _pct(done: int, total: int) -> int:
    return int(100 * done / total) if total else 0


class JobProgress:
    def __init__(self, total: int, start: int = 0):
        self.total     = total
        self.current   = start
        self.completed = 0
        self.failed    = 0
        self.t0        = time.monotonic()
        self.phase     = "idle"
        self.dl_done   = 0
        self.dl_total  = 0
        self.filename  = ""
        self.attempt   = 0
        self.speed     = 0.0          # bytes/sec
        self._dl_t0    = time.monotonic()
        self._dl_last  = 0

    def on_download(self, done: int, total: int) -> None:
        now   = time.monotonic()
        delta = done - self._dl_last
        dt    = now - self._dl_t0
        if dt > 0.5:
            self.speed  = delta / dt
            self._dl_t0 = now
            self._dl_last = done
        self.dl_done  = done
        self.dl_total = total
        self.phase    = "downloading"

    def on_upload(self) -> None: self.phase = "uploading"

    def on_retry(self, att: int) -> None:
        self.phase   = "retrying"
        self.attempt = att

    def on_done(self, ok: bool) -> None:
        if ok: self.completed += 1
        else:  self.failed    += 1
        self.current   += 1
        self.phase      = "idle"
        self.dl_done    = 0
        self.dl_total   = 0
        self.attempt    = 0
        self.speed      = 0.0

    def eta(self) -> float:
        elapsed = time.monotonic() - self.t0
        done    = self.completed + self.failed
        if done == 0 or elapsed == 0: return -1.0
        return (self.total - self.current) * elapsed / done

    def render(self, job_id: str = "") -> str:
        pct = _pct(self.current, self.total)
        bar = _bar(self.current, self.total)

        lines = [
            f"🤖 {bold('AppX Uploader Bot')}",
            DIVIDER,
            f"📦 {bold('Progress')}  {self.current}/{self.total}  [{bar}] {pct}%",
        ]
        if self.filename:
            fn = (self.filename[:36] + "…") if len(self.filename) > 36 else self.filename
            lines.append(f"📄 <code>{fn}</code>")

        if self.phase == "downloading":
            if self.dl_total:
                db = _bar(self.dl_done, self.dl_total, 8)
                dp = _pct(self.dl_done, self.dl_total)
                spd = f"  ⚡ {_fmt(int(self.speed))}/s" if self.speed > 1024 else ""
                lines.append(
                    f"⬇️  {_fmt(self.dl_done)}/{_fmt(self.dl_total)}  [{db}] {dp}%{spd}"
                )
            else:
                lines.append("⬇️  Downloading…")
        elif self.phase == "uploading":
            lines.append("⬆️  Sending to Telegram…")
        elif self.phase == "retrying":
            lines.append(f"🔄  Retry {self.attempt}…")

        lines += [
            DIVIDER,
            f"✅ {smallcaps('Completed')} : {bold(str(self.completed))}",
            f"❌ {smallcaps('Failed')}    : {bold(str(self.failed))}",
        ]
        e = self.eta()
        if e >= 0:
            lines.append(f"⏱  {smallcaps('ETA')}       : {bold(_eta(e))}")
        if job_id:
            lines.append(f"\n🆔 <code>{job_id[:8]}…</code>")
        return "\n".join(lines)
