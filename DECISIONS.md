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
- **2026-11-18 · Pause rules narrowed.** The plan pauses after any gift-sub event, but xqc gets a small gift bomb every 30-60 s, which kept the detector paused almost all the time. Now: pause after a raid, a mystery gift of 10+ subs, or 5+ sub/resub/standalone-gift events within 30 s. Gifts that belong to a mystery gift (`community-gift-id`) only count through the mystery gift's size.
- **2026-11-18 · Baseline excludes only windows that are part of a trigger or a pause, and never during warm-up.** Excluding every above-threshold window let an empty starting baseline (floor 1) lock out all windows forever.
- **2026-11-18 · Onset uses a per-bin threshold** of `1.5 * baseline / bins_per_window` (the baseline is per 10 s window, bins are 2 s), walking back at most 30 s through an unbroken run of hot bins.
- **2026-11-18 · Triggers are emitted early and updated.** The detector emits a trigger as soon as it fires (`final=False`), re-emits it as later hot windows extend it, and emits `final=True` once chat has been quiet for `merge_gap_seconds` or the clip hits `max_length_seconds`. Cooldown counts from the last hot window of the previous trigger.
- **2026-11-18 · Replay splits logs at silences over 60 s** and starts a fresh detector (with warm-up) after each, matching how the live app starts a detector when a channel goes live.
- **2026-11-18 · A clip is cut from one contiguous run of segments.** Concatenating across a restart gap would shift everything after the gap, so the job picks the run covering the most of the window and trims to it; the 20% missing rule applies to that run.
- **2026-11-18 · Cutter seeks after the input (`-ss` after `-i`) on the concat list** for frame-exact starts. Measured on xqc: 30 s 1080p60 clip in ~9 s, first frame matches the source at +0.000 s.
