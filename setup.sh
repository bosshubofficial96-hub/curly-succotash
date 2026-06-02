#!/usr/bin/env bash
set -e

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║         AppX Uploader Bot v3 — Setup Script              ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# Python version check
PY=$(python3 --version 2>/dev/null | awk '{print $2}' || echo "0")
MAJOR=$(echo "$PY" | cut -d. -f1)
MINOR=$(echo "$PY" | cut -d. -f2)
if [ "$MAJOR" -lt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 10 ]; }; then
  echo "❌ Python 3.10+ is required. Found: $PY"
  exit 1
fi
echo "✅ Python $PY"

# ffmpeg check (needed for yt-dlp HLS/DASH merging)
if command -v ffmpeg &>/dev/null; then
  echo "✅ ffmpeg: $(ffmpeg -version 2>&1 | head -1)"
else
  echo "⚠️  ffmpeg not found — HLS/DASH stream merging will fail."
  echo "   Install it: sudo apt-get install ffmpeg"
fi

# .env setup
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo ""
  echo "📝 Created .env from .env.example"
  echo "   👉 Edit .env and set BOT_TOKEN and ADMIN_IDS before running!"
  echo ""
else
  echo "✅ .env already exists"
fi

# Virtual environment
if [ ! -d ".venv" ]; then
  echo ""
  echo "🐍 Creating virtual environment..."
  python3 -m venv .venv
fi
echo "✅ Virtual environment ready"

# Activate and install
echo ""
echo "📦 Installing Python dependencies..."
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

echo ""
echo "📁 Creating required directories..."
mkdir -p temp logs assets

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║                  ✅ Setup Complete!                       ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  1. Edit .env — set BOT_TOKEN and ADMIN_IDS              ║"
echo "║  2. (Optional) Add AppX credentials to .env              ║"
echo "║  3. Run the bot:                                          ║"
echo "║     source .venv/bin/activate && python main.py           ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
