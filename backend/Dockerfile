# Slim Python base — small image, fast Render builds/deploys.
FROM python:3.12-slim

# ffmpeg isn't a pip package — yt-dlp shells out to the real binary for
# trimming/merging, so it has to be installed at the OS level. This is
# the actual fix for "ffmpeg: not found" on Render's native Python env.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Container-local writable folder for in-progress/finished clips — the app's
# DOWNLOAD_DIR env var override (see main.py) points here instead of trying
# to use the local sibling-folder layout, which doesn't exist in the image.
ENV DOWNLOAD_DIR=/app/downloads
RUN mkdir -p /app/downloads

# Render injects the real port to bind to via $PORT at runtime — it varies
# per deploy, so it can't be hardcoded. Using sh -c so the shell expands
# the environment variable (CMD's exec form does NOT expand env vars).
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
