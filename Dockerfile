FROM python:3.12-slim

LABEL maintainer="cesar@adroiteck.com"
LABEL app="kingshot-discord-bot"

# Create non-root user
RUN groupadd -r botuser && useradd -r -g botuser -d /app -s /sbin/nologin botuser

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY bot.py .
COPY config.json .

# Create data directory for persistent storage
# OKD runs with random UID - ensure all files are group-readable/executable
RUN mkdir -p /app/data && \
    chown -R botuser:0 /app && \
    chmod -R g=u /app

USER botuser

# Health check - verify Python can import discord
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD python -c "import discord; print('ok')" || exit 1

CMD ["python", "-u", "bot.py"]
