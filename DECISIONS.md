# Decisions

Choices made during the build that PLAN.md doesn't cover.

- **2026-11-18 · Project lives in `~/Documents/Clipper`**, not `~/gta-clipper`. The plan file was renamed to `PLAN.md`.
- **2026-11-18 · Example channel ships with an empty `permission`** so nothing is recorded until a real, permitted channel is added. The test tools (`clipper.chat`, `tools/test_live.py`) accept any channel name because they only smoke-test locally and never produce postable clips.
- **2026-11-18 · Per-channel overrides are flat keys** on the channel entry (any detector or clip setting, e.g. `ratio_threshold`, `chat_delay_seconds`), merged over the global sections.
- **2026-11-18 · Added `min_free_gb` (default 20)** to config for the startup disk check.
- **2026-11-18 · No `--twitch-disable-ads`.** Streamlink 8 removed the flag; it filters ad segments by default. Added `--twitch-supported-codecs h264` so Twitch never serves HEVC/AV1.
- **2026-11-18 · Segment wall times come from a per-session anchor**, not raw arrival time: `wall = stream_time + offset`, where `offset` is the smallest `arrival - stream_end` seen in the session. Raw arrival times bunch up when Streamlink delivers a burst (startup, after a stall), which would make segments overlap. If the stream clock slips >5 s for 10 segments in a row, a new anchor starts.
- **2026-11-18 · Clip locks are time ranges**, not segment lists, so a lock also protects segments that haven't been recorded yet.
- **2026-11-18 · Recorder deletes session folders from earlier runs at startup.** The buffer is only useful while the app is running.
- **2026-11-18 · Restarts lose ~7 s of video** (2 s delay + Streamlink startup). Measured on xqc. After 3 quick failures in a row the delay rises to 15 s.
