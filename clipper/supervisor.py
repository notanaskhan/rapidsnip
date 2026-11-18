"""Runs everything: live watcher, shared chat, one ChannelSession per live channel, the worker.

Two ways to run it:
- `run()`: the command-line app (rich console table, own signal handling).
- `start()` / `stop()`: embedded in the API server (`clipper serve`), which owns the loop and signals.

The worker queue is shared: live clip jobs and link-clipper jobs (`submit()`) wait in one
priority queue and go to the single worker one at a time.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import queue
import signal
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from rich.console import Console
from rich.live import Live
from rich.table import Table

from .chat import ChatItem, TwitchChat
from .clip_job import ClipJob
from .config import ChannelConfig, Config
from .detector import Detector, Trigger
from .live_watch import LiveWatcher
from .notify import notify
from .output import clip_folder, write_index
from .recorder import Recorder
from .worker import WorkerHandle

log = logging.getLogger(__name__)

TICK_SECONDS = 1.0
STALE_SECONDS = 15 * 60      # drop live jobs whose moment is older than this
STATUS_EVERY = 300
HISTORY_POINTS = 150         # score history kept per channel for the UI (5 min at 2 s)


@dataclass
class ChannelSession:
    cfg: ChannelConfig
    recorder: Recorder
    detector: Detector
    started: float = field(default_factory=time.time)
    jobs: dict[int, ClipJob] = field(default_factory=dict)
    job_tasks: set[asyncio.Task] = field(default_factory=set)
    rec_task: asyncio.Task | None = None
    last_trigger: Trigger | None = None
    clips: int = 0
    dropped: int = 0
    history: deque = field(default_factory=lambda: deque(maxlen=HISTORY_POINTS))
    last_bin: int = 0


@dataclass
class Pending:
    payload: dict                                   # what the worker gets
    score: float                                    # higher goes first
    created: float = field(default_factory=time.time)
    job: ClipJob | None = None                      # live jobs only
    session: ChannelSession | None = None
    stale_after: float | None = STALE_SECONDS       # None = never stale (link jobs)
    moment: float = 0.0
    on_done: Callable[[dict], None] | None = None   # called with the worker's result


class Supervisor:
    def __init__(self, cfg: Config, console: Console | None = None, db=None):
        self.cfg = cfg
        self.console = console or Console()
        self.db = db
        self.channels: dict[str, ChannelConfig] = {c.name: c for c in cfg.channels if c.recordable}
        self.sessions: dict[str, ChannelSession] = {}
        self.chat = TwitchChat(cfg.data_dir / "chat", on_item=self._on_chat)
        self.watcher = LiveWatcher(list(self.channels), cfg.poll_live_seconds,
                                   self._on_live, self._on_offline)
        self.worker = WorkerHandle(dataclasses.asdict(cfg.captions))
        self.pending: list[Pending] = []
        self.busy: Pending | None = None
        self.clips_total = 0
        self.started_at = 0.0
        self.tasks: list[asyncio.Task] = []
        self._stopping = False

    # ---- chat -------------------------------------------------------

    def _on_chat(self, item: ChatItem) -> None:
        s = self.sessions.get(item.channel)
        if s:
            s.detector.feed(item)

    # ---- channel set (changes from the UI) -----------------------------

    async def set_channels(self, channels: list[ChannelConfig]) -> None:
        """Replace the channel list. New channels start being watched, removed or disabled
        ones stop recording, and edited ones pick up their new settings."""
        new = {c.name: c for c in channels if c.recordable}
        for name in list(self.channels):
            if name not in new:
                self.channels.pop(name)
                await self.chat.part(name)
                if name in self.sessions:
                    await self._on_offline(name, reason="removed")
                self.watcher.live.discard(name)
        for name, c in new.items():
            if name not in self.channels:
                await self.chat.join(name)
            self.channels[name] = c
            s = self.sessions.get(name)
            if s:
                s.cfg = c
                s.detector.det, s.detector.clip = c.detector, c.clip
        self.watcher.channels = list(self.channels)
        if self.started_at:
            asyncio.create_task(self.watcher.poll_once())    # notice new live channels right away

    # ---- channel sessions -------------------------------------------

    async def _on_live(self, name: str) -> None:
        if self._stopping or name in self.sessions or name not in self.channels:
            return
        cfg = self.channels[name]
        rec = Recorder(name, self.cfg.data_dir, self.cfg.quality, self.cfg.buffer_minutes)
        det = Detector(name, cfg.detector, cfg.clip, started_at=time.time())
        s = ChannelSession(cfg, rec, det)
        s.rec_task = asyncio.create_task(rec.run(), name=f"rec-{name}")
        s.rec_task.add_done_callback(self._task_died)
        self.sessions[name] = s
        log.info("[%s] session started", name)

    async def _on_offline(self, name: str, reason: str = "stream ended") -> None:
        s = self.sessions.get(name)
        if not s:
            return
        log.info("[%s] %s; finishing open clips", name, reason)
        self._handle_triggers(s, s.detector.flush())
        if s.job_tasks:
            await asyncio.wait(s.job_tasks, timeout=90)
        await s.recorder.stop()
        if s.rec_task:
            s.rec_task.cancel()
        self.sessions.pop(name, None)
        log.info("[%s] session stopped", name)

    @staticmethod
    def _task_died(task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception():
            log.error("task %s died: %r", task.get_name(), task.exception(),
                      exc_info=task.exception())

    # ---- triggers -> jobs -> worker ---------------------------------

    def _handle_triggers(self, s: ChannelSession, triggers: list[Trigger]) -> None:
        for t in triggers:
            s.last_trigger = t
            job = s.jobs.get(t.id)
            if job:
                job.update(t)
                continue
            log.info("[%s] trigger %d: %.1fx at %s", t.channel, t.id, t.ratio,
                     datetime.fromtimestamp(t.moment).strftime("%H:%M:%S"))
            job = ClipJob(t, s.recorder)
            s.jobs[t.id] = job
            task = asyncio.create_task(self._await_job(s, job), name=f"job-{t.channel}-{t.id}")
            task.add_done_callback(self._task_died)
            s.job_tasks.add(task)
            task.add_done_callback(s.job_tasks.discard)

    async def _await_job(self, s: ChannelSession, job: ClipJob) -> None:
        ok = await job.wait_ready()
        if not ok:
            s.dropped += 1
            s.jobs.pop(job.trigger.id, None)
            return
        self.pending.append(Pending(self._job_dict(s, job), job.score, job=job, session=s,
                                    moment=job.trigger.moment))
        self._dispatch()

    def submit(self, payload: dict, score: float = 0.0,
               on_done: Callable[[dict], None] | None = None) -> None:
        """Queue a non-live render (link clipper). Live clips with a higher ratio still go first."""
        self.pending.append(Pending(payload, score, stale_after=None, on_done=on_done))
        self._dispatch()

    def _dispatch(self) -> None:
        if self.busy or not self.pending or self._stopping:
            return
        now = time.time()
        for p in [p for p in self.pending if p.stale_after and now - p.moment > p.stale_after]:
            log.warning("[%s] dropping stale trigger %d", p.session.cfg.name, p.job.trigger.id)
            self._finish(p, ok=False)
        if not self.pending:
            return
        p = max(self.pending, key=lambda p: p.score)
        self.pending.remove(p)
        self.busy = p
        self.worker.submit(p.payload)

    def _job_dict(self, s: ChannelSession, j: ClipJob) -> dict:
        cfg = s.cfg
        t = j.trigger
        return {
            "id": f"{cfg.name}-{t.id}-{int(t.detected_at)}",
            "source": "live",
            "channel": cfg.name,
            "display_name": cfg.display_name,
            "credit_url": cfg.credit_url,
            "permission": cfg.permission,
            "segments": [(str(seg.path), seg.wall_start, seg.wall_end) for seg in j.segments],
            "start": j.start,
            "end": j.end,
            "trigger": dataclasses.asdict(t),
            "folder": str(clip_folder(self.cfg.output_dir, cfg.name, t.moment, t.ratio)),
            "layout": self.layout_for(cfg),
        }

    def layout_for(self, cfg: ChannelConfig | None) -> dict:
        lay = cfg.layout if cfg else None
        return {
            "enabled": self.cfg.layout.enabled,
            "output": list(self.cfg.layout.output),
            "facecam": list(lay.facecam) if lay and lay.facecam else None,
            "gameplay": list(lay.gameplay) if lay and lay.gameplay else None,
        }

    def _finish(self, p: Pending, ok: bool) -> None:
        if p.job:
            p.job.release()
            p.session.jobs.pop(p.job.trigger.id, None)
        if p in self.pending:
            self.pending.remove(p)
        if ok and p.session:
            p.session.clips += 1
        if ok:
            self.clips_total += 1

    def _index_clip(self, folder: str) -> None:
        if not self.db:
            return
        try:
            path = Path(folder)
            meta = json.loads((path / "meta.json").read_text())
            self.db.upsert_clip_from_meta(str(path.relative_to(self.cfg.output_dir)), meta)
        except Exception:  # noqa: BLE001 - indexing must never break clipping
            log.exception("could not index clip %s", folder)

    async def _results_loop(self) -> None:
        while True:
            try:
                msg = await asyncio.to_thread(self.worker.get, 1.0)
            except queue.Empty:
                continue
            if msg.get("type") != "done":
                continue
            p, self.busy = self.busy, None
            if msg["ok"]:
                log.info("clip ready: %s (%s)", msg["folder"], msg["timings"])
                self._index_clip(msg["folder"])
                if p and p.job:
                    t = p.job.trigger
                    notify(f"{p.session.cfg.display_name} {t.ratio:.1f}x spike", subtitle=msg.get("title", ""))
            else:
                log.error("clip failed: %s\n%s", msg["error"], msg.get("trace", ""))
            if p:
                self._finish(p, ok=msg["ok"])
                if p.on_done:
                    try:
                        p.on_done(msg)
                    except Exception:  # noqa: BLE001
                        log.exception("on_done callback failed")
            self._dispatch()

    async def _status_loop(self) -> None:
        """One log line every 5 minutes, so a run without a terminal can still be checked."""
        while True:
            await asyncio.sleep(STATUS_EVERY)
            parts = []
            for name, s in self.sessions.items():
                parts.append(f"{name}: buffer {s.recorder.buffered_seconds / 60:.1f}m, "
                             f"{len(s.recorder.segments)} segs, restarts {s.recorder.stats.restarts}, "
                             f"score {s.detector.last_score:.0f}/{s.detector.last_baseline:.0f}, "
                             f"clips {s.clips}, dropped {s.dropped}")
            log.info("status: clips %d, queue %d, chat %s, reconnects %d | %s", self.clips_total,
                     len(self.pending) + (1 if self.busy else 0),
                     "up" if self.chat.connected.is_set() else "DOWN", self.chat.reconnects,
                     "; ".join(parts) or "no live channels")

    async def _tick_loop(self) -> None:
        while True:
            now = time.time()
            for s in list(self.sessions.values()):
                s.detector.chat_state(self.chat.connected.is_set(), self.chat.last_rx, now)
                self._handle_triggers(s, s.detector.tick(now))
                if s.detector.next_bin != s.last_bin:
                    s.last_bin = s.detector.next_bin
                    s.history.append((round(now), round(s.detector.last_score, 1),
                                      round(s.detector.last_baseline, 1)))
            await asyncio.sleep(TICK_SECONDS)

    # ---- status (console + API) -------------------------------------

    def status(self) -> dict:
        now = time.time()
        chans = []
        for name, cfg in self.channels.items():
            s = self.sessions.get(name)
            row = {"name": name, "display_name": cfg.display_name, "platform": cfg.platform,
                   "live": s is not None}
            if s:
                d = s.detector
                lt = s.last_trigger
                row.update({
                    "since": s.started,
                    "warmup": now < s.started + d.det.warmup_minutes * 60,
                    "buffer_seconds": round(s.recorder.buffered_seconds, 1),
                    "restarts": s.recorder.stats.restarts,
                    "score": round(d.last_score, 1),
                    "baseline": round(d.last_baseline, 1),
                    "mad": round(d.last_mad, 1),
                    "threshold": round(max(d.det.ratio_threshold * d.last_baseline,
                                           d.last_baseline + d.det.z_threshold * d.last_mad), 1),
                    "ratio_threshold": d.det.ratio_threshold,
                    "paused": d.pause_until > now,
                    "clips": s.clips,
                    "dropped": s.dropped,
                    "last_trigger": {"moment": lt.moment, "ratio": round(lt.ratio, 2), "final": lt.final}
                    if lt else None,
                    "history": list(s.history),
                })
            chans.append(row)
        busy = self.busy.payload if self.busy else None
        return {
            "running": bool(self.started_at) and not self._stopping,
            "started_at": self.started_at,
            "chat": {"connected": self.chat.connected.is_set(), "reconnects": self.chat.reconnects},
            "queue": len(self.pending) + (1 if self.busy else 0),
            "working_on": {"channel": busy.get("channel"), "source": busy.get("source")} if busy else None,
            "clips_this_run": self.clips_total,
            "channels": chans,
        }

    def table(self) -> Table:
        tb = Table(title=f"GTA 6 clipper · {datetime.now():%H:%M:%S} · clips {self.clips_total} · "
                         f"queue {len(self.pending) + (1 if self.busy else 0)}"
                         f" · chat {'up' if self.chat.connected.is_set() else 'DOWN'}")
        for col in ("channel", "live", "buffer", "score now", "baseline", "last trigger", "clips", "dropped"):
            tb.add_column(col, justify="right" if col not in ("channel", "last trigger") else "left")
        for name in self.channels:
            s = self.sessions.get(name)
            if not s:
                tb.add_row(name, "–", "", "", "", "", "", "")
                continue
            d = s.detector
            warm = time.time() < s.started + d.det.warmup_minutes * 60
            lt = s.last_trigger
            last = (f"{datetime.fromtimestamp(lt.moment):%H:%M:%S} {lt.ratio:.1f}x"
                    f"{'' if lt.final else ' …'}") if lt else ""
            tb.add_row(name, "LIVE" + (" (warm-up)" if warm else ""),
                       f"{s.recorder.buffered_seconds / 60:.1f}m", f"{d.last_score:.0f}",
                       f"{d.last_baseline:.0f}±{d.last_mad:.0f}", last, str(s.clips), str(s.dropped))
        return tb

    # ---- lifecycle --------------------------------------------------

    async def start(self, live: bool = True) -> None:
        """Start the worker and (if live) chat, live watcher and detector loops."""
        write_index(self.cfg.output_dir)
        self.worker.start()
        self.tasks = [asyncio.create_task(self._results_loop(), name="results")]
        if live:
            for name in self.channels:
                await self.chat.join(name)
            self.tasks += [
                asyncio.create_task(self.chat.run(), name="chat"),
                asyncio.create_task(self.watcher.run(), name="live-watch"),
                asyncio.create_task(self._tick_loop(), name="tick"),
                asyncio.create_task(self._status_loop(), name="status"),
            ]
        for t in self.tasks:
            t.add_done_callback(self._task_died)
        self.started_at = time.time()

    async def run(self) -> None:
        """Command-line mode: console table until Ctrl+C / SIGTERM."""
        log.info("review page: %s", self.cfg.output_dir / "index.html")
        await self.start()
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()

        # Own the signals: a process started in the background can inherit "ignore SIGINT",
        # and `kill` sends SIGTERM. A second signal forces an exit.
        def on_signal(sig: signal.Signals) -> None:
            if stop.is_set():
                log.warning("second %s: exiting now", sig.name)
                os._exit(130)
            log.info("%s received", sig.name)
            stop.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, on_signal, sig)
        try:
            with Live(self.table(), console=self.console, refresh_per_second=1) as live:
                while not stop.is_set():
                    live.update(self.table())
                    for t in self.tasks:
                        if t.done() and not t.cancelled() and t.exception():
                            raise t.exception()
                    try:
                        await asyncio.wait_for(stop.wait(), 2)
                    except asyncio.TimeoutError:
                        pass
        finally:
            await self.stop()

    async def stop(self) -> None:
        self._stopping = True
        log.info("shutting down: stopping recorders, finishing current clip")
        for t in self.tasks:
            if t.get_name() in ("live-watch", "tick"):
                t.cancel()
        for s in self.sessions.values():
            await s.recorder.stop()
            if s.rec_task:
                s.rec_task.cancel()
        deadline = time.time() + 120
        while self.busy and time.time() < deadline:
            await asyncio.sleep(0.5)
        if self.pending:
            log.info("discarding %d queued clips", len(self.pending))
        self.chat.stop()
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.worker.stop()
        log.info("stopped")
