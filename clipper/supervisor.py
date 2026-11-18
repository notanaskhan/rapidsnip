"""Runs everything: live watcher, shared chat, one ChannelSession per live channel, the worker."""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import queue
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime

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
STALE_SECONDS = 15 * 60      # drop jobs whose moment is older than this


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


@dataclass
class Pending:
    job: ClipJob
    session: ChannelSession


class Supervisor:
    def __init__(self, cfg: Config, console: Console | None = None):
        self.cfg = cfg
        self.console = console or Console()
        self.channels = {c.name: c for c in cfg.channels if c.allowed}
        self.sessions: dict[str, ChannelSession] = {}
        self.chat = TwitchChat(cfg.data_dir / "chat", on_item=self._on_chat)
        self.watcher = LiveWatcher(list(self.channels), cfg.poll_live_seconds,
                                   self._on_live, self._on_offline)
        self.worker = WorkerHandle(dataclasses.asdict(cfg.captions))
        self.pending: list[Pending] = []
        self.busy: Pending | None = None
        self.clips_total = 0
        self._stopping = False

    # ---- chat -------------------------------------------------------

    def _on_chat(self, item: ChatItem) -> None:
        s = self.sessions.get(item.channel)
        if s:
            s.detector.feed(item)

    # ---- channel sessions -------------------------------------------

    async def _on_live(self, name: str) -> None:
        if self._stopping or name in self.sessions:
            return
        cfg = self.channels[name]
        rec = Recorder(name, self.cfg.data_dir, self.cfg.quality, self.cfg.buffer_minutes)
        det = Detector(name, cfg.detector, cfg.clip, started_at=time.time())
        s = ChannelSession(cfg, rec, det)
        s.rec_task = asyncio.create_task(rec.run(), name=f"rec-{name}")
        s.rec_task.add_done_callback(self._task_died)
        self.sessions[name] = s
        log.info("[%s] session started", name)

    async def _on_offline(self, name: str) -> None:
        s = self.sessions.get(name)
        if not s:
            return
        log.info("[%s] stream ended; finishing open clips", name)
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
        self.pending.append(Pending(job, s))
        self._dispatch()

    def _dispatch(self) -> None:
        if self.busy or not self.pending or self._stopping:
            return
        now = time.time()
        for p in [p for p in self.pending if now - p.job.trigger.moment > STALE_SECONDS]:
            log.warning("[%s] dropping stale trigger %d", p.session.cfg.name, p.job.trigger.id)
            self._finish(p, ok=False)
        if not self.pending:
            return
        p = max(self.pending, key=lambda p: p.job.score)    # highest ratio first
        self.pending.remove(p)
        self.busy = p
        self.worker.submit(self._job_dict(p))

    def _job_dict(self, p: Pending) -> dict:
        j, cfg = p.job, p.session.cfg
        t = j.trigger
        return {
            "id": f"{cfg.name}-{t.id}-{int(t.detected_at)}",
            "channel": cfg.name,
            "display_name": cfg.display_name,
            "credit_url": cfg.credit_url,
            "permission": cfg.permission,
            "segments": [(str(s.path), s.wall_start, s.wall_end) for s in j.segments],
            "start": j.start,
            "end": j.end,
            "trigger": dataclasses.asdict(t),
            "folder": str(clip_folder(self.cfg.output_dir, cfg.name, t.moment, t.ratio)),
            "layout": {
                "enabled": self.cfg.layout.enabled,
                "output": list(self.cfg.layout.output),
                "facecam": list(cfg.layout.facecam) if cfg.layout.facecam else None,
                "gameplay": list(cfg.layout.gameplay) if cfg.layout.gameplay else None,
            },
        }

    def _finish(self, p: Pending, ok: bool) -> None:
        p.job.release()
        p.session.jobs.pop(p.job.trigger.id, None)
        if p in self.pending:
            self.pending.remove(p)
        if ok:
            p.session.clips += 1
            self.clips_total += 1

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
                if p:
                    t = p.job.trigger
                    notify(f"{p.session.cfg.display_name} {t.ratio:.1f}x spike", subtitle=msg.get("title", ""))
            else:
                log.error("clip failed: %s\n%s", msg["error"], msg.get("trace", ""))
            if p:
                self._finish(p, ok=msg["ok"])
            self._dispatch()

    async def _tick_loop(self) -> None:
        while True:
            now = time.time()
            for s in list(self.sessions.values()):
                self._handle_triggers(s, s.detector.tick(now))
            await asyncio.sleep(TICK_SECONDS)

    # ---- console ----------------------------------------------------

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

    async def run(self) -> None:
        write_index(self.cfg.output_dir)
        log.info("review page: %s", self.cfg.output_dir / "index.html")
        self.worker.start()
        for name in self.channels:
            await self.chat.join(name)
        tasks = [
            asyncio.create_task(self.chat.run(), name="chat"),
            asyncio.create_task(self.watcher.run(), name="live-watch"),
            asyncio.create_task(self._tick_loop(), name="tick"),
            asyncio.create_task(self._results_loop(), name="results"),
        ]
        # Own the signals: a process started in the background can inherit "ignore SIGINT",
        # and `kill` sends SIGTERM. A second signal forces an exit.
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()

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
                    for t in tasks:
                        if t.done() and not t.cancelled() and t.exception():
                            raise t.exception()
                    try:
                        await asyncio.wait_for(stop.wait(), 2)
                    except asyncio.TimeoutError:
                        pass
        finally:
            await self.shutdown(tasks)

    async def shutdown(self, tasks: list[asyncio.Task]) -> None:
        self._stopping = True
        log.info("shutting down: stopping recorders, finishing current clip")
        for t in tasks[1:3]:          # live watcher, detector ticks
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
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.worker.stop()
        log.info("stopped")
