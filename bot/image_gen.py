"""
Welcome card image generator.

Generates a styled SVG welcome card with user info and profile photo,
then converts it to PNG using cairosvg (preferred) or Pillow as fallback.
The PNG is returned as bytes ready to send via Telegram send_photo.
"""

import io
import os
import re
import textwrap
from datetime import datetime
from typing import Optional
from pathlib import Path

from config.settings import (
    BOT_ACCENT_COLOR, BOT_LOGO_EMOJI, BOT_NAME, BOT_THEME_COLOR,
)


# ─── SVG template ─────────────────────────────────────────────────────────────

def _make_svg(
    first_name: str,
    username: str,
    user_id: int,
    joined: str,
    jobs: int,
    files: int,
    bytes_sent: int,
    is_admin: bool = False,
    profile_photo_path: Optional[str] = None,
    additional_info: Optional[dict] = None,
) -> str:
    """Generate SVG with optional profile photo and advanced info."""
    
    tc = BOT_THEME_COLOR    # primary / gradient start
    ac = BOT_ACCENT_COLOR   # accent
    tc2 = "#9B8FFF"         # gradient end
    role = "⭐ Admin" if is_admin else "👤 Member"
    
    # Additional info handling
    additional = additional_info or {}
    country = additional.get('country', '🌍 Unknown')
    language = additional.get('language', '📖 Unknown')
    device = additional.get('device', '💻 Unknown')
    premium = additional.get('is_premium', False)
    
    # Premium badge
    premium_badge = "👑 PREMIUM" if premium else ""
    
    # Bytes → human readable
    def _fz(b: int) -> str:
        if b < 1024:
            return f"{b} B"
        if b < 1 << 20:
            return f"{b/1024:.1f} KB"
        if b < 1 << 30:
            return f"{b/(1<<20):.1f} MB"
        return f"{b/(1<<30):.2f} GB"
    
    uname = f"@{username}" if username else "—"
    short = (first_name[:18] + "…") if len(first_name) > 18 else first_name
    data_s = _fz(bytes_sent)
    now_s = datetime.utcnow().strftime("%Y-%m-%d")
    
    # Profile photo handling (base64 encoded or use initials)
    profile_image_svg = ""
    if profile_photo_path and os.path.exists(profile_photo_path):
        try:
            import base64
            with open(profile_photo_path, "rb") as img_file:
                img_data = base64.b64encode(img_file.read()).decode('utf-8')
                profile_image_svg = f'''
                <clipPath id="avClip">
                    <circle cx="68" cy="100" r="38"/>
                </clipPath>
                <image x="30" y="62" width="76" height="76" 
                       href="data:image/jpeg;base64,{img_data}" 
                       clip-path="url(#avClip)" preserveAspectRatio="xMidYMid slice"/>
                '''
        except Exception as e:
            print(f"Failed to load profile photo: {e}")
    
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="520" height="340"
     viewBox="0 0 520 340" font-family="'Segoe UI',Arial,sans-serif">
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
  </defs>

  <!-- Card background -->
  <rect width="520" height="340" rx="18" ry="18" fill="url(#bg)" filter="url(#shadow)"/>

  <!-- Decorative circles (background blobs) -->
  <circle cx="460" cy="40"  r="80" fill="{tc}" fill-opacity="0.08"/>
  <circle cx="30"  cy="300" r="60" fill="{ac}" fill-opacity="0.07"/>
  <circle cx="260" cy="180" r="120" fill="{tc2}" fill-opacity="0.04"/>

  <!-- Top accent bar -->
  <rect x="0" y="0" width="520" height="5" rx="3" fill="url(#acc)"/>

  <!-- Header band -->
  <rect x="0" y="5" width="520" height="58" fill="url(#hdr)" fill-opacity="0.18"/>

  <!-- Bot icon & title -->
  <text x="24" y="43" font-size="28" fill="white">{BOT_LOGO_EMOJI}</text>
  <text x="60" y="35" font-size="17" font-weight="700" fill="white">{BOT_NAME}</text>
  <text x="60" y="53" font-size="11" fill="{tc2}" letter-spacing="1">ADVANCED DRM BYPASS SYSTEM</text>

  <!-- Role badge -->
  <rect x="380" y="14" width="120" height="24" rx="12" fill="{tc}" fill-opacity="0.6"/>
  <text x="440" y="31" font-size="11" fill="white" text-anchor="middle">{role}</text>
  
  <!-- Premium badge -->
  {f'<rect x="280" y="14" width="85" height="24" rx="12" fill="#FFD700" fill-opacity="0.8"/><text x="322" y="31" font-size="10" fill="#000" text-anchor="middle" font-weight="700">PREMIUM</text>' if premium else ''}

  <!-- Divider -->
  <line x1="16" y1="68" x2="504" y2="68" stroke="{tc}" stroke-width="1" stroke-opacity="0.4"/>

  <!-- Avatar section -->
  {profile_image_svg if profile_image_svg else f'''
  <circle cx="68" cy="110" r="40" fill="{tc}" fill-opacity="0.25" filter="url(#glow)"/>
  <circle cx="68" cy="110" r="38" fill="none" stroke="{tc}" stroke-width="2.5"/>
  <text x="68" y="116" font-size="26" font-weight="700" fill="white"
        text-anchor="middle" dominant-baseline="middle">
    {(first_name[:1] or "U").upper()}
  </text>
  '''}

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

  <!-- Additional Info Row -->
  <rect x="16" y="162" width="488" height="28" rx="8" fill="{tc}" fill-opacity="0.08"/>
  <text x="30" y="181" font-size="11" fill="#999">{country}  •  {language}  •  {device}</text>

  <!-- Divider -->
  <line x1="16" y1="198" x2="504" y2="198" stroke="{tc}" stroke-width="0.8" stroke-opacity="0.3"/>

  <!-- Stats row -->
  <rect x="20"  y="210" width="145" height="68" rx="10" fill="{tc}" fill-opacity="0.12"/>
  <text x="92"  y="238" font-size="22" font-weight="700" fill="{tc}" text-anchor="middle">{jobs}</text>
  <text x="92"  y="256" font-size="11" fill="#aaa" text-anchor="middle">📦 Total Jobs</text>
  <text x="92"  y="271" font-size="10" fill="#666" text-anchor="middle">submitted</text>

  <rect x="188" y="210" width="145" height="68" rx="10" fill="{ac}" fill-opacity="0.12"/>
  <text x="260" y="238" font-size="22" font-weight="700" fill="{ac}" text-anchor="middle">{files}</text>
  <text x="260" y="256" font-size="11" fill="#aaa" text-anchor="middle">📄 Files Sent</text>
  <text x="260" y="271" font-size="10" fill="#666" text-anchor="middle">downloaded</text>

  <rect x="356" y="210" width="145" height="68" rx="10" fill="{tc2}" fill-opacity="0.12"/>
  <text x="428" y="238" font-size="16" font-weight="700" fill="{tc2}" text-anchor="middle">{data_s}</text>
  <text x="428" y="256" font-size="11" fill="#aaa" text-anchor="middle">💾 Data Sent</text>
  <text x="428" y="271" font-size="10" fill="#666" text-anchor="middle">total volume</text>

  <!-- Bottom bar -->
  <rect x="0" y="310" width="520" height="30" fill="url(#hdr)" fill-opacity="0.15"/>
  <rect x="0" y="335" width="520" height="5" rx="3" fill="url(#acc)"/>
  <text x="260" y="328" font-size="10" fill="#666" text-anchor="middle">
    Generated {now_s} · {BOT_NAME}
  </text>
</svg>"""
    return svg


# ─── SVG → PNG conversion ──────────────────────────────────────────────────────

def _svg_to_png_cairosvg(svg: str) -> Optional[bytes]:
    """Convert SVG to PNG using cairosvg (best quality)."""
    try:
        import cairosvg
        return cairosvg.svg2png(bytestring=svg.encode("utf-8"), scale=2.0)
    except Exception:
        return None


def _svg_to_png_pillow(svg: str) -> Optional[bytes]:
    """
    Fallback: renders a simpler image using Pillow when cairosvg is unavailable.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
        import re

        W, H = 1040, 680
        img = Image.new("RGB", (W, H), "#0F0F1A")
        d = ImageDraw.Draw(img)

        # Font helper with fallback
        def _get_font(size: int):
            font_paths = [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/System/Library/Fonts/Helvetica.ttf",  # macOS
                "C:\\Windows\\Fonts\\Arial.ttf",  # Windows
            ]
            for path in font_paths:
                try:
                    return ImageFont.truetype(path, size)
                except:
                    continue
            return ImageFont.load_default()

        # Extract values safely
        def extract_value(pattern: str, default: str = "") -> str:
            match = re.search(pattern, svg)
            return match.group(1).strip() if match else default

        tc = BOT_THEME_COLOR
        ac = BOT_ACCENT_COLOR

        # Parse hex colors to RGB
        def hex_to_rgb(hex_color: str):
            hex_color = hex_color.lstrip('#')
            return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

        tc_rgb = hex_to_rgb(tc)
        ac_rgb = hex_to_rgb(ac)
        tc2_rgb = (155, 143, 255)

        # Top gradient
        for x in range(W):
            r = int(tc_rgb[0] + (tc2_rgb[0] - tc_rgb[0]) * x / W)
            g = int(tc_rgb[1] + (tc2_rgb[1] - tc_rgb[1]) * x / W)
            b = int(tc_rgb[2] + (tc2_rgb[2] - tc_rgb[2]) * x / W)
            d.line([(x, 0), (x, 10)], fill=(r, g, b))

        # Header
        d.rectangle([0, 10, W, 130], fill="#16162A")
        d.text((48, 30), f"{BOT_LOGO_EMOJI}  {BOT_NAME}",
               fill="white", font=_get_font(36))
        d.text((48, 80), "ADVANCED DRM BYPASS SYSTEM",
               fill="#9B8FFF", font=_get_font(20))

        # Extract user info
        name_text = extract_value(r'<text[^>]*x="122"[^>]*y="95"[^>]*>([^<]+)</text>', "User")
        uname_text = extract_value(r'<text[^>]*x="122"[^>]*y="116"[^>]*>([^<]+)</text>', "")
        uid_text = extract_value(r'<text[^>]*x="122"[^>]*y="135"[^>]*>([^<]+)</text>', "🆔 0")
        join_text = extract_value(r'<text[^>]*x="122"[^>]*y="152"[^>]*>([^<]+)</text>', "📅 Joined unknown")
        extra_info = extract_value(r'<text[^>]*x="30"[^>]*y="181"[^>]*>([^<]+)</text>', "🌍 Unknown  •  📖 Unknown  •  💻 Unknown")
        
        # Stats
        jobs_count = extract_value(r'<text[^>]*x="92"[^>]*y="238"[^>]*>([^<]+)</text>', "0")
        files_count = extract_value(r'<text[^>]*x="260"[^>]*y="238"[^>]*>([^<]+)</text>', "0")
        data_sent = extract_value(r'<text[^>]*x="428"[^>]*y="238"[^>]*>([^<]+)</text>', "0 B")

        # Draw avatar
        d.ellipse([80, 140, 220, 280], fill=f"{tc}30", outline=tc, width=4)
        d.text((118, 188), (name_text[:1] or "U").upper(), 
               fill="white", font=_get_font(52), anchor="mm")

        # User info
        d.text((240, 160), name_text, fill="white", font=_get_font(38))
        d.text((240, 216), uname_text, fill="#9B8FFF", font=_get_font(26))
        d.text((240, 254), uid_text, fill="#888888", font=_get_font(22))
        d.text((240, 288), join_text, fill="#888888", font=_get_font(22))
        
        # Extra info bar
        d.rounded_rectangle([32, 310, W-32, 350], radius=10, fill=f"{tc}15")
        d.text((40, 335), extra_info, fill="#999999", font=_get_font(18))

        # Divider
        d.line([(32, 370), (W - 32, 370)], fill=f"{tc}44", width=2)

        # Stat boxes
        stats = [
            (jobs_count, "Total Jobs", tc_rgb),
            (files_count, "Files Sent", ac_rgb),
            (data_sent, "Data Sent", tc2_rgb),
        ]
        
        for i, (num, lbl, clr) in enumerate(stats):
            bx = 40 + i * 340
            d.rounded_rectangle([bx, 390, bx + 300, 560], radius=20,
                              fill=f"{clr[0]:02x}{clr[1]:02x}{clr[2]:02x}22",
                              outline=f"{clr[0]:02x}{clr[1]:02x}{clr[2]:02x}44")
            
            # Center text properly
            d.text((bx + 150, 450), str(num).strip(), fill=f"#{clr[0]:02x}{clr[1]:02x}{clr[2]:02x}",
                   font=_get_font(44), anchor="mm")
            d.text((bx + 150, 510), lbl, fill="#aaaaaa",
                   font=_get_font(22), anchor="mm")
            d.text((bx + 150, 540), "", fill="#666666",
                   font=_get_font(16), anchor="mm")

        # Bottom gradient
        for x in range(W):
            r = int(tc_rgb[0] + (tc2_rgb[0] - tc_rgb[0]) * x / W)
            g = int(tc_rgb[1] + (tc2_rgb[1] - tc_rgb[1]) * x / W)
            b = int(tc_rgb[2] + (tc2_rgb[2] - tc_rgb[2]) * x / W)
            d.line([(x, H - 10), (x, H)], fill=(r, g, b))

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception as e:
        print(f"Pillow fallback failed: {e}")
        return None


# ─── Main generator with profile photo handling ──────────────────────────────

async def download_telegram_profile_photo(user_id: int, bot) -> Optional[str]:
    """
    Download user's Telegram profile photo and save to assets.
    Returns path to saved photo or None.
    """
    try:
        from telethon import types
        
        # Create assets directory if not exists
        os.makedirs("assets/profile_photos", exist_ok=True)
        
        # Get user photos
        photos = await bot.get_profile_photos(user_id, limit=1)
        
        if photos and len(photos) > 0:
            # Download the largest photo available
            photo = photos[0]
            file_path = f"assets/profile_photos/user_{user_id}.jpg"
            
            # Download the photo
            await bot.download_media(photo, file_path)
            
            if os.path.exists(file_path):
                return file_path
    except Exception as e:
        print(f"Failed to download profile photo for {user_id}: {e}")
    
    return None


def get_user_additional_info(user) -> dict:
    """
    Extract additional user info from Telegram user object.
    """
    info = {
        'country': '🌍 Unknown',
        'language': '📖 Unknown',
        'device': '💻 Unknown',
        'is_premium': False,
    }
    
    try:
        # Check if user has premium (Telegram feature)
        if hasattr(user, 'premium') and user.premium:
            info['is_premium'] = True
        
        # Try to get language code
        if hasattr(user, 'language_code') and user.language_code:
            lang_map = {
                'en': '🇬🇧 English',
                'ru': '🇷🇺 Russian',
                'es': '🇪🇸 Spanish',
                'fr': '🇫🇷 French',
                'de': '🇩🇪 German',
                'it': '🇮🇹 Italian',
                'pt': '🇵🇹 Portuguese',
                'ar': '🇸🇦 Arabic',
                'hi': '🇮🇳 Hindi',
                'zh': '🇨🇳 Chinese',
                'ja': '🇯🇵 Japanese',
                'ko': '🇰🇷 Korean',
            }
            info['language'] = lang_map.get(user.language_code, f"📖 {user.language_code.upper()}")
        
        # Other info can be added based on user's presence or custom data
        if hasattr(user, 'country') and user.country:
            info['country'] = f"🌍 {user.country}"
            
    except Exception as e:
        print(f"Failed to get additional user info: {e}")
    
    return info


def generate_welcome_card(
    first_name: str,
    username: str,
    user_id: int,
    joined: str,
    jobs: int,
    files: int,
    bytes_sent: int,
    is_admin: bool = False,
    profile_photo_path: Optional[str] = None,
    additional_info: Optional[dict] = None,
) -> Optional[bytes]:
    """
    Returns PNG bytes of the welcome card, or None if generation fails.
    """
    svg = _make_svg(
        first_name, username, user_id, joined,
        jobs, files, bytes_sent, is_admin,
        profile_photo_path, additional_info
    )

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


def cleanup_profile_photos(max_age_hours: int = 24):
    """Clean up old profile photos to save disk space."""
    try:
        import time
        photos_dir = Path("assets/profile_photos")
        if not photos_dir.exists():
            return
        
        current_time = time.time()
        for photo in photos_dir.glob("*.jpg"):
            # Remove photos older than max_age_hours
            if current_time - photo.stat().st_mtime > (max_age_hours * 3600):
                photo.unlink()
    except Exception as e:
        print(f"Failed to cleanup profile photos: {e}")      
