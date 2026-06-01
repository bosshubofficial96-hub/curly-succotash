#!/usr/bin/env python3
"""
🔥 APPX UPLOADER BOT – Advanced Telegram Bot (2026 Edition)
- Single-file comprehensive implementation.
- Fully asynchronous design using python-telegram-bot (v20.x+).
- Async subprocesses for video watermarking (avoids blocking the main thread loop).
- Unified session management with automatic cleanups for inactive sessions.
- Dynamic error handling and fail-safe file deletions.
- Dual-mode OCR (PDF text scraping & pytesseract image extraction).
"""

import asyncio
import logging
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import aiohttp
import yt_dlp
from PIL import Image
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ConversationHandler, ContextTypes, Defaults
)

# ================== CONFIGURATION ==================
BOT_TOKEN = "8828772252:AAEnP5IGOS5G5MBp2IkH1Cw1JU9l-1A3HDE"          # Get from @BotFather
ADMIN_USER_IDS = [123456789]               # Replace with your Telegram user ID(s)
API_COURSES_URL = None                     # Optional remote API endpoint
API_HEADERS = {}                           # Optional Authorization Headers
SESSION_TIMEOUT_MINUTES = 60               # Clear inactive session data

# Static fallback course catalog
STATIC_COURSES = [
    {"id": 2421305, "name": "Python Programming", "price": 499},
    {"id": 2421403, "name": "Digital Communication", "price": 499},
    {"id": 2421402, "name": "Microcontroller and its Applications", "price": 499},
    {"id": 2421401, "name": "Linear Integrated Circuit", "price": 499},
    {"id": 2421405, "name": "Principles of Electronic Communication", "price": 499},
    {"id": 2421406, "name": "Digital Electronics", "price": 499},
    {"id": 2421407, "name": "Measuring Instruments and Sensors", "price": 499},
    {"id": 2421408, "name": "Analog Electronics", "price": 499},
]

# Resolution/Quality selection options
QUALITY_OPTIONS = {
    "best": "🎬 Best Quality",
    "1080p": "📺 1080p Full HD",
    "720p": "📺 720p HD",
    "480p": "📺 480p SD",
    "360p": "📺 360p Low",
    "audio": "🎵 Audio Only (MP3)"
}

# Directories Setup
BASE_DIR = Path("bot_data")
DOWNLOAD_DIR = BASE_DIR / "downloads"
OUTPUT_DIR = BASE_DIR / "output"
LOG_DIR = BASE_DIR / "logs"
START_IMAGE = BASE_DIR / "start_image.jpg"

for directory in [DOWNLOAD_DIR, OUTPUT_DIR, LOG_DIR]:
    directory.mkdir(parents=True, exist_ok=True)

# Logger Configuration
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.FileHandler(LOG_DIR / "bot.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# State definitions for ConversationHandlers
WAITING_WATERMARK, WAITING_EXTRACT, WAITING_BROADCAST = range(3)

# Global State Stores
user_data: Dict[int, Any] = {}
user_last_active: Dict[int, datetime] = {}

# ================== HELPER FUNCTIONS ==================
def sanitize_filename(text: str, max_len: int = 80) -> str:
    """Removes dangerous characters and limits the length of filenames."""
    text = re.sub(r'[^\w\s\-_.()\[\]]', '', text)
    text = text.replace(' ', '_')
    text = re.sub(r'[_]+', '_', text).strip('._')
    return text[:max_len] or "download_file"

async def fetch_courses_from_api() -> List[Dict]:
    """Retrieves course list from remote API if configured, otherwise falls back to static catalog."""
    if not API_COURSES_URL:
        return STATIC_COURSES
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(API_COURSES_URL, headers=API_HEADERS, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("courses", STATIC_COURSES)
                logger.warning(f"Remote API returned status {resp.status}. Using static catalog fallback.")
                return STATIC_COURSES
    except Exception as e:
        logger.error(f"Failed to query remote courses API: {e}. Defaulting to static assets.")
        return STATIC_COURSES

async def download_direct_file(url: str, filepath: Path) -> Tuple[bool, str]:
    """Asynchronously streams direct file downloads."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        timeout = aiohttp.ClientTimeout(total=600)
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=timeout) as resp:
                resp.raise_for_status()
                with open(filepath, 'wb') as f:
                    async for chunk in resp.content.iter_chunked(8192):
                        f.write(chunk)
        return True, str(filepath)
    except Exception as e:
        return False, str(e)

def download_with_ytdlp(url: str, quality: str, output_template: Path) -> Tuple[bool, str, str]:
    """Blocking yt-dlp task run safely inside a worker pool thread."""
    try:
        if quality == 'audio':
            fmt = 'bestaudio/best'
            postprocessors = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]
        elif quality == 'best':
            fmt = 'bestvideo+bestaudio/best'
            postprocessors = []
        else:
            height = quality.replace('p', '')
            fmt = f'bestvideo[height<={height}]+bestaudio/best[height<={height}]/best'
            postprocessors = []

        ydl_opts = {
            'outtmpl': str(output_template.with_suffix('')) + '.%(ext)s',
            'format': fmt,
            'postprocessors': postprocessors,
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': True,
            'socket_timeout': 30,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                return False, "Failed to extract streaming info (potential DRM layer/protection)", ""
            
            filename = ydl.prepare_filename(info)
            if quality == 'audio':
                filename = filename.rsplit('.', 1)[0] + '.mp3'
            elif not Path(filename).exists():
                # Handling variations in extension matching
                base_p = str(output_template.with_suffix(''))
                matched_files = list(DOWNLOAD_DIR.glob(f"{Path(base_p).name}.*"))
                if matched_files:
                    filename = str(matched_files[0])
            title = info.get('title', 'video_element')
            return True, filename, title
    except Exception as e:
        logger.error(f"yt-dlp core error: {e}")
        return False, str(e), ''

async def add_watermark_async(input_path: Path, output_path: Path, text: str) -> Tuple[bool, str]:
    """Overlays text watermarks on videos using non-blocking asynchronous FFmpeg processes."""
    safe_text = text.replace("'", r"\'").replace(":", r"\:")
    cmd = [
        'ffmpeg', '-i', str(input_path), '-vf',
        f"drawtext=text='{safe_text}':x=w-tw-15:y=h-th-15:fontsize=20:fontcolor=white:alpha=0.6",
        '-c:a', 'copy', str(output_path), '-y'
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        if process.returncode == 0:
            return True, str(output_path)
        return False, f"FFmpeg failed with return code {process.returncode}. Error: {stderr.decode()}"
    except Exception as e:
        return False, str(e)

def extract_text_from_file(filepath: Path) -> Tuple[bool, str]:
    """Extracts text contents from PDF documents or image layouts via OCR processes."""
    ext = filepath.suffix.lower()
    try:
        if ext == '.pdf':
            try:
                import PyPDF2
                text = []
                with open(filepath, 'rb') as f:
                    reader = PyPDF2.PdfReader(f)
                    for page in reader.pages:
                        extracted = page.extract_text()
                        if extracted:
                            text.append(extracted)
                return True, '\n\n'.join(text) if text else "No text could be extracted from PDF elements."
            except ImportError:
                return False, "PyPDF2 is not installed on the system."
        elif ext in ['.jpg', '.jpeg', '.png', '.bmp', '.tiff']:
            try:
                import pytesseract
                img = Image.open(filepath)
                text = pytesseract.image_to_string(img)
                return True, text if text.strip() else "No clear OCR text elements matched."
            except ImportError:
                return False, "Tesseract OCR modules not configured correctly on this machine."
        return False, "Unsupported file format format configuration."
    except Exception as e:
        return False, str(e)

def parse_txt_file(content: str) -> List[Dict]:
    """Parses link documents containing Title:URL pairs."""
    items = []
    for line in content.split('\n'):
        line = line.strip()
        if not line:
            continue
        match = re.search(r'(.*?)(https?://[^\s]+)', line)
        if not match:
            continue
        title = match.group(1).rstrip(':').strip()
        url = match.group(2).strip()
        url_lower = url.lower()
        if '.pdf' in url_lower:
            file_type = 'pdf'
        elif any(ext in url_lower for ext in ['.mkv', '.mp4', '.avi', '.mov', '.webm', '.m3u8']):
            file_type = 'video'
        elif any(ext in url_lower for ext in ['.jpg', '.jpeg', '.png', '.gif']):
            file_type = 'image'
        else:
            file_type = 'link'
        items.append({
            'title': title,
            'url': url,
            'type': file_type,
            'filename': sanitize_filename(title) + ('.pdf' if file_type=='pdf' else '.mp4' if file_type in ['video','link'] else '.jpg')
        })
    return items

def group_into_lessons(items: List[Dict]) -> List[Dict]:
    """Combines materials matching the same semantic parent name together."""
    lessons = {}
    for item in items:
        base = re.sub(r'\s+[\d]+\.?\s*AE$', '', item['title'])
        base = re.sub(r'\s+[\d]+$', '', base)
        base = re.sub(r'\[.*?\]', '', base).strip()
        if not base:
            base = item['title']
        if base not in lessons:
            lessons[base] = {'name': base, 'pdf': None, 'video': None, 'other': []}
        if item['type'] == 'pdf':
            lessons[base]['pdf'] = item
        elif item['type'] == 'video' or item['type'] == 'link':
            lessons[base]['video'] = item
        else:
            lessons[base]['other'].append(item)
    return [v for v in lessons.values() if v['pdf'] or v['video'] or v['other']]

async def update_activity(user_id: int):
    """Updates user interaction timestamp and clears expired session memory."""
    user_last_active[user_id] = datetime.now()
    if len(user_last_active) % 15 == 0:
        now = datetime.now()
        expired = [uid for uid, last in user_last_active.items() if now - last > timedelta(minutes=SESSION_TIMEOUT_MINUTES)]
        for uid in expired:
            user_data.pop(uid, None)
            user_last_active.pop(uid, None)
            logger.info(f"Cleaned expired memory footprint for inactive user {uid}")

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS

# ================== ADMIN MODULE ==================
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Access denied.")
        return
    text = (
        "*🔧 Administrator Dashboard*\n\n"
        "/broadcast – Push announcement dispatch\n"
        "/stats – Query memory storage matrices\n"
        "/update_courses – Direct dynamic catalog reload\n"
        "/logs – Retreive latest log records"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await update.message.reply_text("Provide broadcast text, image, or document. Send /cancel to abort.")
    return WAITING_BROADCAST

async def receive_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    recipients = set(user_last_active.keys()) | set(user_data.keys())
    if not recipients:
        await update.message.reply_text("No active users detected in system record registries.")
        return ConversationHandler.END
    
    success, failure = 0, 0
    for uid in recipients:
        try:
            if update.message.text:
                await context.bot.send_message(uid, update.message.text)
            elif update.message.photo:
                await context.bot.send_photo(uid, update.message.photo[-1].file_id, caption=update.message.caption)
            elif update.message.document:
                await context.bot.send_document(uid, update.message.document.file_id, caption=update.message.caption)
            success += 1
        except Exception:
            failure += 1
        await asyncio.sleep(0.05)
    await update.message.reply_text(f"📢 Broadcast Finished.\nSuccessful deliveries: {success}\nFailed deliveries: {failure}")
    return ConversationHandler.END

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await update.message.reply_text(
        f"📊 *System Node Stats*\n"
        f"Active Session Nodes: {len(user_last_active)}\n"
        f"Stored Context Objects: {len(user_data)}"
    )

async def update_courses_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    global STATIC_COURSES
    STATIC_COURSES = await fetch_courses_from_api()
    await update.message.reply_text(f"✅ Dynamic catalog sync completed. Loaded objects: {len(STATIC_COURSES)}")

async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    log_file = LOG_DIR / "bot.log"
    if not log_file.exists():
        await update.message.reply_text("No log records located inside target paths.")
        return
    with open(log_file, 'r') as f:
        lines = f.readlines()[-35:]
    await update.message.reply_text(f"```\n{''.join(lines)}\n```", parse_mode=ParseMode.MARKDOWN)

# ================== INTERFACE LOGIC ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update_activity(update.effective_user.id)
    text = (
        f"🔥 *Welcome {update.effective_user.first_name}!*\n\n"
        "This uploader bot manages media conversions and dynamic download buffers.\n\n"
        "• /courses – Fetch curriculum selection catalogs\n"
        "• /quality – Toggle media quality levels\n"
        "• /watermark – Apply tracking text overlays on videos\n"
        "• /extract – Upload PDF/images to convert to text format"
    )
    if START_IMAGE.exists():
        with open(START_IMAGE, 'rb') as f:
            await update.message.reply_photo(photo=InputFile(f), caption=text, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def courses_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update_activity(update.effective_user.id)
    page = context.user_data.get('course_page', 0)
    courses = await fetch_courses_from_api()
    per_page = 8
    total_pages = (len(courses) + per_page - 1) // per_page
    
    if page < 0: page = 0
    if page >= total_pages: page = max(0, total_pages - 1)
    context.user_data['course_page'] = page

    start_idx = page * per_page
    page_courses = courses[start_idx:start_idx + per_page]

    keyboard = []
    for course in page_courses:
        c_id = course['id'] if course.get('id') else course['name']
        keyboard.append([InlineKeyboardButton(f"📘 {course['name'][:45]}", callback_data=f"course_{c_id}")])
    
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("◀️ Prev", callback_data="course_prev"))
    if (start_idx + per_page) < len(courses): nav.append(InlineKeyboardButton("Next ▶️", callback_data="course_next"))
    if nav: keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("❌ Close Layout", callback_data="cancel_menu")])
    
    msg_text = f"📚 *Interactive Selection Directories* – Page {page+1}/{total_pages}\nSelect item framework:"
    if update.callback_query:
        await update.callback_query.edit_message_text(msg_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(msg_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update_activity(user_id)
    if not update.message.document.file_name.endswith('.txt'):
        await update.message.reply_text("⚠️ Drop structural link arrays utilizing `.txt` layouts.")
        return
        
    status = await update.message.reply_text("📥 Scannning structural text entries...")
    try:
        t_file = await context.bot.get_file(update.message.document.file_id)
        path = DOWNLOAD_DIR / f"{user_id}_manifest.txt"
        await t_file.download_to_drive(path)
        
        content = path.read_text(encoding='utf-8', errors='ignore')
        if path.exists(): path.unlink()
        
        items = parse_txt_file(content)
        if not items:
            await status.edit_text("❌ No structured links match expected configurations.")
            return
            
        user_data.setdefault(user_id, {})
        user_data[user_id].update({'lessons': group_into_lessons(items), 'page': 0})
        await status.delete()
        await show_lessons_page(update, context, user_id)
    except Exception as e:
        await status.edit_text(f"❌ Structural ingest failed: {e}")

async def show_lessons_page(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    data = user_data.setdefault(user_id, {'lessons': [], 'page': 0, 'quality': 'best', 'watermark': None})
    lessons = data['lessons']
    if not lessons:
        msg = "No parsing configurations matched contents in this document."
        if update.callback_query: await update.callback_query.message.reply_text(msg)
        else: await update.message.reply_text(msg)
        return

    per_page = 8
    total_pages = (len(lessons) + per_page - 1) // per_page
    page = max(0, min(data['page'], total_pages - 1))
    data['page'] = page
    
    start_pos = page * per_page
    page_items = lessons[start_pos:start_pos + per_page]
    
    keyboard = []
    for i, item in enumerate(page_items):
        icons = f"{'📄' if item['pdf'] else ''}{'🎥' if item['video'] else ''}" or '📎'
        keyboard.append([InlineKeyboardButton(f"{icons} {item['name'][:40]}", callback_data=f"lesson_{start_pos+i}")])
        
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("◀️ Prev", callback_data="prev_page"))
    if (start_pos + per_page) < len(lessons): nav.append(InlineKeyboardButton("Next ▶️", callback_data="next_page"))
    if nav: keyboard.append(nav)
    
    keyboard.append([InlineKeyboardButton("📥 Download All Course Contents", callback_data="download_all")])
    keyboard.append([InlineKeyboardButton(f"⚙️ Quality Profile: {QUALITY_OPTIONS.get(data['quality'])}", callback_data="change_quality")])
    
    text = f"📚 *Extracted Course Blocks* – Page {page+1}/{total_pages}\nSelect structural module:"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

async def download_single_file(update: Update, context: ContextTypes.DEFAULT_TYPE, item: Dict, quality: str, watermark_text: Optional[str]):
    user_id = update.effective_user.id
    status = await context.bot.send_message(chat_id=user_id, text=f"⚡ Processing extraction layer: {item['title'][:50]}...")
    try:
        if item['type'] in ['pdf', 'image']:
            filepath = DOWNLOAD_DIR / f"{sanitize_filename(item['title'])}.{'pdf' if item['type']=='pdf' else 'jpg'}"
            ok, err = await download_direct_file(item['url'], filepath)
            if ok and filepath.exists():
                with open(filepath, 'rb') as f:
                    if item['type'] == 'pdf': await context.bot.send_document(user_id, f, filename=filepath.name)
                    else: await context.bot.send_photo(user_id, f)
                filepath.unlink()
                await status.delete()
            else:
                await status.edit_text(f"❌ Extraction interface error: {err}")
        else:
            out_tmpl = DOWNLOAD_DIR / sanitize_filename(item['title'])
            loop = asyncio.get_running_loop()
            ok, res_path, v_title = await loop.run_in_executor(None, download_with_ytdlp, item['url'], quality, out_tmpl)
            
            if ok and Path(res_path).exists():
                final_file = Path(res_path)
                if watermark_text and quality != 'audio' and final_file.suffix.lower() == '.mp4':
                    await status.edit_text("🎨 Appending watermark layer overlays...")
                    wm_out = OUTPUT_DIR / f"wm_{final_file.name}"
                    w_ok, w_err = await add_watermark_async(final_file, wm_out, watermark_text)
                    if final_file.exists(): final_file.unlink()
                    if w_ok: final_file = wm_out
                
                with open(final_file, 'rb') as f:
                    if quality == 'audio': await context.bot.send_audio(user_id, f, title=v_title)
                    elif final_file.stat().st_size / (1024*1024) > 50: await context.bot.send_document(user_id, f, filename=final_file.name)
                    else: await context.bot.send_video(user_id, f, supports_streaming=True)
                
                if final_file.exists(): final_file.unlink()
                await status.delete()
            else:
                await status.edit_text(f"❌ Connection pipeline failure: {res_path}")
    except Exception as e:
        await status.edit_text(f"❌ Internal process loop exception error: {e}")

# ================== ROUTING CALLBACKS ==================
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    await update_activity(user_id)

    if data == "course_prev":
        context.user_data['course_page'] = context.user_data.get('course_page', 0) - 1
        await courses_command(update, context)
    elif data == "course_next":
        context.user_data['course_page'] = context.user_data.get('course_page', 0) + 1
        await courses_command(update, context)
    elif data.startswith("course_"):
        c_target = data.split("_", 1)[1]
        user_data.setdefault(user_id, {})['selected_course'] = c_target
        await query.edit_message_text("✅ Curriculum matrix target locked. Provide dynamic document link arrays (`.txt`) now.")
    elif data == "prev_page":
        user_data[user_id]['page'] -= 1
        await show_lessons_page(update, context, user_id)
    elif data == "next_page":
        user_data[user_id]['page'] += 1
        await show_lessons_page(update, context, user_id)
    elif data == "back_to_list":
        await show_lessons_page(update, context, user_id)
    elif data == "change_quality":
        kb = [[InlineKeyboardButton(v, callback_data=f"setq_{k}")] for k, v in QUALITY_OPTIONS.items()]
        await query.edit_message_text("Select target dynamic terminal resolution depth:", reply_markup=InlineKeyboardMarkup(kb))
    elif data.startswith("setq_"):
        user_data.setdefault(user_id, {})['quality'] = data.split("_")[1]
        await show_lessons_page(update, context, user_id)
    elif data == "cancel_menu":
        await query.edit_message_text("Display structures dismissed.")
    elif data.startswith("lesson_"):
        idx = int(data.split("_")[1])
        lesson = user_data[user_id]['lessons'][idx]
        kb = []
        if lesson['pdf']: kb.append([InlineKeyboardButton("📄 Pull Documentation PDF", callback_data=f"dl_pdf_{idx}")])
        if lesson['video']: kb.append([InlineKeyboardButton("🎥 Extract Video Stream", callback_data=f"dl_vid_{idx}")])
        kb.append([InlineKeyboardButton("◀️ Matrix Root", callback_data="back_to_list")])
        await query.edit_message_text(f"📂 Selected Element: *{lesson['name']}*", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    elif data.startswith("dl_pdf_"):
        await download_single_file(update, context, user_data[user_id]['lessons'][int(data.split("_")[2])]['pdf'], user_data[user_id].get('quality', 'best'), user_data[user_id].get('watermark'))
    elif data.startswith("dl_vid_"):
        await download_single_file(update, context, user_data[user_id]['lessons'][int(data.split("_")[2])]['video'], user_data[user_id].get('quality', 'best'), user_data[user_id].get('watermark'))
    elif data == "download_all":
        await query.edit_message_text("⏳ Enqueuing files into batch download pipeline, please wait...")
        for item in user_data[user_id]['lessons']:
            if item['pdf']: await download_single_file(update, context, item['pdf'], user_data[user_id].get('quality'), user_data[user_id].get('watermark'))
            if item['video']: await download_single_file(update, context, item['video'], user_data[user_id].get('quality'), user_data[user_id].get('watermark'))
        await context.bot.send_message(user_id, "✅ Done! All queued downloads in batch have completed successfully.")

async def watermark_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Please enter your custom watermark text string (use /cancel to abort):")
    return WAITING_WATERMARK

async def receive_watermark(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data.setdefault(update.effective_user.id, {})['watermark'] = update.message.text.strip()
    await update.message.reply_text(f"✅ Text tracking configuration added successfully: `{update.message.text}`")
    return ConversationHandler.END

async def extract_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Please send your image/PDF file to convert to text.")
    return WAITING_EXTRACT

async def receive_extract_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    document = update.message.document
    if not document:
        await update.message.reply_text("⚠️ Process state canceled. Please upload an actual document format.")
        return ConversationHandler.END
        
    status = await update.message.reply_text("📝 Running system parsing layers OCR converter...")
    try:
        t_file = await context.bot.get_file(document.file_id)
        path = DOWNLOAD_DIR / f"ocr_{user_id}_{document.file_name}"
        await t_file.download_to_drive(path)
        
        ok, res = extract_text_from_file(path)
        if path.exists(): path.unlink()
        
        if ok:
            await status.delete()
            # Send in chunks of 4000 characters (telegram limits)
            for i in range(0, len(res), 4000):
                await update.message.reply_text(res[i:i+4000])
        else:
            await status.edit_text(f"❌ Failed to extract content: {res}")
    except Exception as e:
        await status.edit_text(f"❌ Conversion interrupted: {e}")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Process states reset to operational routing rules.")
    return ConversationHandler.END

# ================== INITIALIZATION ENTRYPOINT ==================
def main():
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        sys.exit("❌ Error: Valid configuration profiles needed for initialization.")
        
    defaults = Defaults(parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
    app = Application.builder().token(BOT_TOKEN).defaults(defaults).build()

    # Dynamic conversation states bindings
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("watermark", watermark_command)],
        states={WAITING_WATERMARK: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_watermark)]},
        fallbacks=[CommandHandler("cancel", cancel)]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("extract", extract_command)],
        states={WAITING_EXTRACT: [MessageHandler(filters.Document.ALL, receive_extract_file)]},
        fallbacks=[CommandHandler("cancel", cancel)]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_command)],
        states={WAITING_BROADCAST: [MessageHandler(filters.ALL, receive_broadcast)]},
        fallbacks=[CommandHandler("cancel", cancel)]
    ))

    # User command mappings
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("courses", courses_command))
    
    # Admin configurations
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("update_courses", update_courses_command))
    app.add_handler(CommandHandler("logs", logs_command))
    
    # Message fallback integrations
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(button_callback))

    logger.info("🔥 Core systems initialized. Threading pools are up and active.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
