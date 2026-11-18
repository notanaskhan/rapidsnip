"""Chat-spike detector. Pure logic: feed() chat items, tick(now) to get triggers.

Times are Unix seconds on Twitch's clock (tmi-sent-ts). No sockets, files or
implicit clocks, so the same code runs live and in tools/replay.py.

Lifecycle of a trigger: it is emitted as soon as a window qualifies (final=False),
re-emitted when later hot windows extend it, and emitted once more with
final=True when chat has been quiet for merge_gap_seconds or the clip hits
max_length_seconds. Each emission carries the same `id`.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter, deque
from dataclasses import dataclass, field

from .chat import ChatEvent, ChatItem, ChatMessage
from .config import ClipConfig, DetectorConfig

KNOWN_BOTS = {"nightbot", "streamelements", "streamlabs", "moobot", "fossabot", "sery_bot"}
MASS_GIFT_EVENTS = {"submysterygift", "anonsubmysterygift"}
MASS_GIFT_PAUSE = 10         # mystery gifts this big pause detection; small ones are routine
SUB_EVENTS = {"sub", "resub", "subgift", "anonsubgift"}
SUB_BURST_COUNT = 5
SUB_BURST_SECONDS = 30
ONSET_FACTOR = 1.5
ONSET_Z = 3.0                # a bin must also beat the per-bin median by this many MADs
ONSET_MAX_LOOKBACK = 30      # seconds before the trigger window to search for onset
SETTLE_SECONDS = 1.0         # evaluate a window only once its last bin is this far in the past
TOP_MESSAGES = 10


def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


@dataclass
class Bin:
    messages: int = 0
    chatters: set[str] = field(default_factory=set)
    reaction_chatters: set[str] = field(default_factory=set)
    texts: list[str] = field(default_factory=list)
    reactions: Counter = field(default_factory=Counter)

    @property
    def reaction_hits(self) -> int:
        return len(self.reaction_chatters)


@dataclass
class Trigger:
    channel: str
    id: int
    moment: float            # estimated time of the action on stream (wall clock)
    start: float
    end: float
    onset: float             # start of the chat spike
    detected_at: float       # end of the window that fired
    peak_score: float
    baseline: float
    ratio: float
    top_messages: list[tuple[str, int]]
    reaction_counts: dict[str, int]
    final: bool = False


@dataclass
class _Open:
    trigger: Trigger
    last_hot: float
    bins: list[int]


class ReactionMatcher:
    def __init__(self, reactions: list[str]):
        self.words: dict[str, str] = {}
        self.phrases: list[tuple[re.Pattern, str]] = []
        self.symbols: list[str] = []
        for r in reactions:
            if " " in r.strip():
                pat = re.compile(r"(?<!\w)" + r"\s+".join(map(re.escape, r.split())) + r"(?!\w)", re.I)
                self.phrases.append((pat, r))
            elif re.fullmatch(r"\w+", r):
                self.words[r.lower()] = r
            else:
                self.symbols.append(r)

    def find(self, text: str) -> set[str]:
        found = set()
        for tok in re.findall(r"\w+", text.lower()):
            if tok in self.words:
                found.add(self.words[tok])
        for pat, name in self.phrases:
            if pat.search(text):
                found.add(name)
        for s in self.symbols:
            if s in text:
                found.add(s)
        return found


class Detector:
    def __init__(self, channel: str, det: DetectorConfig, clip: ClipConfig, started_at: float):
        self.channel = channel
        self.det = det
        self.clip = clip
        self.started_at = started_at
        self.step = det.step_seconds
        self.bins_per_window = max(1, round(det.window_seconds / det.step_seconds))
        self.bots = KNOWN_BOTS | {b.lower() for b in det.extra_bots}
        self.matcher = ReactionMatcher(det.reactions)
        self.bins: dict[int, Bin] = {}
        self.scores: deque[tuple[float, float, bool]] = deque()   # (window_end, score, excluded)
        self.bin_scores: deque[tuple[float, float]] = deque()     # (bin_end, score) of non-excluded bins
        self.pause_until = 0.0
        self.sub_times: deque[float] = deque()
        self.open: _Open | None = None
        self.last_hot_end = -1e18     # last hot window of the most recent trigger
        self.next_id = 1
        self.next_bin = int(started_at // self.step) + 1    # first bin index to close
        # Exposed for the console
        self.last_score = 0.0
        self.last_baseline = 1.0
        self.last_mad = 0.0

    # ---- input ------------------------------------------------------

    def feed(self, item: ChatItem) -> None:
        if isinstance(item, ChatEvent):
            self._event(item)
            return
        if not isinstance(item, ChatMessage):
            return
        if item.login in self.bots or item.text.lstrip().startswith("!"):
            return
        b = self.bins.setdefault(int(item.ts // self.step), Bin())
        b.messages += 1
        b.chatters.add(item.login)
        b.texts.append(item.text.strip())
        found = self.matcher.find(item.text)
        if found:
            b.reaction_chatters.add(item.login)
            b.reactions.update(found)

    def _event(self, ev: ChatEvent) -> None:
        if ev.kind == "raid" or (
                ev.kind in MASS_GIFT_EVENTS
                and _int(ev.params.get("mass-gift-count")) >= MASS_GIFT_PAUSE):
            self.pause_until = max(self.pause_until, ev.ts + self.det.pause_after_event_seconds)
        elif ev.kind in SUB_EVENTS and not ev.params.get("community-gift-id"):
            # Gifts that belong to a mystery gift are judged by the mystery gift's size above.
            self.sub_times.append(ev.ts)
            while self.sub_times and self.sub_times[0] < ev.ts - SUB_BURST_SECONDS:
                self.sub_times.popleft()
            if len(self.sub_times) >= SUB_BURST_COUNT:
                self.pause_until = max(self.pause_until, ev.ts + self.det.pause_after_event_seconds)

    # ---- evaluation -------------------------------------------------

    def tick(self, now: float) -> list[Trigger]:
        """Evaluate every window that has fully closed by `now`. Returns new/updated triggers."""
        out: list[Trigger] = []
        last_closed = int((now - SETTLE_SECONDS) // self.step) - 1
        while self.next_bin <= last_closed:
            self._evaluate(self.next_bin, out)
            self.next_bin += 1
        self._prune()
        return out

    def flush(self) -> list[Trigger]:
        """Finalize any open trigger (end of stream / replay)."""
        if not self.open:
            return []
        t = self.open.trigger
        t.final = True
        self.open = None
        return [t]

    def window(self, last_bin: int) -> tuple[float, int, int, int]:
        """(score, unique_chatters, messages, reaction_hits) for the window ending with last_bin."""
        chatters: set[str] = set()
        msgs = hits = 0
        for i in range(last_bin - self.bins_per_window + 1, last_bin + 1):
            b = self.bins.get(i)
            if b:
                chatters |= b.chatters
                msgs += b.messages
                hits += b.reaction_hits
        share = min(1.0, hits / msgs) if msgs else 0.0
        return len(chatters) * (1 + share), len(chatters), msgs, hits

    def _bin_score(self, i: int) -> float:
        b = self.bins.get(i)
        if not b or not b.messages:
            return 0.0
        share = min(1.0, b.reaction_hits / b.messages)
        return len(b.chatters) * (1 + share)

    def _baseline(self) -> tuple[float, float]:
        vals = [s for _, s, excluded in self.scores if not excluded]
        if not vals:
            return 1.0, 0.0
        med = statistics.median(vals)
        mad = statistics.median(abs(v - med) for v in vals)
        return max(1.0, med), mad

    def _evaluate(self, last_bin: int, out: list[Trigger]) -> None:
        t_end = (last_bin + 1) * self.step
        score, chatters, _, _ = self.window(last_bin)
        baseline, mad = self._baseline()
        self.last_score, self.last_baseline, self.last_mad = score, baseline, mad

        paused = t_end < self.pause_until
        warm = t_end < self.started_at + self.det.warmup_minutes * 60
        above = (score >= self.det.ratio_threshold * baseline
                 and score >= baseline + self.det.z_threshold * mad)
        hot = above and not paused

        # Close an open trigger once chat has been quiet for merge_gap.
        if self.open and t_end - self.open.last_hot > self.clip.merge_gap_seconds:
            self._finalize(out)

        in_trigger = False
        if hot and self.open:
            self._extend(last_bin, t_end, score, out)
            in_trigger = True
        elif hot and not warm and chatters >= self.det.min_unique_chatters \
                and t_end - self.last_hot_end >= self.det.cooldown_seconds:
            self._fire(last_bin, t_end, score, baseline, out)
            in_trigger = True

        # Keep spikes and event floods out of the baseline, but never during warm-up,
        # or an empty starting baseline would mark every window as a spike.
        excluded = not warm and (in_trigger or paused)
        self.scores.append((t_end, score, excluded))
        if not excluded:
            self.bin_scores.append((t_end, self._bin_score(last_bin)))

    def _onset(self, last_bin: int, baseline: float) -> float:
        # Single 2 s bins are much noisier than 10 s windows, so judge them against their own
        # history: above 1.5x the per-bin median and median + 3 MAD.
        vals = [v for _, v in self.bin_scores]
        if vals:
            med = statistics.median(vals)
            mad = statistics.median(abs(v - med) for v in vals)
        else:
            med, mad = baseline / self.bins_per_window, 0.0
        med = max(med, 1.0 / self.bins_per_window)
        thresh = max(ONSET_FACTOR * med, med + ONSET_Z * mad)
        first = last_bin - self.bins_per_window + 1
        floor = first - int(ONSET_MAX_LOOKBACK / self.step)
        i = last_bin
        onset_bin = None
        while i >= floor:
            if self._bin_score(i) > thresh:
                onset_bin = i
            elif i < first:
                break                 # before the window, the run of hot bins must be unbroken
            i -= 1
        if onset_bin is None:
            onset_bin = first
        return onset_bin * self.step

    def _fire(self, last_bin: int, t_end: float, score: float, baseline: float,
              out: list[Trigger]) -> None:
        onset = self._onset(last_bin, baseline)
        moment = onset - self.clip.chat_delay_seconds
        start = moment - self.clip.pre_seconds
        end = max(moment + self.clip.post_seconds, t_end - self.clip.chat_delay_seconds)
        end = min(end, start + self.clip.max_length_seconds)
        trig = Trigger(
            channel=self.channel, id=self.next_id, moment=moment, start=start, end=end,
            onset=onset, detected_at=t_end, peak_score=score, baseline=baseline,
            ratio=score / baseline, top_messages=[], reaction_counts={},
        )
        self.next_id += 1
        first_bin = int(onset // self.step)
        self.open = _Open(trig, t_end, list(range(first_bin, last_bin + 1)))
        self.last_hot_end = t_end
        self._summarize()
        out.append(trig)

    def _extend(self, last_bin: int, t_end: float, score: float, out: list[Trigger]) -> None:
        o = self.open
        t = o.trigger
        o.last_hot = t_end
        self.last_hot_end = t_end
        if o.bins and last_bin > o.bins[-1]:
            o.bins.extend(range(o.bins[-1] + 1, last_bin + 1))
        new_end = min(t_end - self.clip.chat_delay_seconds, t.start + self.clip.max_length_seconds)
        changed = new_end > t.end or score > t.peak_score
        t.end = max(t.end, new_end)
        if score > t.peak_score:
            t.peak_score = score
            t.ratio = score / t.baseline
        if t.end >= t.start + self.clip.max_length_seconds:
            self._finalize(out)
            return
        if changed:
            self._summarize()
            out.append(t)

    def _finalize(self, out: list[Trigger]) -> None:
        self._summarize()
        self.open.trigger.final = True
        out.append(self.open.trigger)
        self.open = None

    def _summarize(self) -> None:
        o = self.open
        texts: Counter = Counter()
        reactions: Counter = Counter()
        for i in o.bins:
            b = self.bins.get(i)
            if b:
                texts.update(t for t in b.texts if t)
                reactions.update(b.reactions)
        o.trigger.top_messages = texts.most_common(TOP_MESSAGES)
        o.trigger.reaction_counts = dict(reactions.most_common())

    def _prune(self) -> None:
        horizon = self.next_bin * self.step - self.det.baseline_minutes * 60
        while self.scores and self.scores[0][0] < horizon:
            self.scores.popleft()
        while self.bin_scores and self.bin_scores[0][0] < horizon:
            self.bin_scores.popleft()
        keep_from = int((horizon - ONSET_MAX_LOOKBACK - self.clip.max_length_seconds) // self.step)
        if self.open and self.open.bins:
            keep_from = min(keep_from, self.open.bins[0])
        for k in [k for k in self.bins if k < keep_from]:
            del self.bins[k]
