# Clipreel — local clip tool for YouTube & X (Twitter)

Pulls just a time-range from a YouTube video or X video/broadcast, without
downloading the full thing first. Runs entirely on your own machine.

⚠️ **Use this for personal purposes / content you have the right to download.**
Both YouTube's and X's Terms of Service prohibit downloading video outside of
their own official tools. This is built as a local, personal tool, not
something to deploy as a public website — see the note at the end of this file.

---

## What's inside

```
yt-clipper/
├── backend/
│   ├── main.py            # FastAPI server — video info + clip extraction
│   ├── requirements.txt
│   └── cookies.txt        # (you add this — see "X / Twitter setup" below)
├── frontend/
│   └── index.html         # Single-file UI, no build step needed
└── downloads/              # Clips land here temporarily (auto-deleted after 30 min)
```

## How it works

1. Pick a source (YouTube or X) and paste a link, click **Load video** →
   backend calls `yt-dlp --dump-json --skip-download` to grab the
   title/duration/thumbnail only.
2. Drag the timeline (or type timecodes) to pick in/out points.
3. Click **Download clip** → backend runs `yt-dlp --download-sections
   "*00:47:00-00:52:00" ...` so it only fetches that time range from the
   source, not the whole video.
4. The clip downloads straight to your browser.

## Setup

**Requirements:** Python 3.9+, `ffmpeg` installed and on your PATH.

```bash
# 1. Install ffmpeg if you don't have it
#    macOS:   brew install ffmpeg
#    Ubuntu:  sudo apt install ffmpeg
#    Windows: https://ffmpeg.org/download.html (add to PATH)

# 2. Install backend dependencies
cd yt-clipper/backend
pip install -r requirements.txt

# 3. Start the server
python3 -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Then open `yt-clipper/frontend/index.html` directly in your browser
(double-click it). The page talks to `http://127.0.0.1:8000` automatically.

## X / Twitter setup (cookies required)

Most video and broadcast content on X requires being logged in to view —
this isn't optional the way it is for most public YouTube videos. To let
`yt-dlp` access it on your behalf, you need to export your browser's X login
session as a cookies file:

1. Install a browser extension that exports cookies in Netscape format —
   e.g. **"Get cookies.txt LOCALLY"** (Chrome/Firefox).
2. Log into **x.com** in that browser as normal.
3. Click the extension while on x.com and export cookies for the
   `x.com` domain.
4. Save the exported file as **`backend/cookies.txt`** (exact filename,
   right next to `main.py`).
5. Restart the backend server.

Once `cookies.txt` exists, both X broadcasts (`x.com/i/broadcasts/...`) and
regular video tweets (`x.com/user/status/...`) should work the same way
YouTube does. The same cookies file is also used for YouTube requests if
present (helps with age-restricted videos), so one file covers both.

**Keep `cookies.txt` private** — it's effectively your login session. Don't
commit it to git or share the file. It's worth treating it like a password
and refreshing it (re-exporting) if it stops working, since X sessions can
expire or get invalidated.

## Notes & limits

- Clips are capped at 30 minutes and auto-deleted from `downloads/` after
  30 minutes.
- X support is less stable than YouTube's: X actively works against
  unofficial access, so things may break more often and need `yt-dlp`
  updates (`pip install -U yt-dlp`) more frequently. Some broadcasts may
  also have a limited availability window after they end.
- Age-restricted, private, or region-locked content may fail regardless of
  source — that's a platform-side restriction `yt-dlp` can't always work
  around.

## About hosting this publicly

This was deliberately built as a **local tool**, not a hosted product. If you
later want to put this on the open internet for anyone to use, know that:

- It would likely violate both YouTube's and X's Terms of Service, and
  public video-downloading sites are routinely sent takedown notices / C&Ds.
- You'd be the one holding legal/liability exposure as the operator, not
  your users.
- Sharing your personal X `cookies.txt` session with a hosted service used
  by others is a real account-security risk on top of the legal one — don't
  do this.

If your actual goal is a *product*, safer directions are: a tool scoped to
content you/creators have rights to, or a desktop app users run locally
themselves (using their own login) rather than a server you host. Happy to
help adapt this toward either of those if that's the direction you want to
take it.

