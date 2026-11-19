# GTA 6 Live Stream Clipper

Mac app that watches Twitch streams live, detects highlight moments from chat spikes, and renders captioned vertical clips into `clips/` for human review. Full spec: `PLAN.md`. Choices not covered there: `DECISIONS.md`.

## Context
- Goal: post GTA 6 stream moments within minutes during launch week (from 2026-11-19). Being live is the edge.
- Twitch only for now (anonymous IRC chat, no API key). YouTube live is phase 2.
- Runs on an Apple Silicon MacBook Pro (M3 Pro, 18 GB). No NVIDIA.

## Tech stack
- Python 3.11+ managed with `uv` (`uv run ...`, `uv add ...`). asyncio for chat and subprocesses.
- Streamlink (live check + recording) → FFmpeg `-c copy` 2 s mpegts segments as a rolling buffer.
- FFmpeg from Homebrew; final encodes with `h264_videotoolbox`; captions burned in via libass.
- Captions: `mlx-whisper` + `mlx-community/whisper-large-v3-turbo`, loaded once in a single worker process.
- Config in `config.yaml` (pyyaml → dataclasses in `clipper/config.py`). Console via `rich`. Notifications via `osascript`. Tests via `pytest`.

## Rules
- All internal times are wall-clock Unix seconds (`time.time()`); chat uses `tmi-sent-ts / 1000`.
- `detector.py` is pure logic: no sockets, files or clocks passed implicitly. Keep it testable with `tools/replay.py`.
- A channel without a non-empty `permission` note is never recorded. Every meta.json/description carries `credit_url`.
- Nothing is posted automatically. Output ends at a folder of clips plus `clips/index.html`; each new clip may also be sent privately to the owner's paired Telegram chat (`clipper/telegram.py`), never anywhere public.
- Build milestone by milestone (PLAN.md "Build order"); run each acceptance check before moving on.
- If a choice isn't covered, pick the simplest option and log it in `DECISIONS.md`.
- Run long sessions under `caffeinate -dims`. Refuse to start with < 20 GB free disk.
