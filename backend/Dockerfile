# Slim Python base — small image, fast Render builds/deploys.
FROM python:3.12-slim

# ffmpeg: needed by yt-dlp to trim/merge video segments.
# nodejs: needed by yt-dlp to solve YouTube's n-parameter challenge
# (anti-bot obfuscation layer) — without it, only images can be downloaded.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Container-local writable folder for in-progress/finished clips.
ENV DOWNLOAD_DIR=/app/downloads
RUN mkdir -p /app/downloads

# Render injects the real port via $PORT at runtime.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
