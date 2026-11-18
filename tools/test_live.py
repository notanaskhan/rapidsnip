"""Smoke-test the recorder on any live channel.

    uv run python tools/test_live.py <channel> [--minutes 5] [--buffer-minutes 2] [--kill-at 120]

Records for --minutes with a short rolling buffer (so deletion is exercised), kills
Streamlink once at --kill-at seconds to check the restart, then reports.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clipper.live_watch import is_live  # noqa: E402
from clipper.recorder import Recorder  # noqa: E402


async def main(args) -> int:
    if not await asyncio.to_thread(is_live, args.channel):
        print(f"{args.channel} is not live")
        return 1
    rec = Recorder(args.channel, Path(args.data_dir), args.quality, args.buffer_minutes)
    task = asyncio.create_task(rec.run())
    started = time.time()
    killed_at = None
    last_old_end = None
    restart_report = None
    max_files = 0

    async def watch_restart():
        nonlocal restart_report
        while restart_report is None:
            await asyncio.sleep(0.25)
            if killed_at and len(rec.stats.sessions) > 1:
                new = [s for s in rec.segments if s.session == rec.stats.sessions[1]]
                if new:
                    restart_report = (time.time() - killed_at, new[0].wall_start - last_old_end)

    watcher = asyncio.create_task(watch_restart())
    while time.time() - started < args.minutes * 60:
        await asyncio.sleep(5)
        elapsed = time.time() - started
        files = len(list(rec.root.glob("*/seg_*.ts")))
        max_files = max(max_files, files)
        print(f"t={elapsed:5.0f}s  segments={len(rec.segments):4d}  files={files:4d}  "
              f"buffer={rec.buffered_seconds:6.1f}s  deleted={rec.stats.deleted:4d}  "
              f"restarts={rec.stats.restarts}", flush=True)
        if args.kill_at and killed_at is None and elapsed >= args.kill_at:
            last_old_end = rec.latest_wall_end
            killed_at = time.time()
            print(f"--- killing streamlink at t={elapsed:.0f}s", flush=True)
            rec.kill_streamlink()
    watcher.cancel()
    await rec.stop()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    segs = rec.segments
    print("\n=== report ===")
    print(f"sessions: {rec.stats.sessions}")
    print(f"restarts: {rec.stats.restarts}  deleted: {rec.stats.deleted}  kept: {len(segs)}  "
          f"max files on disk: {max_files}")
    missing = [s for s in segs if not s.path.exists()]
    print(f"kept segments missing on disk: {len(missing)}")
    durs = [s.duration for s in segs]
    if durs:
        print(f"segment duration: min {min(durs):.2f}s  max {max(durs):.2f}s")
    gaps = []
    for a, b in zip(segs, segs[1:]):
        g = b.wall_start - a.wall_end
        if abs(g) > 0.5:
            gaps.append((g, a.session != b.session, time.strftime("%H:%M:%S", time.localtime(a.wall_end))))
    within = [g for g in gaps if not g[1]]
    across = [g for g in gaps if g[1]]
    print(f"gaps/overlaps > 0.5s within a session: {len(within)} {[(round(g, 1), t) for g, _, t in within]}")
    print(f"gaps across restarts: {[(round(g, 1), t) for g, _, t in across]}")
    if restart_report:
        delay, gap = restart_report
        print(f"restart: first new segment landed {delay:.1f}s after kill; "
              f"video gap across restart {gap:.1f}s")
    elif args.kill_at:
        print("restart: no new segment seen after kill")
    lag = time.time() - rec.latest_wall_end if segs else float("nan")
    print(f"last segment wall_end was {lag:.1f}s before report time")
    ok = (not within or max(abs(g[0]) for g in within) <= 3) and rec.stats.deleted > 0 \
        and (not args.kill_at or restart_report is not None) and not missing
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("channel")
    p.add_argument("--minutes", type=float, default=5)
    p.add_argument("--buffer-minutes", type=float, default=2)
    p.add_argument("--kill-at", type=float, default=120)
    p.add_argument("--quality", default="1080p60,720p60,best")
    p.add_argument("--data-dir", default="data")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(asyncio.run(main(a)))
