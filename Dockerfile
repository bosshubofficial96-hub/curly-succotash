FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (ffmpeg, tesseract, etc.)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    tesseract-ocr \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for better caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the bot code
COPY bot.py .
COPY config.py .
# Run the bot
CMD ["python", "bot.py"]
