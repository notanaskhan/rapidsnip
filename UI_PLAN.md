# Clipper UI — plan

Local-first web app on the Mac. Decided 2026-11-18: local first (Vercel later, optional); live clipping first, VOD/link clipper second.

## Architecture

```
Mac
├─ Python API  (FastAPI + uvicorn, :8000)   `uv run python -m clipper serve`
│   ├─ Supervisor (existing): live watcher, recorders, chat, detector, worker
│   ├─ Link jobs: yt-dlp download -> chat replay / "most replayed" / loudness -> candidate moments -> worker
│   ├─ SQLite  data/clipper.db: streamers, clips (+ review status), link jobs
│   └─ /media -> clips/ on disk (range requests, so videos seek and download)
└─ Next.js UI  (TypeScript, React, plain CSS modules, :3210)
    └─ /api and /media proxied to :8000, so the browser sees one origin
```

- One process owns the recorders. The API runs the supervisor in its own event loop; `--no-live` starts the API without recorders (for development or VOD-only use).
- `config.yaml` keeps global settings (thresholds, layout, captions). Streamers move to the database; the channels in `config.yaml` are imported once on first start.
- Permission rule stays: a streamer or link job without a permission note is never recorded or clipped.
- The UI polls `/api/status` every 2 s (no websockets needed at this scale).
- Phone on the same Wi-Fi can open `http://<mac-name>.local:3210`.

## Pages

| Page | What it does |
| --- | --- |
| **Live** (home) | Channel cards: live/offline, warm-up, buffer, chat score vs baseline (sparkline), last trigger, clips; queue and chat status; newest clips strip |
| **Clips** | Grid of clips, filter by streamer / status (new, approved, posted, rejected) / source (live, link); player with vertical/horizontal toggle; edit title + description; copy; download vertical/horizontal/both; approve / reject / mark posted |
| **Streamers** | Add/edit/remove: platform (Twitch now; YouTube and Kick shown as "coming"), login, display name, permission note (required), credit URL, enabled; per-streamer threshold and chat-delay overrides; layout calibration (grab frame, drag facecam and gameplay boxes, preview) |
| **Links** | Paste a YouTube / Twitch VOD / other yt-dlp URL, pick the streamer (permission), get candidate moments on a timeline (chat spikes, most-replayed peaks, loud moments), render the ones you pick or set your own start/end |
| **Settings** | Detector thresholds, clip lengths, captions on/off, layout on/off, disk use |

## Build order

1. **API core**: FastAPI app, SQLite, streamers CRUD, clips index from `meta.json` + review status, status endpoint, media serving. Supervisor reads streamers from the DB and starts/stops channels when they change.
2. **UI core**: Next.js app, layout + nav, Live page, Clips page (review, edit, copy, download), Streamers page (without calibration).
3. **Switch over**: run the live app through `clipper serve`; CLI stays available.
4. **Link clipper**: yt-dlp download, Twitch VOD chat replay + YouTube live-chat replay through `chat-downloader`, YouTube "most replayed" heatmap, audio loudness fallback; candidate timeline; manual in/out points.
5. **Calibration UI**, **Settings** page.
6. Later: YouTube Live and Kick live recording, Vercel deployment via tunnel, LLM titles.
