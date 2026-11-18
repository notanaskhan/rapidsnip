"""A trigger becomes a job that waits for its video to be recorded.

The job locks its time range in the recorder, follows the trigger's updates
(extensions), and is ready once the trigger is final and the recorder has
segments up to the clip end. It then checks how much of the window exists and
hands itself to the worker.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from .detector import Trigger
from .recorder import Recorder, Segment

log = logging.getLogger(__name__)

WAIT_TIMEOUT = 60            # seconds to wait for the recording once the trigger is final
FINAL_TIMEOUT = 120          # give up waiting for the detector to finalize
MAX_MISSING = 0.20
GAP_TOLERANCE = 0.5          # seconds between segments that still count as contiguous


@dataclass
class ClipJob:
    trigger: Trigger
    recorder: Recorder
    created_at: float = field(default_factory=time.time)
    segments: list[Segment] = field(default_factory=list)
    start: float = 0.0       # actual cut range (may be trimmed to the recorded run)
    end: float = 0.0
    dropped: str | None = None
    _lock: int | None = None
    _final: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self):
        self._lock = self.recorder.lock(self.trigger.start, self.trigger.end)
        if self.trigger.final:
            self._final.set()

    @property
    def score(self) -> float:
        return self.trigger.ratio

    def update(self, trigger: Trigger) -> None:
        """Called with each re-emission of the same trigger id."""
        self.trigger = trigger
        self.recorder.extend_lock(self._lock, trigger.end)
        if trigger.final:
            self._final.set()

    def release(self) -> None:
        if self._lock is not None:
            self.recorder.unlock(self._lock)
            self._lock = None

    async def wait_ready(self) -> bool:
        """Wait for the trigger to finish and the video to exist. False = dropped."""
        try:
            await asyncio.wait_for(self._final.wait(), FINAL_TIMEOUT)
        except asyncio.TimeoutError:
            log.warning("[%s] trigger %d never finalized; cutting what we have",
                        self.trigger.channel, self.trigger.id)
        deadline = time.time() + WAIT_TIMEOUT
        while self.recorder.latest_wall_end < self.trigger.end and time.time() < deadline:
            await self.recorder.wait_for_segment(timeout=max(0.1, deadline - time.time()))
        return self.select()

    def select(self) -> bool:
        """Pick the segments for the window. Returns False (and sets .dropped) if too much is missing."""
        t = self.trigger
        want = t.end - t.start
        segs = self.recorder.segments_between(t.start, t.end)
        # Concatenation only keeps time right within one contiguous run, so use the best run.
        runs: list[list[Segment]] = []
        for s in segs:
            if runs and abs(s.wall_start - runs[-1][-1].wall_end) <= GAP_TOLERANCE \
                    and s.session == runs[-1][-1].session:
                runs[-1].append(s)
            else:
                runs.append([s])

        def covered(run):
            return max(0.0, min(run[-1].wall_end, t.end) - max(run[0].wall_start, t.start))

        best = max(runs, key=covered, default=[])
        have = covered(best) if best else 0.0
        if have < want * (1 - MAX_MISSING):
            self.dropped = f"only {have:.1f}s of {want:.1f}s recorded"
            log.warning("[%s] dropping trigger %d: %s", t.channel, t.id, self.dropped)
            self.release()
            return False
        self.segments = best
        self.start = max(t.start, best[0].wall_start)
        self.end = min(t.end, best[-1].wall_end)
        return True
