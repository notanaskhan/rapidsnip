"""Rolling video buffer: Streamlink -> FFmpeg segmenter -> in-memory segment index.

Segments are 2 s mpegts files in data/<channel>/<session>/. Each one gets wall-clock
times (Unix seconds) so chat triggers can be mapped onto video.

Wall times: FFmpeg's segment list gives each segment's position on the stream's own
timeline. A session "anchor" maps that timeline onto wall time with
offset = min(arrival_time - stream_end) over the session, i.e. the least-delayed
arrival seen so far. This keeps segments contiguous even when Streamlink delivers
a burst of old segments at startup or after a network stall.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import live_url, streamlink_args

log = logging.getLogger(__name__)

SEGMENT_SECONDS = 2
CLEANUP_EVERY = 30
RESTART_DELAY = 2            # seconds before restarting a dead pipeline
REANCHOR_SLIP = 5            # re-anchor if this many seconds behind for REANCHOR_COUNT segments
REANCHOR_COUNT = 10
POLL_INDEX = 0.25


def _tool(name: str) -> str:
    """Prefer the copy installed next to this Python (the uv venv), so PATH doesn't matter."""
    local = Path(sys.executable).parent / name
    found = local if local.exists() else shutil.which(name)
    if not found:
        raise FileNotFoundError(f"{name} not found (looked in {local.parent} and PATH)")
    return str(found)


@dataclass
class Anchor:
    offset: float            # wall = stream_time + offset


@dataclass
class Segment:
    path: Path
    stream_start: float
    stream_end: float
    anchor: Anchor
    session: str

    @property
    def wall_start(self) -> float:
        return self.stream_start + self.anchor.offset

    @property
    def wall_end(self) -> float:
        return self.stream_end + self.anchor.offset

    @property
    def duration(self) -> float:
        return self.stream_end - self.stream_start


@dataclass
class Lock:
    start: float
    end: float


@dataclass
class RecorderStats:
    restarts: int = 0
    deleted: int = 0
    sessions: list[str] = field(default_factory=list)


class Recorder:
    def __init__(self, channel: str, data_dir: Path, quality: str = "best",
                 buffer_minutes: float = 10, platform: str = "twitch"):
        self.channel = channel
        self.platform = platform
        self.root = Path(data_dir) / channel
        self.quality = quality
        self.buffer_seconds = buffer_minutes * 60
        self.segments: list[Segment] = []
        self.stats = RecorderStats()
        self._lock_ranges: dict[int, Lock] = {}
        self._next_lock = 0
        self._procs: list[asyncio.subprocess.Process] = []
        self._stop = False
        self._new_segment = asyncio.Event()

    # ---- public API -------------------------------------------------

    @property
    def buffered_seconds(self) -> float:
        if not self.segments:
            return 0.0
        return self.segments[-1].wall_end - self.segments[0].wall_start

    @property
    def latest_wall_end(self) -> float:
        return self.segments[-1].wall_end if self.segments else 0.0

    def segments_between(self, start: float, end: float) -> list[Segment]:
        return [s for s in self.segments if s.wall_end > start and s.wall_start < end]

    def lock(self, start: float, end: float) -> int:
        """Protect [start, end] from cleanup (also segments not yet recorded)."""
        self._next_lock += 1
        self._lock_ranges[self._next_lock] = Lock(start, end)
        return self._next_lock

    def extend_lock(self, token: int, end: float) -> None:
        if token in self._lock_ranges:
            self._lock_ranges[token].end = max(self._lock_ranges[token].end, end)

    def unlock(self, token: int) -> None:
        self._lock_ranges.pop(token, None)

    async def wait_for_segment(self, timeout: float) -> bool:
        """Wait until a new segment lands. Returns False on timeout."""
        self._new_segment.clear()
        try:
            await asyncio.wait_for(self._new_segment.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def kill_streamlink(self) -> None:
        """For testing restarts."""
        if self._procs:
            self._procs[0].kill()

    async def stop(self) -> None:
        self._stop = True
        await self._terminate()

    def purge_stale(self) -> None:
        """Delete session folders left by earlier runs (nothing in memory refers to them)."""
        if not self.root.exists():
            return
        mine = {s.session for s in self.segments} | set(self.stats.sessions)
        for d in self.root.iterdir():
            if d.is_dir() and d.name not in mine:
                shutil.rmtree(d, ignore_errors=True)

    async def run(self) -> None:
        self.purge_stale()
        cleanup = asyncio.create_task(self._cleanup_loop())
        try:
            quick_failures = 0
            while not self._stop:
                started = time.time()
                try:
                    await self._session()
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - log and restart, never die silently
                    log.exception("[%s] recorder session crashed", self.channel)
                if self._stop:
                    break
                quick_failures = quick_failures + 1 if time.time() - started < 15 else 0
                delay = RESTART_DELAY if quick_failures < 3 else 15
                self.stats.restarts += 1
                log.warning("[%s] recorder pipeline exited; restarting in %ss", self.channel, delay)
                await asyncio.sleep(delay)
        finally:
            cleanup.cancel()
            await self._terminate()

    # ---- pipeline ---------------------------------------------------

    async def _session(self) -> None:
        session = time.strftime("%Y%m%d-%H%M%S")
        folder = self.root / session
        folder.mkdir(parents=True, exist_ok=True)
        self.stats.sessions.append(session)
        index = folder / "index.csv"

        url = live_url(self.platform, self.channel)
        r, w = os.pipe()
        sl_log = open(folder / "streamlink.log", "ab")
        ff_log = open(folder / "ffmpeg.log", "ab")
        try:
            streamlink = await asyncio.create_subprocess_exec(
                _tool("streamlink"), *streamlink_args(self.platform),
                "--loglevel", "warning", "--stdout", url, self.quality,
                stdout=w, stderr=sl_log, start_new_session=True,
            )
            ffmpeg = await asyncio.create_subprocess_exec(
                _tool("ffmpeg"), "-hide_banner", "-loglevel", "warning", "-nostdin",
                "-i", "pipe:0", "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                "-f", "segment", "-segment_time", str(SEGMENT_SECONDS), "-reset_timestamps", "1",
                "-segment_format", "mpegts", "-segment_list", str(index),
                "-segment_list_type", "csv", str(folder / "seg_%06d.ts"),
                stdin=r, stdout=asyncio.subprocess.DEVNULL, stderr=ff_log, start_new_session=True,
            )
        finally:
            os.close(r)
            os.close(w)
            sl_log.close()
            ff_log.close()
        self._procs = [streamlink, ffmpeg]
        log.info("[%s] recording session %s", self.channel, session)

        tail = asyncio.create_task(self._tail_index(index, folder, session))
        waits = [asyncio.create_task(p.wait()) for p in self._procs]
        await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
        # One died (or stop()): give ffmpeg a moment to flush the last segment row, then end both.
        await asyncio.sleep(0.5)
        await self._terminate()
        await asyncio.sleep(POLL_INDEX * 2)
        tail.cancel()
        await asyncio.gather(tail, return_exceptions=True)
        codes = [p.returncode for p in self._procs]
        log.info("[%s] session %s ended (streamlink=%s ffmpeg=%s)", self.channel, session, *codes)
        self._procs = []

    async def _terminate(self) -> None:
        for p in self._procs:
            if p.returncode is None:
                try:
                    p.terminate()
                except ProcessLookupError:
                    pass
        for p in self._procs:
            try:
                await asyncio.wait_for(p.wait(), 5)
            except asyncio.TimeoutError:
                p.kill()
                await p.wait()

    async def _tail_index(self, index: Path, folder: Path, session: str) -> None:
        anchor: Anchor | None = None
        slips = 0
        pos = 0
        buf = ""
        while True:
            await asyncio.sleep(POLL_INDEX)
            if not index.exists():
                continue
            with index.open() as f:
                f.seek(pos)
                chunk = f.read()
                pos = f.tell()
            if not chunk:
                continue
            buf += chunk
            *lines, buf = buf.split("\n")
            now = time.time()
            for line in lines:
                parts = line.strip().split(",")
                if len(parts) < 3:
                    continue
                name, s_start, s_end = parts[0], float(parts[1]), float(parts[2])
                offset = now - s_end
                if anchor is None:
                    anchor = Anchor(offset)
                elif offset < anchor.offset:
                    anchor.offset = offset          # less-delayed arrival: refine the whole session
                elif offset > anchor.offset + REANCHOR_SLIP:
                    slips += 1
                    if slips >= REANCHOR_COUNT:     # stream timeline jumped back; start a new anchor
                        log.warning("[%s] re-anchoring stream clock (+%.1fs)",
                                    self.channel, offset - anchor.offset)
                        anchor = Anchor(offset)
                        slips = 0
                else:
                    slips = 0
                self.segments.append(Segment(folder / name, s_start, s_end, anchor, session))
            self._new_segment.set()

    # ---- cleanup ----------------------------------------------------

    def _locked(self, seg: Segment) -> bool:
        return any(seg.wall_end > lk.start and seg.wall_start < lk.end
                   for lk in self._lock_ranges.values())

    def cleanup(self, now: float | None = None) -> int:
        now = now or time.time()
        cutoff = now - self.buffer_seconds
        keep, removed = [], 0
        for seg in self.segments:
            if seg.wall_end < cutoff and not self._locked(seg):
                try:
                    seg.path.unlink()
                except FileNotFoundError:
                    pass
                removed += 1
            else:
                keep.append(seg)
        self.segments = keep
        self.stats.deleted += removed
        self._remove_dead_sessions()
        return removed

    def _remove_dead_sessions(self) -> None:
        live = {s.session for s in self.segments}
        if self.stats.sessions:
            live.add(self.stats.sessions[-1])
        if not self.root.exists():
            return
        for d in self.root.iterdir():
            if d.is_dir() and d.name not in live and not any(d.glob("seg_*.ts")):
                shutil.rmtree(d, ignore_errors=True)

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(CLEANUP_EVERY)
            n = self.cleanup()
            if n:
                log.debug("[%s] deleted %d old segments", self.channel, n)
