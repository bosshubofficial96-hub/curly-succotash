FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg wget curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data /app/temp /app/logs /app/assets

ENV DB_PATH=/app/data/appx_bot.db
ENV TEMP_DIR=/app/temp
ENV LOG_DIR=/app/logs
ENV YTDLP_COOKIES_FILE=/app/cookies.txt

EXPOSE 8080

CMD ["python", "main.py"]
