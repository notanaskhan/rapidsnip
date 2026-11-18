# GTA 6 Live Stream Clipper — Build Plan

Nov 18, 2026 · @Anas Khan

## How to use this doc with Claude Code

This doc is the full spec for a Mac app that watches Twitch streams live, finds highlight moments from chat spikes, and outputs captioned vertical clips ready to upload. Claude Code should be able to build it from this doc alone.

1. Export this doc as Markdown and save it as `PLAN.md` in an empty folder, e.g. `~/gta-clipper/`.
2. Open Claude Code in that folder and say: *"Read PLAN.md. Build the project milestone by milestone, following the Build order section. After each milestone, run its acceptance check and show me the result before moving on."*
3. Ask Claude Code to create a short `CLAUDE.md` from the Context, Tech stack and Rules sections, so every future session keeps the same decisions.
4. Whenever a choice isn't covered here, Claude Code should pick the simplest option, write it down in `DECISIONS.md`, and keep going.

**Priority for launch night:** Milestones 1–5 give a working clipper (live recording, chat detection, 30-second horizontal clips). Captions, vertical layout and the review page come after. Ship the basic version first, then improve it while streams are running.

## Context and goal

The goal is to post the best GTA 6 stream moments within minutes of them happening, during the launch rush that starts November 19, 2026. Most clippers wait for a stream to end and then search hours of video, so being live is the edge.

**Core idea:** Twitch chat is a free highlight detector. When a streamer does something wild, chat explodes a few seconds later. The app counts chat messages in 10-second windows, compares them with that channel's normal rate, and cuts a clip around each spike.

**Decisions already made:**

- **Build our own, don't rely on clipping SaaS.** Tools like Opus Clip, Vizard and Klap are tuned for talking-head content, can't read chat, don't watch live streams, and charge per minute processed. Crayo is mainly a faceless-content generator (fake texts, split-screen stories), not a stream clipper. These tools may still be used later for polishing.
- **Twitch first.** Twitch chat can be read anonymously without an account or API key. YouTube live support is phase 2.
- **Free tools only:** Streamlink (recording), FFmpeg (cutting and encoding), Whisper running locally (captions), Python (glue).
- **Runs on the user's MacBook Pro.** No NVIDIA GPU, so use Apple-Silicon-friendly options (MLX Whisper, VideoToolbox encoding).
- **Upload by hand, not through the API.** The YouTube Data API allows only about 6 uploads a day by default (1,600 of 10,000 quota units each), and a new unverified API project's uploads are locked to private until Google audits it. The app's job ends at a folder of finished, reviewed clips. The user uploads from YouTube Studio or a phone.
- **A human reviews every clip** before it's posted.
- **Permission first.** Clip only streamers who have agreed, and credit and link them in every upload (see Risks, rules and permission).

**Success looks like:** A finished, captioned vertical clip lands in the output folder within 2 minutes of the moment happening on stream, and at least half the clips are worth posting.

## System overview

Two lanes run side by side for each live channel: video goes into a rolling buffer, chat goes into the detector. A trigger from the detector pulls the matching seconds out of the buffer.

&#91;embedded content: clipper pipeline · video and chat lanes merge at the clip job\]

Everything left of the clip job runs continuously and cheaply (FFmpeg copies the stream without re-encoding). The heavy work, cutting, Whisper and rendering, happens only for triggered moments, one clip at a time.

## Tech stack and MacBook setup

Everything is free and runs locally on macOS. Assume Apple Silicon (M1 or newer); Intel fallbacks are noted.

| Job | Tool | Why / notes |
| --- | --- | --- |
| Language | Python 3.11+, managed with `uv` | asyncio for chat + many subprocesses; `uv` gives fast, reproducible installs |
| Live check + recording | Streamlink (Python library + CLI) | Detects when a channel goes live and pulls the HLS stream. Flags: `--twitch-disable-ads`, `--twitch-low-latency` |
| Chat | Twitch IRC over TLS, raw asyncio socket | `irc.chat.twitch.tv:6697`, anonymous nick `justinfan<random digits>`, no login. No extra library needed |
| Cutting + encoding | FFmpeg from Homebrew | Segment recording with `-c copy` (near-zero CPU); final clips encoded with `h264_videotoolbox` (Apple hardware encoder). Homebrew's build includes libass for burned-in captions |
| Captions | `mlx-whisper` with `mlx-community/whisper-large-v3-turbo` | Runs on the Apple GPU through MLX. Gives word timestamps. Intel fallback: `faster-whisper` on CPU with the `small` model, int8 |
| Config | YAML (`pyyaml`) | Channels, thresholds, layouts |
| Logs + console | `rich` | Live status table per channel |
| Notifications | `osascript` (built into macOS) | Desktop notification when a clip is ready |
| Tests | `pytest` | Mostly for the detector and timing maths |

**One-time setup commands:**

```bash
brew install ffmpeg uv
cd ~/gta-clipper
uv init --python 3.11
uv add streamlink pyyaml rich mlx-whisper
uv add --dev pytest
```

**Mac-specific rules:**

- Run the app under `caffeinate -dims` so the Mac never sleeps mid-stream. Keep it plugged in.
- Check free disk space at startup and refuse to start below 20 GB.
- Download the Whisper model once at setup (first run pulls about 1.6 GB), not during a live clip.
- Keep one Whisper model loaded in a single worker process. Loading it per clip wastes 5–10 seconds.

## Project structure and config file

One Python package, one config file, and clear folders for raw data and finished clips.

```
gta-clipper/
  PLAN.md              this doc
  CLAUDE.md            short context for future Claude Code sessions
  DECISIONS.md         choices made during the build
  config.yaml          channels, thresholds, layouts
  clipper/
    __main__.py        `uv run python -m clipper` starts everything
    config.py          load + validate config.yaml (dataclasses)
    supervisor.py      one ChannelSession per channel, start/stop on live/offline
    live_watch.py      polls Streamlink to see who is live
    recorder.py        Streamlink -> FFmpeg segmenter, segment index
    chat.py            anonymous Twitch IRC reader, parses tags
    detector.py        windows, baseline, scoring, triggers (pure logic, no I/O)
    clip_job.py        a trigger -> a job waiting for its segments
    cutter.py          joins segments, trims to the exact window
    captions.py        MLX Whisper -> word-timed .ass subtitles
    layout.py          9:16 vertical layout (facecam over gameplay)
    worker.py          single process queue: cut -> caption -> render
    output.py          writes clip + metadata JSON, updates review page
    notify.py          macOS notifications
  tools/
    calibrate.py       grabs a frame so facecam/gameplay crop boxes can be set
    replay.py          runs the detector on a saved chat log (offline tuning)
    test_live.py       records 5 min of any live channel to smoke-test the pipeline
  tests/
  data/                rolling segments + chat logs (git-ignored)
  clips/               finished clips, one folder per day
```

**`config.yaml` (starting values, all tunable):**

```yaml
output_dir: ./clips
data_dir: ./data
poll_live_seconds: 60
buffer_minutes: 10            # how much recent video to keep on disk per channel
quality: "1080p60,720p60,best"

detector:
  window_seconds: 10
  step_seconds: 2             # sliding window recalculated every 2 s
  baseline_minutes: 5
  warmup_minutes: 3           # no triggers right after going live
  ratio_threshold: 3.0        # score must be >= 3x baseline
  z_threshold: 4.0            # and >= baseline + 4 x MAD
  min_unique_chatters: 8
  cooldown_seconds: 60
  pause_after_event_seconds: 90   # after raids, hype trains, sub bombs

clip:
  pre_seconds: 20             # before the estimated moment
  post_seconds: 10            # after the estimated moment
  chat_delay_seconds: 6       # how far chat lags the video, tuned per channel
  max_length_seconds: 45
  merge_gap_seconds: 15       # spikes this close become one clip

captions:
  enabled: true
  model: mlx-community/whisper-large-v3-turbo
  words_per_caption: 3

layout:
  enabled: true
  output: [1080, 1920]

channels:
  - name: examplestreamer       # Twitch login name
    permission: "yes, Discord DM 2026-11-18"   # required, app refuses to clip without it
    credit_url: https://twitch.tv/examplestreamer
    ratio_threshold: 3.0        # optional per-channel overrides
    chat_delay_seconds: 6
    layout:
      facecam: [1500, 760, 400, 300]    # x, y, w, h in source pixels; null = no facecam
      gameplay: [240, 0, 1440, 1080]
```

## Module specs

All times inside the app are wall-clock Unix seconds (`time.time()`). Every module converts to that, so chat and video line up without guessing.

### 1. live\_watch.py — who is live

- Every `poll_live_seconds`, for each channel call `streamlink.Streamlink().streams("https://twitch.tv/<name>")`. Non-empty = live.
- On offline → live: tell the supervisor to start a ChannelSession (recorder + chat + detector). On live → offline for 2 polls in a row: stop it cleanly.
- Optional later: a free Twitch developer app (client ID + secret) lets the Helix API confirm the game is GTA VI and fetch the stream title. Not needed for launch.

### 2. recorder.py — rolling video buffer

- Pipeline: `streamlink --twitch-disable-ads --twitch-low-latency --stdout <url> <quality>` piped into `ffmpeg -i pipe:0 -c copy -f segment -segment_time 2 -reset_timestamps 1 -segment_format mpegts -segment_list index.csv -segment_list_type csv seg_%06d.ts`, in `data/<channel>/<session>/`.
- Tail `index.csv`. A row appears when a segment is finished. Record `wall_end = time.time()` at that moment and `wall_start = wall_end - duration`. Keep an in-memory list of `Segment(path, wall_start, wall_end)`.
- Every 30 s, delete segments older than `buffer_minutes`, except ones locked by a pending clip job.
- If Streamlink or FFmpeg exits while the channel is still live, restart within 5 s with a new session folder. Log it.
- Ad breaks with `--twitch-disable-ads` leave gaps. That's fine: gaps show up as missing wall time.

### 3. chat.py — anonymous Twitch chat

- One TLS connection to `irc.chat.twitch.tv:6697` for all channels. Send `CAP REQ :twitch.tv/tags twitch.tv/commands`, `NICK justinfan<5 random digits>`, then `JOIN #<channel>` for each (at most 20 joins per 10 s).
- Reply to `PING` with `PONG`. On `RECONNECT` or a dropped socket, reconnect with backoff (1, 2, 4 … 30 s) and rejoin.
- Parse `PRIVMSG` into `ChatMessage(channel, user_id, login, text, ts)` where `ts = tmi-sent-ts / 1000`. Parse `USERNOTICE` into `ChatEvent(channel, kind=msg-id, ts)` (raid, sub, resub, subgift, submysterygift, announcement).
- Append every message and event to `data/chat/<channel>/<date>.jsonl`. These logs drive offline tuning with `tools/replay.py`.

### 4. detector.py — finding the moment

Pure logic: takes messages and events, returns triggers. No sockets or files, so it's easy to test and replay.

- **Filter:** drop messages from known bots (Nightbot, StreamElements, Streamlabs, Moobot, Fossabot, Sery\_Bot, plus a config list) and commands starting with `!`.
- **Bins:** count per 2-second bin: messages, unique chatters, and reaction hits. Reactions are case-insensitive matches on a config list: `LUL, LULW, OMEGALUL, KEKW, LMAO, LMFAO, 💀, 😂, WTF, NO WAY, HOLY, CLIP, CLIP IT, Pog, PogChamp, monkaS, W, L`. Each chatter counts once per bin.
- **Window score** (10 s window, recalculated every 2 s): `score = unique_chatters * (1 + reaction_share)`, where `reaction_share` is the fraction of messages containing a reaction. Unique chatters beat raw messages because one spammer can't fake a spike.
- **Baseline:** median and MAD (median absolute deviation) of window scores over the last `baseline_minutes`, leaving out windows that triggered. Floor the baseline at 1.
- **Trigger** when all are true: `score >= ratio_threshold * baseline`, `score >= baseline + z_threshold * MAD`, `unique_chatters >= min_unique_chatters`, not in warm-up, not in cooldown, not paused.
- **Pause** for `pause_after_event_seconds` after a raid, a gift-sub event or 5+ sub events within 30 s. These flood chat without a gameplay moment.
- **Onset:** walk back from the trigger window bin by bin to the first bin above `1.5 * baseline`. That bin's start is `onset`. The estimated moment is `moment = onset - chat_delay_seconds`.
- **Extend:** while later windows stay above threshold, push the clip end forward, up to `max_length_seconds`.
- **Merge:** a new trigger within `merge_gap_seconds` of the last one extends it instead of starting a new clip.
- **Output:** `Trigger(channel, moment, start=moment-pre_seconds, end=moment+post_seconds, peak_score, baseline, ratio, top_messages[10], reaction_counts)`.

### 5. clip\_job.py — waiting for the video

- On a trigger, lock the segments covering `[start, end]` so cleanup can't delete them.
- Wait until a segment with `wall_end >= end` exists (the post-moment seconds need to be recorded), plus any extension. Time out after 60 s and use what exists.
- If more than 20% of the window is missing (ad break, reconnect), drop the job and log why.
- Hand the job to the worker queue.

### 6. cutter.py — exact cut

- Write an FFmpeg concat list of the covering segments. Cut with `-ss` (start minus first segment's wall\_start) and `-t` (end minus start).
- Re-encode for a frame-exact cut: `-c:v h264_videotoolbox -b:v 8M -c:a aac -b:a 160k`. Output `horizontal_raw.mp4`.
- Also save `thumb.jpg`, the frame at `moment`.

### 7. captions.py — burned-in captions

- Extract 16 kHz mono WAV. Run `mlx_whisper.transcribe(wav, path_or_hf_repo=model, word_timestamps=True)`.
- Group words into chunks of `words_per_caption`. Write an `.ass` subtitle file: bold, white, thick black outline, centered, uppercase. Font size around 80 at 1080×1920.
- Empty or near-empty transcript (screams, music) → skip captions for that clip, don't fail.
- Save the plain transcript to the metadata for titles.

### 8. layout.py — vertical 9:16

- Output 1080×1920. With a facecam: facecam crop on top, scaled to fill 1080×672; gameplay crop below, scaled to fill 1080×1248 (use `scale=W:H:force_original_aspect_ratio=increase,crop=W:H`, so nothing stretches).
- Without a facecam (`facecam: null`): gameplay scaled to 1080 wide in the middle, over a blurred, enlarged copy of itself as the background.
- Captions burn in last with the `subtitles` filter, placed near the band boundary so they don't cover the facecam or the game's center.
- Write `vertical.mp4`. Also write `horizontal.mp4` (16:9 with the same captions) for long-form compilations.
- Crop boxes come from `config.yaml`. `tools/calibrate.py <channel>` saves a current frame as `calibrate_<channel>.jpg` with a pixel grid. The user (or Claude Code, by viewing the image) reads off the facecam and gameplay boxes.

### 9. worker.py — one processing queue

- A single separate process with a job queue. It loads the Whisper model once and runs cut → captions → layout for each job.
- Process highest score first when several jobs wait. Drop jobs older than 15 minutes: a stale clip is worth little during launch week.
- Rough target on an M-series MacBook: under 30 s of processing per 30-second clip.

### 10. output.py and notify.py — ready to review

- Folder per clip: `clips/<YYYY-MM-DD>/<channel>_<HHMMSS>_<ratio>x/` with `vertical.mp4`, `horizontal.mp4`, `thumb.jpg`, `meta.json`.
- `meta.json`: channel, credit\_url, permission note, moment time, window, score, baseline, ratio, top chat messages, transcript, suggested title, suggested description.
- Suggested title from a template: a short hook from the transcript or the most common reaction, plus the streamer name and `#GTA6`. Suggested description always includes "Clip from \<credit\_url>, used with permission."
- Regenerate `clips/index.html` after each clip: newest first, a video player per clip, ratio, top chat lines, and a button that copies the title and description.
- Send a macOS notification: `osascript -e 'display notification "<channel> 4.8x spike" with title "New clip ready"'`.

### 11. supervisor.py and the console

- Starts live\_watch, the shared chat connection, the worker, and a ChannelSession per live channel. Handles Ctrl+C cleanly: stops recorders, finishes the current clip, then exits.
- A `rich` live table refreshing every 2 s: channel, live, buffer length, score now, baseline, last trigger, clips made, queue length.

## Build order

Build in this order. Each milestone ends with a check that must pass before the next one starts. Milestones 1–5 are the launch-night minimum; 6–9 make the clips postable and the app comfortable to run.

**M0 — Setup.** Create the folders, `config.yaml`, `CLAUDE.md`, install dependencies, download the Whisper model. *Check:* `ffmpeg -encoders | grep videotoolbox` shows `h264_videotoolbox`; `uv run python -c "import streamlink, mlx_whisper"` works; the model is cached.

**M1 — Chat reader** (`chat.py`). *Check:* `uv run python -m clipper.chat <big live channel>` prints messages live with correct timestamps for 2 minutes, survives pulling Wi-Fi for 10 s, and writes the `.jsonl` log.

**M2 — Recorder** (`recorder.py`, `live_watch.py`). *Check:* `tools/test_live.py <channel>` records 5 minutes. Segments exist, the in-memory index has no gaps over 3 s, segments older than the buffer get deleted, and killing Streamlink triggers a restart.

**M3 — Detector** (`detector.py` + unit tests + `tools/replay.py`). *Check:* unit tests pass for: a flat chat (no triggers), a clear spike (one trigger, correct onset), one spammer (no trigger), a raid (no trigger during pause), two close spikes (merged). `replay.py` on a 30-minute saved chat log prints its triggers with times.

**M4 — Clip jobs + cutter** (`clip_job.py`, `cutter.py`). *Check:* a fake trigger at "now minus 40 s" on a live recording produces a 30-second `horizontal_raw.mp4` that plays and starts within 1 s of the requested time.

**M5 — End to end, horizontal** (`supervisor.py`, `worker.py`, basic `output.py`). *Check:* `uv run python -m clipper` with 2 live channels runs for 30 minutes, the console table updates, and real chat spikes produce clip folders with `meta.json`. **This is the launch-night minimum.**

**M6 — Captions** (`captions.py`). *Check:* clips have readable, correctly timed captions. A clip with only screaming still renders, without captions.

**M7 — Vertical layout** (`layout.py`, `tools/calibrate.py`). *Check:* `vertical.mp4` is 1080×1920 with the facecam on top, gameplay below and captions between, for one facecam channel and one `facecam: null` channel.

**M8 — Review page + notifications** (`output.py`, `notify.py`). *Check:* `clips/index.html` lists clips newest first, plays them, and copies the title and description. A notification appears per clip.

**M9 — Hardening.** *Check:* runs 3 hours on 3 channels without crashing or growing memory; disk use stays near `buffer_minutes` per channel; Ctrl+C exits cleanly; a channel ending its stream stops its session.

## Testing and tuning

Test on any big live channel today. GTA 6 isn't needed to prove the pipeline works.

**Before launch:**

- Run M1–M5 against 2–3 large channels streaming anything right now. Chat behaves the same whatever the game.
- Save at least an hour of chat logs per channel. These become the replay set for the detector.
- Optional: download chat from a past Twitch VOD with the `chat-downloader` pip package, so the detector can be tuned against a known stream.

**Tuning targets:**

| Measure | Target | If off |
| --- | --- | --- |
| Clips per channel per hour | 2–6 | Too many: raise `ratio_threshold` toward 4. Too few: lower toward 2.5 |
| Clips worth posting | At least half | Look at the rejects' chat in `meta.json`. Add bot names or noise words to the filters |
| Moment inside the clip | Action visible in the first 20 s | Clip starts late: raise `chat_delay_seconds`. Long dead lead-in: lower it. Adjust per channel |
| Spike to finished clip | Under 2 minutes | Check the worker queue length and the Whisper time per clip |

**Rules for tuning:**

- Change one setting at a time, then rerun `tools/replay.py` on the same logs to compare trigger counts.
- Small channels need lower thresholds than giant ones. Use per-channel overrides rather than one global value.
- Keep every rejected clip's `meta.json` for a day. They're the best evidence for what to filter.

## Launch night runbook

The app finds and renders clips; the person running it picks, titles and posts. Keep the loop under 5 minutes per clip.

**Before streams start:**

1. Confirm permission notes are filled in for every channel in `config.yaml`. The app refuses channels without one.
2. Run `tools/calibrate.py` for each facecam channel once they're live, and paste the crop boxes into the config.
3. Plug in the Mac, close heavy apps, check at least 50 GB free.
4. Start with `caffeinate -dims uv run python -m clipper`.
5. Open `clips/index.html` in a browser and YouTube Studio in another tab.

**While running:**

- Start with 3 channels. Add more only if the queue stays near zero. Each channel at 1080p60 writes about 3–4 GB per hour, but the rolling buffer keeps only the last 10 minutes.
- On each notification: watch the clip, reject or post. Post the vertical version as a Short first, since Shorts are where the launch-week views are.
- Use the suggested title as a starting point, not as-is. A sharp human title beats a template.
- Every upload credits and links the streamer in the description.
- Every hour, look at the console: rising queue = too many channels or thresholds too low.

**If something breaks:**

- No clips but chat is busy → check the console baseline; lower that channel's `ratio_threshold`.
- Clips miss the moment → raise that channel's `chat_delay_seconds` by 2–3 s.
- Recorder keeps restarting → drop that channel to `720p60`.
- Mac running hot or slow → turn captions off for a while (`captions.enabled: false`) and add them in a later pass.

## Risks, rules and permission

The biggest risk isn't technical. It's losing the channel to copyright strikes during the one week that matters.

**Hard rules the code enforces:**

- A channel without a `permission` note in `config.yaml` is never recorded. The supervisor logs a warning and skips it.
- Every `meta.json` and suggested description carries the streamer's `credit_url`.
- Nothing is uploaded automatically. A person watches every clip first.

**Risks and what to do about them:**

| Risk | What happens | Fallback |
| --- | --- | --- |
| Copyright strikes | Streams belong to the streamers. Three strikes and YouTube deletes the channel | Get written permission (a Discord DM counts). Mid-size streamers are easier to reach and less crowded with clippers |
| No monetization | YouTube's reused content policy blocks plain re-uploads | Add captions, edits and compilations so the uploads count as transformed |
| Twitch chat or Streamlink changes | Twitch has changed its stream and chat formats before | Keep Streamlink updated (`uv add streamlink --upgrade`). The chat reader is small and easy to fix |
| Mac can't keep up | Queue grows, clips get stale | Fewer channels, 720p, captions off, or move to a cloud machine later |
| Many competitors with the same idea | The "post first" edge shrinks | Pick streamers well, write better titles, post the vertical cut fast |
| Bad clips posted | A raid or off-topic moment goes up under your name | Human review, pause after raids, bot filters |
| Something unshareable on stream | Personal info or something offensive gets clipped | Review step; never post a clip you haven't watched to the end |

## Later: phase 2 ideas

None of these are needed for launch week. Build them only once the core loop is producing views.

- **YouTube Live streams:** record with Streamlink or `yt-dlp`, read live chat with `pytchat` or `chat-downloader`. The detector stays the same.
- **More highlight signals:** audio loudness spikes (the streamer yelling) combined with the chat score, to catch moments on channels with quiet chats.
- **Twitch Helix API** (free developer app): confirm the game is GTA VI, fetch stream titles, and compare triggers with viewer-made Twitch clips to measure detector accuracy.
- **Better titles:** send the transcript and top chat lines to an LLM for 3 title options per clip.
- **Review server:** a small local web app with Approve and Reject buttons that move files into `to_post/` and `rejected/`.
- **Karaoke-style captions:** highlight each word as it's spoken.
- **Compilations:** stitch the day's best horizontal clips into a 10-minute "Best GTA 6 moments" video.
- **Auto-upload:** only after the Google API audit is approved and quota raised, and still behind human approval.
