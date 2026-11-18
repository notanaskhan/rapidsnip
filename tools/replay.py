"""Run the detector over saved chat logs and print the triggers it would fire.

    uv run python tools/replay.py data/chat/xqc/2026-11-18.jsonl [--ratio 3.5] [--z 4] [--min-chatters 8]

Uses config.yaml (including per-channel overrides when the channel is listed there).
Change one setting at a time and compare trigger counts on the same logs.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clipper.chat import ChatEvent, ChatMessage  # noqa: E402
from clipper.config import load_config  # noqa: E402
from clipper.detector import Detector  # noqa: E402


def load_items(paths: list[Path]):
    items = []
    for p in paths:
        for line in p.open(encoding="utf-8"):
            row = json.loads(line)
            kind = row.pop("type", "msg")
            cls = ChatMessage if kind == "msg" else ChatEvent
            names = {f.name for f in dataclasses.fields(cls)} - {"type"}
            items.append(cls(**{k: v for k, v in row.items() if k in names}))
    items.sort(key=lambda i: i.ts)
    return items


def hhmmss(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("logs", nargs="+", type=Path)
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--ratio", type=float, help="override ratio_threshold")
    p.add_argument("--z", type=float, help="override z_threshold")
    p.add_argument("--min-chatters", type=int, help="override min_unique_chatters")
    p.add_argument("--chat-delay", type=float, help="override chat_delay_seconds")
    p.add_argument("--quiet", action="store_true", help="only print the summary")
    p.add_argument("--peaks", type=int, default=0, help="also list the N biggest windows (score/baseline)")
    p.add_argument("--gap", type=float, default=60, help="silence (s) that splits the log into runs")
    args = p.parse_args()

    items = load_items(args.logs)
    if not items:
        sys.exit("no chat items")
    channel = items[0].channel
    cfg = load_config(args.config)
    ch = next((c for c in cfg.channels if c.name == channel), None)
    det_cfg = ch.detector if ch else cfg.detector
    clip_cfg = ch.clip if ch else cfg.clip
    if args.ratio is not None:
        det_cfg = dataclasses.replace(det_cfg, ratio_threshold=args.ratio)
    if args.z is not None:
        det_cfg = dataclasses.replace(det_cfg, z_threshold=args.z)
    if args.min_chatters is not None:
        det_cfg = dataclasses.replace(det_cfg, min_unique_chatters=args.min_chatters)
    if args.chat_delay is not None:
        clip_cfg = dataclasses.replace(clip_cfg, chat_delay_seconds=args.chat_delay)

    # A long silence means the log has a hole (app stopped, stream offline): start a fresh
    # detector after it, with its own warm-up, like the live app does on going live.
    det = Detector(channel, det_cfg, clip_cfg, started_at=items[0].ts)
    finals = []
    windows = []
    runs = [[items[0].ts, items[0].ts]]
    prev_ts = items[0].ts
    for it in items:
        if it.ts - prev_ts > args.gap:
            finals += [t for t in det.tick(prev_ts + 60) + det.flush() if t.final]
            det = Detector(channel, det_cfg, clip_cfg, started_at=it.ts)
            runs.append([it.ts, it.ts])
        prev_ts = it.ts
        runs[-1][1] = it.ts
        det.feed(it)
        finals += [t for t in det.tick(it.ts) if t.final]
        if args.peaks and det.last_baseline > 0:
            windows.append((det.last_score / det.last_baseline, det.last_score, det.last_baseline,
                            det.last_mad, det.next_bin * det.step, det.pause_until > det.next_bin * det.step,
                            det.next_bin * det.step < det.started_at + det_cfg.warmup_minutes * 60))
    finals += [t for t in det.tick(items[-1].ts + 60) + det.flush() if t.final]

    if len(runs) > 1:
        print("log has gaps; replayed as separate runs: "
              + ", ".join(f"{hhmmss(a)}-{hhmmss(b)}" for a, b in runs))
    span_h = sum(b - a for a, b in runs) / 3600
    msgs = sum(isinstance(i, ChatMessage) for i in items)
    events = len(items) - msgs
    print(f"#{channel}: {msgs} messages, {events} events, {hhmmss(items[0].ts)} -> "
          f"{hhmmss(items[-1].ts)} ({span_h * 60:.0f} min of chat)")
    print(f"settings: ratio {det_cfg.ratio_threshold}  z {det_cfg.z_threshold}  "
          f"min_chatters {det_cfg.min_unique_chatters}  chat_delay {clip_cfg.chat_delay_seconds}")
    print(f"final baseline {det.last_baseline:.1f}  MAD {det.last_mad:.1f}\n")
    if not args.quiet:
        for t in finals:
            top = ", ".join(f"{txt[:30]!r}x{n}" for txt, n in t.top_messages[:3])
            reacts = ", ".join(f"{k} {v}" for k, v in list(t.reaction_counts.items())[:4])
            print(f"[{t.id:2d}] moment {hhmmss(t.moment)}  clip {hhmmss(t.start)}-{hhmmss(t.end)} "
                  f"({t.end - t.start:.0f}s)  ratio {t.ratio:.1f}x  score {t.peak_score:.0f}/"
                  f"{t.baseline:.0f}  detected +{t.detected_at - t.onset:.0f}s after onset")
            print(f"     chat: {top}")
            if reacts:
                print(f"     reactions: {reacts}")
    if args.peaks:
        print(f"\nbiggest windows (need ratio >= {det_cfg.ratio_threshold} and score >= baseline + "
              f"{det_cfg.z_threshold} x MAD):")
        seen = []
        for r, sc, bl, mad, t, paused, warm in sorted(windows, reverse=True):
            if any(abs(t - u) < 30 for u in seen):
                continue
            seen.append(t)
            need = max(det_cfg.ratio_threshold * bl, bl + det_cfg.z_threshold * mad)
            flags = " PAUSED" if paused else (" WARM-UP" if warm else "")
            print(f"  {hhmmss(t)}  {r:4.1f}x  score {sc:5.0f}  baseline {bl:5.0f}  MAD {mad:4.0f}  "
                  f"needs {need:5.0f}{flags}")
            if len(seen) >= args.peaks:
                break
    rate = len(finals) / span_h if span_h > 0 else 0
    print(f"\n{len(finals)} triggers = {rate:.1f}/hour (target 2-6/hour)")


if __name__ == "__main__":
    main()
