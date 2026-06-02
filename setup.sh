#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  AppX Uploader Bot — Setup Script
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

echo "╔══════════════════════════════════════════════════════╗"
echo "║        AppX Uploader Bot — Setup Script              ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── Python version check ──────────────────────────────────────
PYTHON=$(command -v python3 || command -v python || echo "")
if [ -z "$PYTHON" ]; then
    echo "❌  Python 3 not found. Please install Python 3.10+."
    exit 1
fi

PY_VER=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "✅  Python $PY_VER found"

# ── Virtual environment ───────────────────────────────────────
if [ ! -d ".venv" ]; then
    echo "📦  Creating virtual environment…"
    $PYTHON -m venv .venv
fi

source .venv/bin/activate
echo "✅  Virtual environment activated"

# ── Install dependencies ──────────────────────────────────────
echo ""
echo "📥  Installing dependencies…"
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
echo "✅  Dependencies installed"

# ── ffmpeg check (needed by yt-dlp for stream merging) ────────
if command -v ffmpeg &>/dev/null; then
    echo "✅  ffmpeg found: $(ffmpeg -version 2>&1 | head -1 | cut -d' ' -f1-3)"
else
    echo "⚠️   ffmpeg not found. HLS/DASH stream merging may not work."
    echo "    Install with:  sudo apt install ffmpeg   (Debian/Ubuntu)"
    echo "                   brew install ffmpeg        (macOS)"
fi

# ── .env setup ────────────────────────────────────────────────
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo "⚠️   Created .env from .env.example"
    echo "    Please edit .env and set your BOT_TOKEN and ADMIN_IDS"
else
    echo "✅  .env already exists"
fi

# ── Directories ───────────────────────────────────────────────
mkdir -p temp logs
echo "✅  Directories created (temp/, logs/)"

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  Setup complete!                                     ║"
echo "║                                                      ║"
echo "║  Next steps:                                         ║"
echo "║  1. Edit .env and add your BOT_TOKEN and ADMIN_IDS   ║"
echo "║  2. Run: source .venv/bin/activate                   ║"
echo "║  3. Run: python main.py                              ║"
echo "╚══════════════════════════════════════════════════════╝"
