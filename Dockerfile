# Use official Python slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies (libmagic is required for python-magic)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for better layer caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire project (all .py files, .env, etc.)
COPY . .

# Create runtime directories (temp downloads, logs)
RUN mkdir -p temp_downloads logs

# (Optional) Create a non‑root user to run the bot (more secure)
RUN addgroup --system appgroup && adduser --system --no-create-home --ingroup appgroup appuser
RUN chown -R appuser:appgroup /app
USER appuser

# Health check (optional, requires a web server – you can remove if not needed)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import sys; sys.exit(0)" || exit 1

# Command to run the bot (change to botStart.py if that's your main file)
CMD ["python", "bot.py"]
