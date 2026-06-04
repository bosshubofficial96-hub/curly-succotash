"""
Welcome card image generator — v3 FIXED.

Changes from v2:
  • html.escape() on ALL user-supplied strings (names, usernames, IDs)
  • Robust cairosvg → Pillow fallback
  • Better font metrics in SVG (dominant-baseline, text-anchor)
  • Handles empty / special-char names gracefully
"""

import html
import io
import os
from datetime import datetime
from typing import Optional

from config.settings import (
    BOT_ACCENT_COLOR, BOT_LOGO_EMOJI, BOT_NAME, BOT_THEME_COLOR,
)


# ── SVG escape ────────────────────────────────────────────────────────────────
def _esc(s: str) -> str:
    """HTML-escape a string for safe embedding inside SVG text/attributes."""
    return html.escape(str(s), quote=True)


# ── Size helpers ──────────────────────────────────────────────────────────────
def _fz(b: int) -> str:
    if b < 1024:       return f"{b} B"
    if b < 1 << 20:    return f"{b/1024:.1f} KB"
    if b < 1 << 30:    return f"{b/(1<<20):.1f} MB"
    return f"{b/(1<<30):.2f} GB"


# ── SVG template ──────────────────────────────────────────────────────────────
def _make_svg(
    first_name: str,
    username:   str,
    user_id:    int,
    joined:     str,
    jobs:       int,
    files:      int,
    bytes_sent: int,
    is_admin:   bool = False,
) -> str:
    tc   = BOT_THEME_COLOR
    ac   = BOT_ACCENT_COLOR
    tc2  = "#9B8FFF"
    role = "⭐ Admin" if is_admin else "👤 Member"

    # Safe-escape all user data
    name_raw  = first_name.strip() or "User"
    short_raw = (name_raw[:18] + "…") if len(name_raw) > 18 else name_raw
    short     = _esc(short_raw)
    uname     = _esc(f"@{username}" if username else "—")
    uid_s     = _esc(str(user_id))
    joined_s  = _esc(joined[:10] if joined else "—")
    initial   = _esc((name_raw[:1] or "U").upper())
    data_s    = _esc(_fz(bytes_sent))
    now_s     = _esc(datetime.utcnow().strftime("%Y-%m-%d"))
    role_s    = _esc(role)
    jobs_s    = _esc(str(jobs))
    files_s   = _esc(str(files))
    botname_s = _esc(BOT_NAME)

    return f"""<svg xmlns="http://www.w3.org/2000/svg"
     width="520" height="300" viewBox="0 0 520 300"
     font-family="'Segoe UI',Arial,Helvetica,sans-serif">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%"   stop-color="#0F0F1A"/>
      <stop offset="100%" stop-color="#1A1A2E"/>
    </linearGradient>
    <linearGradient id="hdr" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%"   stop-color="{tc}"/>
      <stop offset="100%" stop-color="{tc2}"/>
    </linearGradient>
    <linearGradient id="acc" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%"   stop-color="{tc}"/>
      <stop offset="50%"  stop-color="{ac}"/>
      <stop offset="100%" stop-color="{tc2}"/>
    </linearGradient>
    <filter id="glow">
      <feGaussianBlur stdDeviation="3" result="b"/>
      <feComposite in="SourceGraphic" in2="b" operator="over"/>
    </filter>
    <clipPath id="av"><circle cx="68" cy="110" r="38"/></clipPath>
  </defs>

  <!-- Background -->
  <rect width="520" height="300" rx="16" fill="url(#bg)"/>

  <!-- Blobs -->
  <circle cx="460" cy="40"  r="80" fill="{tc}"  fill-opacity="0.07"/>
  <circle cx="30"  cy="260" r="60" fill="{ac}"  fill-opacity="0.06"/>
  <circle cx="260" cy="150" r="120" fill="{tc2}" fill-opacity="0.03"/>

  <!-- Top accent bar -->
  <rect x="0" y="0" width="520" height="5" rx="2" fill="url(#acc)"/>

  <!-- Header band -->
  <rect x="0" y="5" width="520" height="58" fill="url(#hdr)" fill-opacity="0.15"/>

  <!-- Bot title -->
  <text x="24" y="43" font-size="26" fill="white">🤖</text>
  <text x="58" y="35" font-size="16" font-weight="700" fill="white">{botname_s}</text>
  <text x="58" y="53" font-size="10" fill="{tc2}" letter-spacing="1.2">ADVANCED DRM BYPASS SYSTEM</text>

  <!-- Role badge -->
  <rect x="396" y="14" width="108" height="24" rx="12" fill="{tc}" fill-opacity="0.5"/>
  <text x="450" y="30" font-size="11" fill="white"
        text-anchor="middle" dominant-baseline="middle">{role_s}</text>

  <!-- Divider -->
  <line x1="16" y1="68" x2="504" y2="68" stroke="{tc}" stroke-width="1" stroke-opacity="0.35"/>

  <!-- Avatar ring + circle -->
  <circle cx="68" cy="110" r="42" fill="{tc}" fill-opacity="0.15" filter="url(#glow)"/>
  <circle cx="68" cy="110" r="39" fill="none" stroke="{tc}" stroke-width="2.5"/>
  <circle cx="68" cy="110" r="36" fill="{tc}" fill-opacity="0.2"/>
  <text x="68" y="110" font-size="28" font-weight="700" fill="white"
        text-anchor="middle" dominant-baseline="central">{initial}</text>

  <!-- Online dot -->
  <circle cx="98" cy="140" r="8" fill="#0F0F1A"/>
  <circle cx="98" cy="140" r="6" fill="#4CAF50"/>

  <!-- User info -->
  <text x="122" y="90"  font-size="19" font-weight="700" fill="white">{short}</text>
  <text x="122" y="112" font-size="12" fill="{tc2}">{uname}</text>
  <text x="122" y="130" font-size="11" fill="#7a7a9a">🆔 {uid_s}</text>
  <text x="122" y="148" font-size="11" fill="#7a7a9a">📅 Joined {joined_s}</text>

  <!-- Divider -->
  <line x1="16" y1="168" x2="504" y2="168" stroke="{tc}" stroke-width="0.7" stroke-opacity="0.25"/>

  <!-- Stat box 1 — Jobs -->
  <rect x="20"  y="180" width="148" height="70" rx="10" fill="{tc}"  fill-opacity="0.1"/>
  <text x="94"  y="210" font-size="24" font-weight="700" fill="{tc}"
        text-anchor="middle" dominant-baseline="central">{jobs_s}</text>
  <text x="94"  y="232" font-size="11" fill="#aaa"
        text-anchor="middle">📦 Total Jobs</text>
  <text x="94"  y="246" font-size="9" fill="#555"
        text-anchor="middle">submitted</text>

  <!-- Stat box 2 — Files -->
  <rect x="186" y="180" width="148" height="70" rx="10" fill="{ac}"  fill-opacity="0.1"/>
  <text x="260" y="210" font-size="24" font-weight="700" fill="{ac}"
        text-anchor="middle" dominant-baseline="central">{files_s}</text>
  <text x="260" y="232" font-size="11" fill="#aaa"
        text-anchor="middle">📄 Files Sent</text>
  <text x="260" y="246" font-size="9" fill="#555"
        text-anchor="middle">downloaded</text>

  <!-- Stat box 3 — Data -->
  <rect x="352" y="180" width="148" height="70" rx="10" fill="{tc2}" fill-opacity="0.1"/>
  <text x="426" y="210" font-size="18" font-weight="700" fill="{tc2}"
        text-anchor="middle" dominant-baseline="central">{data_s}</text>
  <text x="426" y="232" font-size="11" fill="#aaa"
        text-anchor="middle">💾 Data Sent</text>
  <text x="426" y="246" font-size="9" fill="#555"
        text-anchor="middle">total volume</text>

  <!-- Bottom bar -->
  <rect x="0" y="295" width="520" height="5" rx="2" fill="url(#acc)"/>
  <text x="260" y="284" font-size="9" fill="#3a3a5a"
        text-anchor="middle">Generated {now_s} · {botname_s}</text>
</svg>"""


# ── SVG → PNG ─────────────────────────────────────────────────────────────────
def _to_png_cairo(svg: str) -> Optional[bytes]:
    try:
        import cairosvg
        return cairosvg.svg2png(bytestring=svg.encode("utf-8"), scale=2.0)
    except Exception:
        return None


def _to_png_pillow(
    first_name: str,
    username:   str,
    user_id:    int,
    joined:     str,
    jobs:       int,
    files:      int,
    bytes_sent: int,
    is_admin:   bool,
) -> Optional[bytes]:
    """Pure-Pillow fallback welcome card."""
    try:
        from PIL import Image, ImageDraw, ImageFont

        W, H = 1040, 600
        tc   = BOT_THEME_COLOR
        ac   = BOT_ACCENT_COLOR
        img  = Image.new("RGB", (W, H), "#0F0F1A")
        d    = ImageDraw.Draw(img)

        def _font(sz: int, bold: bool = False):
            paths = [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            ]
            for p in paths:
                if os.path.exists(p):
                    try:
                        return ImageFont.truetype(p, sz)
                    except Exception:
                        pass
            return ImageFont.load_default()

        # Accent bar top
        for x in range(W):
            r_c = int(0x6C + (0x9B - 0x6C) * x / W)
            g_c = int(0x63 + (0x8F - 0x63) * x / W)
            b_c = 0xFF
            d.line([(x, 0), (x, 10)], fill=(r_c, g_c, b_c))

        # Header
        d.rectangle([0, 10, W, 120], fill="#16162A")
        d.text((50, 22), f"🤖 {BOT_NAME}", fill="white", font=_font(34, True))
        d.text((50, 72), "ADVANCED DRM BYPASS SYSTEM", fill="#9B8FFF", font=_font(18))

        # Avatar circle
        d.ellipse([60, 140, 220, 300], fill="#1a1a40", outline="#6C63FF", width=4)
        init = (first_name[:1] or "U").upper()
        d.text((140, 220), init, fill="white", font=_font(52, True), anchor="mm")

        # Info
        d.text((250, 150), (first_name[:20] or "User"),  fill="white",   font=_font(36, True))
        d.text((250, 204), f"@{username or '—'}",         fill="#9B8FFF", font=_font(24))
        d.text((250, 244), f"🆔 {user_id}",               fill="#888",    font=_font(20))
        d.text((250, 278), f"📅 Joined {joined[:10] if joined else '—'}", fill="#888", font=_font(20))

        # Divider
        d.line([(40, 330), (W - 40, 330)], fill="#333366", width=2)

        # Stat boxes
        for i, (num, lbl, clr) in enumerate([
            (str(jobs),           "Total Jobs",   "#6C63FF"),
            (str(files),          "Files Sent",   "#FF6584"),
            (_fz(bytes_sent),     "Data Sent",    "#9B8FFF"),
        ]):
            bx = 40 + i * 330
            d.rounded_rectangle([bx, 350, bx + 300, 510], radius=20,
                                  fill=clr + "22", outline=clr + "55")
            d.text((bx + 150, 420), num,  fill=clr,    font=_font(38, True), anchor="mm")
            d.text((bx + 150, 468), lbl,  fill="#aaa", font=_font(20),       anchor="mm")

        # Accent bar bottom
        for x in range(W):
            r_c = int(0x6C + (0x9B - 0x6C) * x / W)
            g_c = int(0x63 + (0x8F - 0x63) * x / W)
            d.line([(x, H - 10), (x, H)], fill=(r_c, g_c, 0xFF))

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Pillow fallback failed: %s", e)
        return None


# ── Public API ────────────────────────────────────────────────────────────────
def generate_welcome_card(
    first_name: str,
    username:   str,
    user_id:    int,
    joined:     str,
    jobs:       int,
    files:      int,
    bytes_sent: int,
    is_admin:   bool = False,
) -> Optional[bytes]:
    """Returns PNG bytes or None."""
    # Sanitise
    first_name = str(first_name or "User").strip()
    username   = str(username   or "").strip()

    svg = _make_svg(first_name, username, user_id, joined,
                    jobs, files, bytes_sent, is_admin)

    # Persist SVG source
    try:
        os.makedirs("assets", exist_ok=True)
        with open(os.path.join("assets", f"welcome_{user_id}.svg"), "w", encoding="utf-8") as f:
            f.write(svg)
    except Exception:
        pass

    # cairosvg first (best quality), Pillow fallback
    png = _to_png_cairo(svg)
    if png:
        return png
    return _to_png_pillow(first_name, username, user_id, joined,
                           jobs, files, bytes_sent, is_admin)


def get_svg_source(user_id: int) -> Optional[str]:
    path = os.path.join("assets", f"welcome_{user_id}.svg")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return None
