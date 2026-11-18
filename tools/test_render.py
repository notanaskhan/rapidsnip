"""M7 check: run the worker's full cut -> captions -> layout path on recorded segments.

    uv run python tools/test_render.py <channel> [--session DIR] [--seconds 30] [--back 40]

Takes `--seconds` of video ending `--back` seconds before the newest segment of the
channel's latest recording session, renders it exactly like the app would (using the
channel's layout from config.yaml, or no facecam if it isn't listed), then checks the
outputs and saves a contact sheet of frames at caption times.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clipper.config import load_config  # noqa: E402
from clipper.cutter import probe_duration, probe_size  # noqa: E402
from clipper.output import clip_folder  # noqa: E402
from clipper.worker import _process  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("channel")
    p.add_argument("--session", type=Path)
    p.add_argument("--seconds", type=float, default=30)
    p.add_argument("--back", type=float, default=40)
    a = p.parse_args()
    cfg = load_config()
    ch = next((c for c in cfg.channels if c.name == a.channel), None)

    session = a.session or sorted((cfg.data_dir / a.channel).iterdir())[-1]
    rows = [ln.split(",") for ln in (session / "index.csv").read_text().splitlines() if ln.count(",") >= 2]
    segs = [(str(session / r[0]), float(r[1]), float(r[2])) for r in rows if (session / r[0]).exists()]
    end = segs[-1][2] - a.back
    start = end - a.seconds
    use = [s for s in segs if s[2] > start and s[1] < end]
    now = time.time()
    shift = now - segs[-1][2]                       # stream time -> pretend wall time
    trig = {"moment": start + 20 + shift, "onset": start + 26 + shift, "detected_at": now,
            "peak_score": 0, "baseline": 0, "ratio": 0.0, "top_messages": [], "reaction_counts": {}}
    job = {
        "id": "test", "channel": a.channel,
        "display_name": ch.display_name if ch else a.channel,
        "credit_url": ch.credit_url if ch else f"https://twitch.tv/{a.channel}",
        "permission": ch.permission if ch else "TEST ONLY - not for posting",
        "segments": [(p_, s + shift, e + shift) for p_, s, e in use],
        "start": start + shift, "end": end + shift, "trigger": trig,
        "folder": str(clip_folder(cfg.data_dir / "test_render", a.channel, start + shift, 0)),
        "layout": {"enabled": True, "output": list(cfg.layout.output),
                   "facecam": list(ch.layout.facecam) if ch and ch.layout.facecam else None,
                   "gameplay": list(ch.layout.gameplay) if ch and ch.layout.gameplay else None},
    }
    print(f"{a.channel}: {len(use)} segments from {session.name}, layout {job['layout']}")
    from clipper.captions import load_model
    load_model(cfg.captions.model)
    res = _process(job, {"enabled": True, "model": cfg.captions.model,
                         "words_per_caption": cfg.captions.words_per_caption})
    folder = Path(res["folder"])
    print(f"timings {res['timings']}  captions {res['captions']}")
    ok = True
    for name, want in (("vertical.mp4", (1080, 1920)), ("horizontal.mp4", None), ("thumb.jpg", None),
                       ("meta.json", None)):
        f = folder / name
        line = f"  {name}: {'exists' if f.exists() else 'MISSING'}"
        ok &= f.exists()
        if f.exists() and f.suffix == ".mp4":
            size = probe_size(f)
            line += f"  {size[0]}x{size[1]}  {probe_duration(f):.2f}s"
            if want and size != want:
                ok = False
                line += "  WRONG SIZE"
        print(line)
    meta = json.loads((folder / "meta.json").read_text())
    print(f"  title: {meta['suggested_title']}")
    print(f"  transcript: {meta['transcript'][:120]!r}")

    # Contact sheet: vertical frames at the middle of the first 3 captions (or 3 evenly spaced times).
    ass = folder / "captions_v.ass"
    times = []
    if ass.exists():
        for ln in ass.read_text().splitlines():
            if ln.startswith("Dialogue"):
                s_, e_ = ln.split(",")[1:3]
                to_s = lambda t: sum(float(x) * m for x, m in zip(t.split(":"), (3600, 60, 1)))  # noqa: E731
                times.append((to_s(s_) + to_s(e_)) / 2)
    times = (times or [5, 15, 25])[:3]
    frames = []
    for i, t in enumerate(times):
        out = folder / f"check_{i}.jpg"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(folder / "vertical.mp4"), "-ss",
                        f"{t:.2f}", "-frames:v", "1", "-vf", "scale=360:640", str(out)], check=True)
        frames.append(out)
    sheet = folder / "check_sheet.jpg"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *sum((["-i", str(f)] for f in frames), []),
                    "-filter_complex", f"hstack=inputs={len(frames)}", str(sheet)], check=True)
    for f in frames:
        f.unlink()
    print(f"sheet: {sheet}")
    print("RESULT:", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    main()
