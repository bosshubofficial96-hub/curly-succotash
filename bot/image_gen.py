"""
Welcome card image generator.

Generates a styled SVG welcome card with user info,
then converts it to PNG using cairosvg (preferred) or Pillow as fallback.
The PNG is returned as bytes ready to send via Telegram send_photo.
"""

import io
import os
import textwrap
from datetime import datetime
from typing import Optional

from config.settings import (
    BOT_ACCENT_COLOR, BOT_LOGO_EMOJI, BOT_NAME, BOT_THEME_COLOR,
)


# ─── SVG template ─────────────────────────────────────────────────────────────

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
    tc   = BOT_THEME_COLOR    # primary / gradient start
    ac   = BOT_ACCENT_COLOR   # accent
    tc2  = "#9B8FFF"          # gradient end
    role = "⭐ Admin" if is_admin else "👤 Member"

    # bytes → human
    def _fz(b: int) -> str:
        if b < 1024:          return f"{b} B"
        if b < 1 << 20:       return f"{b/1024:.1f} KB"
        if b < 1 << 30:       return f"{b/(1<<20):.1f} MB"
        return f"{b/(1<<30):.2f} GB"

    uname  = f"@{username}" if username else "—"
    short  = (first_name[:18] + "…") if len(first_name) > 18 else first_name
    data_s = _fz(bytes_sent)
    now_s  = datetime.utcnow().strftime("%Y-%m-%d")

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="520" height="300"
     viewBox="0 0 520 300" font-family="'Segoe UI',Arial,sans-serif">
  <defs>
    <!-- Card background gradient -->
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%"   style="stop-color:#0F0F1A"/>
      <stop offset="100%" style="stop-color:#1A1A2E"/>
    </linearGradient>
    <!-- Header gradient -->
    <linearGradient id="hdr" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%"   style="stop-color:{tc}"/>
      <stop offset="100%" style="stop-color:{tc2}"/>
    </linearGradient>
    <!-- Accent bar -->
    <linearGradient id="acc" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%"   style="stop-color:{tc}"/>
      <stop offset="50%"  style="stop-color:{ac}"/>
      <stop offset="100%" style="stop-color:{tc2}"/>
    </linearGradient>
    <!-- Glow filter -->
    <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="4" result="blur"/>
      <feComposite in="SourceGraphic" in2="blur" operator="over"/>
    </filter>
    <!-- Card shadow -->
    <filter id="shadow">
      <feDropShadow dx="0" dy="4" stdDeviation="8" flood-color="#000" flood-opacity="0.5"/>
    </filter>
    <!-- Avatar clip -->
    <clipPath id="av"><circle cx="68" cy="100" r="38"/></clipPath>
  </defs>

  <!-- Card background -->
  <rect width="520" height="300" rx="18" ry="18" fill="url(#bg)" filter="url(#shadow)"/>

  <!-- Decorative circles (background blobs) -->
  <circle cx="460" cy="40"  r="80" fill="{tc}" fill-opacity="0.08"/>
  <circle cx="30"  cy="260" r="60" fill="{ac}" fill-opacity="0.07"/>
  <circle cx="260" cy="150" r="120" fill="{tc2}" fill-opacity="0.04"/>

  <!-- Top accent bar -->
  <rect x="0" y="0" width="520" height="5" rx="3" fill="url(#acc)"/>

  <!-- Header band -->
  <rect x="0" y="5" width="520" height="58" fill="url(#hdr)" fill-opacity="0.18"/>

  <!-- Bot icon & title -->
  <text x="24" y="43" font-size="28" fill="white">{BOT_LOGO_EMOJI}</text>
  <text x="60" y="35" font-size="17" font-weight="700" fill="white">{BOT_NAME}</text>
  <text x="60" y="53" font-size="11" fill="{tc2}" letter-spacing="1">ADVANCED DRM BYPASS SYSTEM</text>

  <!-- Role badge -->
  <rect x="400" y="14" width="100" height="24" rx="12" fill="{tc}" fill-opacity="0.6"/>
  <text x="450" y="31" font-size="11" fill="white" text-anchor="middle">{role}</text>

  <!-- Divider -->
  <line x1="16" y1="68" x2="504" y2="68" stroke="{tc}" stroke-width="1" stroke-opacity="0.4"/>

  <!-- Avatar placeholder circle -->
  <circle cx="68" cy="110" r="40" fill="{tc}" fill-opacity="0.25" filter="url(#glow)"/>
  <circle cx="68" cy="110" r="38" fill="none" stroke="{tc}" stroke-width="2.5"/>
  <!-- Avatar initials -->
  <text x="68" y="116" font-size="26" font-weight="700" fill="white"
        text-anchor="middle" dominant-baseline="middle">
    {(first_name[:1] or "U").upper()}
  </text>

  <!-- Status dot (online) -->
  <circle cx="97" cy="140" r="7" fill="#1A1A2E"/>
  <circle cx="97" cy="140" r="5" fill="#4CAF50"/>

  <!-- User name -->
  <text x="122" y="95" font-size="20" font-weight="700" fill="white">{short}</text>
  <!-- Username -->
  <text x="122" y="116" font-size="13" fill="{tc2}">{uname}</text>
  <!-- User ID -->
  <text x="122" y="135" font-size="11" fill="#888">🆔 {user_id}</text>
  <!-- Joined -->
  <text x="122" y="152" font-size="11" fill="#888">📅 Joined {joined[:10]}</text>

  <!-- Divider -->
  <line x1="16" y1="170" x2="504" y2="170" stroke="{tc}" stroke-width="0.8" stroke-opacity="0.3"/>

  <!-- Stats row -->
  <!-- Jobs -->
  <rect x="20"  y="182" width="145" height="68" rx="10" fill="{tc}" fill-opacity="0.12"/>
  <text x="92"  y="210" font-size="22" font-weight="700" fill="{tc}" text-anchor="middle">{jobs}</text>
  <text x="92"  y="228" font-size="11" fill="#aaa" text-anchor="middle">📦 Total Jobs</text>
  <text x="92"  y="243" font-size="10" fill="#666" text-anchor="middle">submitted</text>

  <!-- Files -->
  <rect x="188" y="182" width="145" height="68" rx="10" fill="{ac}" fill-opacity="0.12"/>
  <text x="260" y="210" font-size="22" font-weight="700" fill="{ac}" text-anchor="middle">{files}</text>
  <text x="260" y="228" font-size="11" fill="#aaa" text-anchor="middle">📄 Files Sent</text>
  <text x="260" y="243" font-size="10" fill="#666" text-anchor="middle">downloaded</text>

  <!-- Data -->
  <rect x="356" y="182" width="145" height="68" rx="10" fill="{tc2}" fill-opacity="0.12"/>
  <text x="428" y="210" font-size="16" font-weight="700" fill="{tc2}" text-anchor="middle">{data_s}</text>
  <text x="428" y="228" font-size="11" fill="#aaa" text-anchor="middle">💾 Data Sent</text>
  <text x="428" y="243" font-size="10" fill="#666" text-anchor="middle">total volume</text>

  <!-- Bottom bar -->
  <rect x="0" y="275" width="520" height="25" fill="url(#hdr)" fill-opacity="0.15" rx="0"/>
  <rect x="0" y="295" width="520" height="5" rx="3" fill="url(#acc)"/>
  <text x="260" y="289" font-size="10" fill="#666" text-anchor="middle">
    Generated {now_s} · {BOT_NAME}
  </text>
</svg>"""
    return svg


# ─── SVG → PNG conversion ──────────────────────────────────────────────────────

def _svg_to_png_cairosvg(svg: str) -> Optional[bytes]:
    try:
        import cairosvg
        return cairosvg.svg2png(bytestring=svg.encode("utf-8"), scale=2.0)
    except Exception:
        return None


def _svg_to_png_pillow(svg: str) -> Optional[bytes]:
    """
    Fallback: renders a simpler image using Pillow when cairosvg is unavailable.
    Parses key values from the SVG data already prepared and draws them.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
        import re

        W, H = 1040, 600
        img  = Image.new("RGB", (W, H), "#0F0F1A")
        d    = ImageDraw.Draw(img)

        # Helper — pick font size
        def _font(size: int):
            try:
                return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)
            except Exception:
                return ImageFont.load_default()

        tc = BOT_THEME_COLOR
        ac = BOT_ACCENT_COLOR

        # Top gradient bar (simulated)
        for x in range(W):
            r  = int(0x6C + (0x9B - 0x6C) * x / W)
            g  = int(0x63 + (0x8F - 0x63) * x / W)
            b  = int(0xFF + (0xFF - 0xFF) * x / W)
            d.line([(x, 0), (x, 10)], fill=(r, g, b))

        # Header
        d.rectangle([0, 10, W, 120], fill="#16162A")
        d.text((48, 20), BOT_LOGO_EMOJI + "  " + BOT_NAME,
               fill="white", font=_font(36))
        d.text((48, 70), "ADVANCED DRM BYPASS SYSTEM",
               fill="#9B8FFF", font=_font(20))

        # Extract name/values with simple regex from SVG
        def _ex(tag: str) -> str:
            m = re.search(rf'>{tag}(.*?)<', svg)
            return m.group(1).strip() if m else ""

        # Avatar circle
        d.ellipse([80, 140, 220, 280], fill="#6C63FF30", outline="#6C63FF", width=4)
        d.text((118, 188), (BOT_NAME[:1] or "U").upper(), fill="white", font=_font(52))

        # Info area — read directly from function args embedded in SVG text nodes
        # We use a different approach: re-parse SVG text content
        texts = re.findall(r'<text[^>]*>([^<]+)</text>', svg)
        name_text = texts[2] if len(texts) > 2 else "User"
        uname_text = texts[3] if len(texts) > 3 else ""
        uid_text   = texts[4] if len(texts) > 4 else ""
        join_text  = texts[5] if len(texts) > 5 else ""

        d.text((240, 150), name_text, fill="white", font=_font(38))
        d.text((240, 206), uname_text, fill="#9B8FFF", font=_font(26))
        d.text((240, 244), uid_text,   fill="#888888", font=_font(22))
        d.text((240, 278), join_text,  fill="#888888", font=_font(22))

        # Divider
        d.line([(32, 330), (W - 32, 330)], fill="#6C63FF44", width=2)

        # Stat boxes
        for i, (num, lbl, clr) in enumerate([
            (texts[8]  if len(texts) > 8  else "0", "Total Jobs",   "#6C63FF"),
            (texts[11] if len(texts) > 11 else "0", "Files Sent",   "#FF6584"),
            (texts[14] if len(texts) > 14 else "0", "Data Sent",    "#9B8FFF"),
        ]):
            bx = 40 + i * 340
            d.rounded_rectangle([bx, 350, bx + 300, 500], radius=20,
                                  fill=clr + "22", outline=clr + "44")
            d.text((bx + 150, 400), str(num).strip(), fill=clr,
                   font=_font(44), anchor="mm")
            d.text((bx + 150, 454), lbl, fill="#aaaaaa",
                   font=_font(22), anchor="mm")

        # Bottom bar
        for x in range(W):
            r  = int(0x6C + (0x9B - 0x6C) * x / W)
            g  = int(0x63 + (0x8F - 0x63) * x / W)
            b  = int(0xFF + (0xFF - 0xFF) * x / W)
            d.line([(x, H - 10), (x, H)], fill=(r, g, b))

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception:
        return None


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
    """
    Returns PNG bytes of the welcome card, or None if generation fails.
    """
    svg = _make_svg(first_name, username, user_id, joined,
                    jobs, files, bytes_sent, is_admin)

    # Save SVG to assets/
    try:
        os.makedirs("assets", exist_ok=True)
        with open(os.path.join("assets", f"welcome_{user_id}.svg"), "w", encoding="utf-8") as f:
            f.write(svg)
    except Exception:
        pass

    # Try cairosvg first (best quality), then Pillow fallback
    png = _svg_to_png_cairosvg(svg) or _svg_to_png_pillow(svg)
    return png


def get_svg_source(user_id: int) -> Optional[str]:
    """Return the raw SVG string for a previously generated card."""
    path = os.path.join("assets", f"welcome_{user_id}.svg")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return None      
