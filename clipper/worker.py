"""Single worker process: cut -> (captions -> layout, later milestones) -> output.

The supervisor keeps the priority queue and sends one job at a time, so the worker
only ever holds the job it is working on. Jobs and results are plain dicts.
"""
from __future__ import annotations

import json
import logging
import multiprocessing as mp
import os
import queue
import signal
import time
import traceback
from pathlib import Path

log = logging.getLogger(__name__)


def _process(job: dict, captions_cfg: dict) -> dict:
    from . import captions
    from .cutter import cut
    from .layout import render
    from .output import write_index, write_meta
    from .recorder import Anchor, Segment

    timings = {}
    t0 = time.time()
    folder = Path(job["folder"])
    segs = [Segment(Path(p), a, b, Anchor(0.0), "") for p, a, b in job["segments"]]
    res = cut(segs, job["start"], job["end"], job["trigger"]["moment"], folder)
    timings["cut"] = round(time.time() - t0, 1)

    transcript = ""
    caps = []
    if captions_cfg.get("enabled"):
        t1 = time.time()
        wav = captions.extract_wav(res.video, folder / "audio.wav")
        words, transcript = captions.transcribe(wav, captions_cfg["model"])
        wav.unlink(missing_ok=True)
        caps = captions.group(words, captions_cfg.get("words_per_caption", 3))
        timings["captions"] = round(time.time() - t1, 1)

    # One pass: vertical 9:16 (if enabled) + horizontal 16:9, both with the same captions.
    t2 = time.time()
    lay = job.get("layout", {})
    render(res.video, folder, caps, lay.get("facecam"), lay.get("gameplay"),
           output=tuple(lay.get("output", (1080, 1920))), vertical=lay.get("enabled", True))
    timings["render"] = round(time.time() - t2, 1)
    timings["total"] = round(time.time() - t0, 1)

    meta = write_meta(folder, job, transcript=transcript, timings=timings)
    write_index(folder.parent.parent)
    return {"folder": str(folder), "duration": res.duration, "timings": timings,
            "captions": len(caps), "title": json.loads(meta.read_text())["suggested_title"]}


def worker_main(in_q: mp.Queue, out_q: mp.Queue, captions_cfg: dict | None = None) -> None:
    # Ctrl+C in the terminal must not kill the clip in progress (or its ffmpeg children):
    # leave the terminal's process group; the supervisor stops us through the queue.
    os.setpgrp()
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    logging.basicConfig(level=logging.INFO)
    captions_cfg = captions_cfg or {}
    parent = os.getppid()
    if captions_cfg.get("enabled"):
        from .captions import load_model
        t = time.time()
        load_model(captions_cfg["model"])     # once, so clips don't pay 5-10 s each
        log.info("whisper model loaded in %.1fs", time.time() - t)
    out_q.put({"type": "ready"})
    while True:
        try:
            job = in_q.get(timeout=5)
        except queue.Empty:
            if os.getppid() != parent:     # supervisor died without stopping us
                break
            continue
        if job is None:
            break
        try:
            result = _process(job, captions_cfg)
            out_q.put({"type": "done", "id": job["id"], "ok": True, **result})
        except Exception as e:  # noqa: BLE001 - report every failure back to the supervisor
            out_q.put({"type": "done", "id": job["id"], "ok": False, "error": repr(e),
                       "trace": traceback.format_exc()})


class WorkerHandle:
    def __init__(self, captions_cfg: dict | None = None):
        ctx = mp.get_context("spawn")
        self.in_q = ctx.Queue()
        self.out_q = ctx.Queue()
        self.proc = ctx.Process(target=worker_main, args=(self.in_q, self.out_q, captions_cfg),
                                name="clip-worker", daemon=True)

    def start(self) -> None:
        self.proc.start()

    def submit(self, job: dict) -> None:
        self.in_q.put(job)

    def get(self, timeout: float | None = None):
        return self.out_q.get(timeout=timeout)

    def stop(self, timeout: float = 5) -> None:
        if self.proc.is_alive():
            self.in_q.put(None)
            self.proc.join(timeout)
        if self.proc.is_alive():
            self.proc.terminate()
