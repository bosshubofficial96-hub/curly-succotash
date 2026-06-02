FROM python:3.11-slim

RUN apt-get update && apt-get install -y libmagic1 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Explicitly copy every required Python file
COPY bot.py config.py database.py downloader.py processor.py handlers.py admin.py gdrive.py utils.py logging_config.py ./

# Copy .env if it exists
COPY .env* ./

RUN mkdir -p temp_downloads logs

CMD ["python", "bot.py"]
