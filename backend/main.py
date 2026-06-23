"""
YT Clipper - local backend
Fetches a single time-range slice of a YouTube or X (Twitter) video using
yt-dlp's --download-sections feature, so we never pull the full video to disk.
"""

BACKEND_VERSION = "2.2-progress-fix"

import os
import re
import sys
import uuid
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator

print(f"=" * 60, file=sys.stderr)
print(f"  yt-clipper backend starting — version {BACKEND_VERSION}", file=sys.stderr)
print(f"  loaded from: {__file__}", file=sys.stderr)
print(f"  PORT env var: {os.environ.get('PORT', 'not set, using 8000')}", file=sys.stderr)
print(f"=" * 60, file=sys.stderr)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Locally this lives one level up as a sibling of backend/ (yt-clipper/downloads).
# In Docker (Render etc.) only backend/'s own files are copied into the image,
# so there's no sibling folder to find — DOWNLOAD_DIR env var lets the
# container define its own writable path instead. Falls back to the local
# layout when that env var isn't set, so nothing changes for local runs.
DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", "")) if os.environ.get("DOWNLOAD_DIR") \
    else Path(__file__).resolve().parent.parent / "downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# How long a finished clip is kept on disk before being auto-deleted (seconds)
CLIP_TTL_SECONDS = 60 * 30  # 30 minutes

# Cookies file path — checked in order:
# 1. COOKIES_CONTENT env var (Railway) — write contents to /tmp and use that
# 2. Render's secret file location (/etc/secrets/cookies.txt)
# 3. Local path next to main.py (backend/cookies.txt) for local dev
RENDER_COOKIES = Path("/etc/secrets/cookies.txt")
LOCAL_COOKIES = Path(__file__).resolve().parent / "cookies.txt"
ENV_COOKIES = Path("/tmp/cookies_env.txt")

_cookies_content = os.environ.get("COOKIES_CONTENT", "").strip()
print(f"  cookies: COOKIES_CONTENT env var length = {len(_cookies_content)} chars", file=sys.stderr)
if _cookies_content:
    # Railway: write env var content to a writable temp path
    ENV_COOKIES.write_text(_cookies_content)
    COOKIES_FILE = ENV_COOKIES
    print(f"  cookies: loaded from COOKIES_CONTENT env var → {ENV_COOKIES}", file=sys.stderr)
    print(f"  cookies: first line = {_cookies_content.splitlines()[0][:60]}", file=sys.stderr)
elif RENDER_COOKIES.exists():
    COOKIES_FILE = RENDER_COOKIES
    print(f"  cookies: found at Render secret path {RENDER_COOKIES}", file=sys.stderr)
elif LOCAL_COOKIES.exists():
    COOKIES_FILE = LOCAL_COOKIES
    print(f"  cookies: found at local path {LOCAL_COOKIES}", file=sys.stderr)
else:
    COOKIES_FILE = LOCAL_COOKIES  # won't exist, _cookie_args handles gracefully
    print(f"  cookies: NOT FOUND — YouTube/X may hit bot-detection wall", file=sys.stderr)

YOUTUBE_URL_RE = re.compile(
    r"^(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/|m\.youtube\.com/watch\?v=)[\w\-]+",
    re.IGNORECASE,
)

X_URL_RE = re.compile(
    r"^(https?://)?(www\.)?(twitter\.com|x\.com)/"
    r"(i/(broadcasts|events)/\w+|[\w]+/status(es)?/\d+)",
    re.IGNORECASE,
)


def detect_source(url: str) -> Optional[str]:
    url = url.strip()
    if YOUTUBE_URL_RE.match(url):
        return "youtube"
    if X_URL_RE.match(url):
        return "x"
    return None

app = FastAPI(title="YT Clipper")

# Local-only tool -> permissive CORS so the simple frontend (file:// or any
# localhost port) can call it without hassle.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# job_id -> {"status": ..., "filepath": ..., "error": ..., "created": ts}
JOBS: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class VideoInfoRequest(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not detect_source(v):
            raise ValueError("That doesn't look like a YouTube or X (Twitter) video URL.")
        return v.strip()


class ClipRequest(BaseModel):
    url: str
    start: float  # seconds
    end: float    # seconds
    quality: str = "1080"  # "best", "1080", "720", "480", "360"

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not detect_source(v):
            raise ValueError("That doesn't look like a YouTube or X (Twitter) video URL.")
        return v.strip()

    @field_validator("quality")
    @classmethod
    def validate_quality(cls, v: str) -> str:
        if v not in ("best", "1080", "720", "480", "360"):
            return "1080"
        return v


def seconds_to_hhmmss(total_seconds: float) -> str:
    total_seconds = max(0, int(round(total_seconds)))
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _cookie_args(source: Optional[str]) -> list[str]:
    """Returns yt-dlp cookie args if a cookies file is available.
    The file is always writable (either /tmp/cookies_env.txt from env var,
    /tmp/cookies_working.txt copied from Render secrets, or local file)
    so yt-dlp can update session tokens without hitting read-only errors."""
    if not COOKIES_FILE.exists():
        return []

    # For Render's read-only /etc/secrets path, copy to /tmp first.
    if str(COOKIES_FILE).startswith("/etc/secrets"):
        writable_copy = Path("/tmp/cookies_working.txt")
        try:
            if not writable_copy.exists() or \
                    writable_copy.stat().st_mtime < COOKIES_FILE.stat().st_mtime:
                import shutil
                shutil.copy2(str(COOKIES_FILE), str(writable_copy))
            return ["--cookies", str(writable_copy)]
        except Exception as e:
            print(f"  [cookies] copy to /tmp failed: {e}", file=sys.stderr)

    return ["--cookies", str(COOKIES_FILE)]


# ---------------------------------------------------------------------------
# Background cleanup of old clips
# ---------------------------------------------------------------------------

def _cleanup_loop():
    while True:
        now = time.time()
        for job_id, job in list(JOBS.items()):
            created = job.get("created", now)
            if now - created > CLIP_TTL_SECONDS:
                fp = job.get("filepath")
                if fp and os.path.exists(fp):
                    try:
                        os.remove(fp)
                    except OSError:
                        pass
                JOBS.pop(job_id, None)
        time.sleep(60)


threading.Thread(target=_cleanup_loop, daemon=True).start()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/api/thumbnail")
async def proxy_thumbnail(url: str):
    """Proxy thumbnail images through backend to avoid CORS blocks."""
    import urllib.request
    import urllib.error
    from fastapi.responses import Response
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://twitter.com/",
            }
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            content_type = resp.headers.get("Content-Type", "image/jpeg")
            data = resp.read()
            return Response(content=data, media_type=content_type)
    except Exception as e:
        print(f"[thumbnail proxy] failed for {url[:80]}: {e}", file=sys.stderr)
        raise HTTPException(status_code=404, detail="Thumbnail not available.")


@app.get("/api/health")
def health():
    return {"ok": True, "version": BACKEND_VERSION}


@app.post("/api/video-info")
def video_info(payload: VideoInfoRequest):
    """Look up title/duration/thumbnail without downloading the video."""
    source = detect_source(payload.url)

    cmd = [
        "yt-dlp",
        "--dump-json",
        "--no-playlist",
        "--skip-download",
        "--extractor-args", "youtube:player_client=web",
        "--remote-components", "ejs:github",
    ]
    cmd += _cookie_args(source)
    cmd.append(payload.url)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Timed out fetching video info.")

    if result.returncode != 0:
        # Print the FULL stderr to server logs (visible in `render logs`,
        # Railway logs, journalctl, etc.) — the HTTP response only carries
        # the last line to keep it readable, but full output is often
        # needed to diagnose things like YouTube's bot-detection wall.
        print(f"[video-info] yt-dlp failed for source={source!r}:", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        msg = result.stderr.strip().splitlines()[-1] if result.stderr else "Unknown error"
        if source == "x" and not COOKIES_FILE.exists():
            msg = (
                "X (Twitter) video usually requires a logged-in session. "
                "Add a cookies.txt file next to main.py — see README. "
                f"(yt-dlp said: {msg})"
            )
        raise HTTPException(status_code=400, detail=f"Could not fetch video info: {msg}")

    import json
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Unexpected response from yt-dlp.")

    # Return the raw thumbnail URL — the frontend handles broken images
    # gracefully with a placeholder if the CDN blocks cross-origin loading.
    thumbnail = data.get("thumbnail")

    return {
        "title": data.get("title"),
        "duration": data.get("duration"),
        "thumbnail": thumbnail,
        "uploader": data.get("uploader"),
        "source": source,
    }


@app.post("/api/clip")
def create_clip(payload: ClipRequest):
    """Kick off a background job that downloads only [start, end] of the video."""
    if payload.end <= payload.start:
        raise HTTPException(status_code=400, detail="End time must be after start time.")
    if payload.end - payload.start > 60 * 30:
        raise HTTPException(status_code=400, detail="Clips longer than 30 minutes aren't supported.")

    source = detect_source(payload.url)
    if not source:
        raise HTTPException(status_code=400, detail="Unrecognized URL.")

    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {
        "status": "processing",
        "filepath": None,
        "error": None,
        "created": time.time(),
        "percent": 0,
        "stage": "starting",
    }

    thread = threading.Thread(
        target=_run_clip_job,
        args=(job_id, payload.url, payload.start, payload.end, source, payload.quality),
        daemon=True,
    )
    thread.start()

    return {"job_id": job_id}


JOB_TIMEOUT_SECONDS = 60 * 10

# Emits one parseable line per progress tick. Two signals, since not every
# source can report the same thing:
#   - "DL|<percent>|<eta>"      byte-based percent (works for regular VOD)
#   - "FR|<frag_index>|<frag_count>"  fragment count (HLS/broadcast streams,
#     where total size isn't known upfront so percent is always 0)
# Both templates fire on every progress tick; whichever fields are actually
# populated for this download will have real values, the other prints
# "NA" and gets ignored on the parsing side.
PROGRESS_TEMPLATE = "DL|%(progress._percent_str)s|%(progress._eta_str)s"
FRAGMENT_TEMPLATE = "FR|%(progress.fragment_index)s|%(progress.fragment_count)s"
POSTPROC_TEMPLATE = "postprocess:PP|%(progress._percent_str)s"


def _run_clip_job(job_id: str, url: str, start: float, end: float, source: str, quality: str = "1080"):
    out_template = str(DOWNLOAD_DIR / f"{job_id}.%(ext)s")
    section = f"*{seconds_to_hhmmss(start)}-{seconds_to_hhmmss(end)}"

    # Build format selector based on requested quality
    if quality == "best":
        fmt = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b"
    elif quality in ("1080", "720", "480", "360"):
        h = quality
        fmt = f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/b[height<={h}][ext=mp4]/b[height<={h}]"
    else:
        fmt = "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/b"

    cmd = ["yt-dlp", "--no-playlist"]
    cmd += _cookie_args(source)
    cmd += [
        "--download-sections", section,
        "--force-keyframes-at-cuts",
        "--extractor-args", "youtube:player_client=web",
        "--remote-components", "ejs:github",
        "-f", fmt,
        "--merge-output-format", "mp4",
        "--newline",
        "--progress-template", PROGRESS_TEMPLATE,
        "--progress-template", FRAGMENT_TEMPLATE,
        "--progress-template", POSTPROC_TEMPLATE,
        "-o", out_template,
        url,
    ]

    JOBS[job_id]["percent"] = 0
    JOBS[job_id]["stage"] = "downloading"

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        start_time = time.time()
        stderr_tail = []
        have_byte_percent = False  # once we see a real % once, prefer it over fragment estimates

        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue

            if line.startswith("DL|"):
                _, pct_str, eta = (line.split("|") + ["", ""])[:3]
                pct = _parse_percent(pct_str)
                if pct is not None and pct > 0:
                    have_byte_percent = True
                    JOBS[job_id]["percent"] = pct
                    JOBS[job_id]["stage"] = "downloading"
            elif line.startswith("FR|"):
                # Fallback for HLS/fragment-based streams (e.g. X broadcasts)
                # where total size — and therefore a true percent — isn't
                # knowable upfront. Use fragment_index/fragment_count instead.
                if have_byte_percent:
                    continue  # real percent already flowing, ignore fragment estimate
                _, idx_str, count_str = (line.split("|") + ["", ""])[:3]
                pct = _parse_fragment_percent(idx_str, count_str)
                if pct is not None:
                    JOBS[job_id]["percent"] = pct
                    JOBS[job_id]["stage"] = "downloading"
            elif line.startswith("PP|"):
                JOBS[job_id]["stage"] = "processing"
                JOBS[job_id]["percent"] = max(JOBS[job_id]["percent"], 95)
            else:
                stderr_tail.append(line)
                if len(stderr_tail) > 15:
                    stderr_tail.pop(0)

            if time.time() - start_time > JOB_TIMEOUT_SECONDS:
                proc.kill()
                JOBS[job_id]["status"] = "error"
                JOBS[job_id]["error"] = "Clip download timed out."
                return

        returncode = proc.wait()

        if returncode != 0:
            err = stderr_tail[-1] if stderr_tail else "yt-dlp failed"
            if source == "x" and not COOKIES_FILE.exists():
                err = (
                    "X (Twitter) video usually requires a logged-in session. "
                    f"Add a cookies.txt file next to main.py — see README. (yt-dlp said: {err})"
                )
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = err
            return

        produced = list(DOWNLOAD_DIR.glob(f"{job_id}.*"))
        if not produced:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = "No output file was produced."
            return

        JOBS[job_id]["percent"] = 100
        JOBS[job_id]["status"] = "done"
        JOBS[job_id]["filepath"] = str(produced[0])

    except Exception as e:  # noqa: BLE001
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["error"] = str(e)


def _parse_percent(pct_str: str) -> Optional[float]:
    """yt-dlp gives us strings like ' 42.3%' (with ANSI codes stripped by --newline)."""
    pct_str = pct_str.strip().rstrip("%")
    # progress template can still leave color codes in rare cases; strip non-numeric chars
    pct_str = re.sub(r"[^\d.]", "", pct_str)
    if not pct_str:
        return None
    try:
        return round(float(pct_str), 1)
    except ValueError:
        return None


def _parse_fragment_percent(idx_str: str, count_str: str) -> Optional[float]:
    """Fallback progress for HLS/fragment downloads: index / count * 100.
    yt-dlp prints 'NA' for fields that aren't populated for this download
    type, so anything non-numeric just means 'no signal yet'."""
    idx_str, count_str = idx_str.strip(), count_str.strip()
    if not idx_str.isdigit() or not count_str.isdigit():
        return None
    count = int(count_str)
    if count <= 0:
        return None
    idx = int(idx_str)
    # Cap at 94 so it never visually collides with/exceeds the "processing"
    # stage marker (95) that kicks in once ffmpeg starts merging.
    return round(min(94.0, (idx / count) * 100), 1)


@app.get("/api/clip/{job_id}/status")
def clip_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown job id.")
    return {
        "status": job["status"],
        "error": job["error"],
        "percent": job.get("percent", 0),
        "stage": job.get("stage", "starting"),
    }


@app.get("/api/clip/{job_id}/download")
def clip_download(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown job id.")
    if job["status"] != "done" or not job["filepath"]:
        raise HTTPException(status_code=409, detail="Clip isn't ready yet.")
    if not os.path.exists(job["filepath"]):
        raise HTTPException(status_code=410, detail="Clip has expired. Please create it again.")

    filename = f"clip-{job_id}.mp4"
    return FileResponse(job["filepath"], media_type="video/mp4", filename=filename)
