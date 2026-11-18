"""M4 check: fake trigger at "now minus 40 s" on a live recording -> 30 s horizontal_raw.mp4.

    uv run python tools/test_cut.py <channel> [--warmup 75]

Verifies the clip length, and that its first frame matches the source frame at the
requested start (by comparing against frames decoded straight from the segment files).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clipper.clip_job import ClipJob  # noqa: E402
from clipper.cutter import cut  # noqa: E402
from clipper.detector import Trigger  # noqa: E402
from clipper.live_watch import is_live  # noqa: E402
from clipper.recorder import Recorder  # noqa: E402


def frame(src: Path, at: float, out: Path) -> None:
    # Output seeking (after -i): input seeking on a lone .ts segment returns no frame.
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), "-ss", f"{at:.3f}",
                    "-frames:v", "1", "-vf", "scale=480:270", str(out)], check=True)


def psnr(a: Path, b: Path) -> float:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(a), "-i", str(b), "-lavfi", "psnr",
                        "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r"average:(\S+)", r.stderr)
    if not m:
        return 0.0
    return 99.0 if m.group(1) == "inf" else float(m.group(1))


async def main(args) -> int:
    if not await asyncio.to_thread(is_live, args.channel):
        print(f"{args.channel} is not live")
        return 1
    rec = Recorder(args.channel, Path("data"), "1080p60,720p60,best", buffer_minutes=3)
    task = asyncio.create_task(rec.run())
    print(f"recording {args.warmup:.0f}s ...", flush=True)
    await asyncio.sleep(args.warmup)

    now = time.time()
    trig = Trigger(channel=args.channel, id=1, moment=now - 30, start=now - 40, end=now - 10,
                   onset=now - 24, detected_at=now, peak_score=0, baseline=0, ratio=0,
                   top_messages=[], reaction_counts={}, final=True)
    job = ClipJob(trig, rec)
    t0 = time.time()
    ok = await job.wait_ready()
    if not ok:
        print("job dropped:", job.dropped)
        return 1
    out_dir = Path("data/test_cut") / time.strftime("%H%M%S")
    res = await asyncio.to_thread(cut, job.segments, job.start, job.end, trig.moment, out_dir)
    took = time.time() - t0
    print(f"cut {res.duration:.2f}s clip in {took:.1f}s -> {res.video}")

    # Accuracy: compare the clip's first frame with source frames around the requested start.
    first = out_dir / "clip_first.png"
    frame(res.video, 0.0, first)
    seg = next(s for s in job.segments if s.wall_start <= job.start < s.wall_end)
    best = None
    scores = []
    for k in range(-12, 13):
        d = k * 0.125
        at = job.start + d
        s = next((x for x in job.segments if x.wall_start <= at < x.wall_end), None)
        if s is None:
            continue
        ref = out_dir / f"ref_{k:+03d}.png"
        frame(s.path, at - s.wall_start, ref)
        if not ref.exists():          # seek landed past the segment's last frame
            continue
        p = psnr(first, ref)
        ref.unlink()
        scores.append((d, p))
        if best is None or p > best[1]:
            best = (d, p)
    first.unlink()

    job.release()
    await rec.stop()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    dur_ok = abs(res.duration - 30) <= 0.5
    # A re-encoded frame never matches exactly; the right offset just has to stand out.
    others = sorted(p for d, p in scores if abs(d - best[0]) > 0.25) if best else []
    runner_up = others[-1] if others else 0.0
    start_ok = best is not None and abs(best[0]) <= 1.0 and best[1] - runner_up >= 3
    print("PSNR by offset: " + "  ".join(f"{d:+.2f}:{p:.0f}" for d, p in scores))
    print(f"duration {res.duration:.2f}s (want 30 ±0.5): {'ok' if dur_ok else 'BAD'}")
    print(f"first frame best matches source at {best[0]:+.3f}s from requested start "
          f"(PSNR {best[1]:.1f} dB, next best >0.25s away {runner_up:.1f} dB): {'ok' if start_ok else 'BAD'}")
    print(f"start segment {seg.path.name}, offset into it {job.start - seg.wall_start:.2f}s")
    print(f"thumb: {res.thumb} ({res.thumb.stat().st_size // 1024} KB)")
    print("RESULT:", "PASS" if dur_ok and start_ok else "FAIL")
    return 0 if dur_ok and start_ok else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("channel")
    p.add_argument("--warmup", type=float, default=75)
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(asyncio.run(main(a)))
