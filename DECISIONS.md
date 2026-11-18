# Decisions

Choices made during the build that PLAN.md doesn't cover.

- **2026-11-18 · Project lives in `~/Documents/Clipper`**, not `~/gta-clipper`. The plan file was renamed to `PLAN.md`.
- **2026-11-18 · Example channel ships with an empty `permission`** so nothing is recorded until a real, permitted channel is added. The test tools (`clipper.chat`, `tools/test_live.py`) accept any channel name because they only smoke-test locally and never produce postable clips.
- **2026-11-18 · Per-channel overrides are flat keys** on the channel entry (any detector or clip setting, e.g. `ratio_threshold`, `chat_delay_seconds`), merged over the global sections.
- **2026-11-18 · Added `min_free_gb` (default 20)** to config for the startup disk check.
