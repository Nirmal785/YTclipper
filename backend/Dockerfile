# Slim Python base — small image, fast Render builds/deploys.
FROM python:3.12-slim

# ffmpeg: needed by yt-dlp to trim/merge video segments.
# curl + unzip: needed to install deno (better JS runtime support for
# yt-dlp's n-challenge solver than nodejs on debian-slim).
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl unzip && \
    rm -rf /var/lib/apt/lists/*

# Install deno — yt-dlp recommends it over node for challenge solving.
RUN curl -fsSL https://deno.land/install.sh | sh
ENV DENO_INSTALL="/root/.deno"
ENV PATH="${DENO_INSTALL}/bin:${PATH}"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Pre-warm yt-dlp's JS extractor cache so first request isn't slow.
RUN yt-dlp --update-to stable 2>/dev/null || true

# Container-local writable folder for in-progress/finished clips.
ENV DOWNLOAD_DIR=/app/downloads
RUN mkdir -p /app/downloads

# Render/Railway inject the real port via $PORT at runtime.
# Using /bin/sh -c ensures the variable expands correctly.
CMD ["/bin/sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
