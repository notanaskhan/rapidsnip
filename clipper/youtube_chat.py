"""YouTube live chat for one channel, read through yt-dlp's live_chat downloader.

yt-dlp runs as its own process (`--write-subs --sub-langs live_chat`) and appends one JSON
action per line to a .live_chat.json(.part) file as YouTube hands out chat. This tails that
file and turns actions into the same ChatMessage / ChatEvent items the Twitch reader emits,
so the detector, logs and replay work unchanged.

Running yt-dlp as a separate program also keeps it clear of Streamlink's urllib3 patch.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Callable

from .chat import ChatEvent, ChatItem, ChatLog, ChatMessage

log = logging.getLogger(__name__)

POLL = 0.5
RESTART_DELAY = 5
MAX_RESTART_DELAY = 60
GIFT_COUNT_RE = re.compile(r"(\d+)")


def _text(runs: list[dict]) -> str:
    out = []
    for r in runs or []:
        if "text" in r:
            out.append(r["text"])
        elif "emoji" in r:
            e = r["emoji"]
            eid = e.get("emojiId", "")
            # Standard emoji carry the character itself as their id; custom ones use :shortcuts:.
            out.append(eid if eid and len(eid) <= 8 else (e.get("shortcuts") or [""])[0])
    return "".join(out)


def _login(renderer: dict) -> str:
    name = (renderer.get("authorName") or {}).get("simpleText", "")
    return name.lstrip("@").strip().lower()


def parse_line(line: str, channel: str, recv_ts: float) -> list[ChatItem]:
    """One line of yt-dlp's live_chat JSON -> chat items (usually one)."""
    try:
        obj = json.loads(line)
    except ValueError:
        return []
    actions = (obj.get("replayChatItemAction") or {}).get("actions") or [obj]
    items: list[ChatItem] = []
    for a in actions:
        item = (a.get("addChatItemAction") or {}).get("item") or {}
        for kind, r in item.items():
            ts = int(r.get("timestampUsec", 0)) / 1e6 or recv_ts
            user = r.get("authorExternalChannelId", "")
            if kind == "liveChatTextMessageRenderer":
                items.append(ChatMessage(channel, user, _login(r), _text((r.get("message") or {}).get("runs")), ts, recv_ts))
            elif kind == "liveChatPaidMessageRenderer":           # Super Chat: a message with money on it
                items.append(ChatMessage(channel, user, _login(r), _text((r.get("message") or {}).get("runs")), ts, recv_ts))
                items.append(ChatEvent(channel, "superchat", ts, _login(r), {}, recv_ts))
            elif kind == "liveChatMembershipItemRenderer":        # new member / milestone ~ Twitch sub
                items.append(ChatEvent(channel, "sub", ts, _login(r), {}, recv_ts))
            elif kind == "liveChatSponsorshipsGiftPurchaseAnnouncementRenderer":   # gifted memberships
                header = (r.get("header") or {}).get("liveChatSponsorshipsHeaderRenderer") or {}
                m = GIFT_COUNT_RE.search(_text((header.get("primaryText") or {}).get("runs")))
                items.append(ChatEvent(channel, "submysterygift", ts, _login(header) or _login(r),
                                       {"mass-gift-count": m.group(1) if m else "1"}, recv_ts))
    return items


class YouTubeChat:
    """Live chat of one YouTube channel while it's live. `run()` as a task, `stop()` to end."""

    def __init__(self, channel: str, url: str, work_dir: Path, log_dir: Path | None,
                 on_item: Callable[[ChatItem], None] | None, ytdlp: str):
        self.channel = channel
        self.url = url
        self.work_dir = Path(work_dir)
        self.log = ChatLog(log_dir) if log_dir else None
        self.on_item = on_item
        self.ytdlp = ytdlp
        self.proc: asyncio.subprocess.Process | None = None
        self.last_rx = time.time()
        self.messages = 0
        self.restarts = 0
        self._stop = False

    @property
    def connected(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    def stop(self) -> None:
        self._stop = True
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()

    async def run(self) -> None:
        delay = RESTART_DELAY
        while not self._stop:
            started = time.time()
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - log and retry; chat must not take the recorder down
                log.exception("[%s] youtube chat crashed", self.channel)
            if self._stop:
                break
            delay = RESTART_DELAY if time.time() - started > 120 else min(delay * 2, MAX_RESTART_DELAY)
            self.restarts += 1
            log.warning("[%s] youtube chat ended; restarting in %ss", self.channel, delay)
            await asyncio.sleep(delay)
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
        if self.log:
            self.log.close()

    async def _session(self) -> None:
        folder = self.work_dir / time.strftime("%Y%m%d-%H%M%S")
        folder.mkdir(parents=True, exist_ok=True)
        err = open(folder / "yt-dlp.log", "ab")
        try:
            self.proc = await asyncio.create_subprocess_exec(
                self.ytdlp, "--skip-download", "--write-subs", "--sub-langs", "live_chat",
                "--compat-options", "prefer-legacy-http-handler", "--no-warnings", "--quiet",
                "-o", str(folder / "chat.%(ext)s"), self.url,
                stdout=asyncio.subprocess.DEVNULL, stderr=err, start_new_session=True,
            )
        finally:
            err.close()
        log.info("[%s] youtube chat started", self.channel)
        tail = asyncio.create_task(self._tail(folder))
        try:
            await self.proc.wait()
        finally:
            await asyncio.sleep(POLL * 2)       # read what was written last
            tail.cancel()
            await asyncio.gather(tail, return_exceptions=True)

    async def _tail(self, folder: Path) -> None:
        pos, buf, path = 0, "", None
        while True:
            await asyncio.sleep(POLL)
            if path is None or not path.exists():
                found = sorted(folder.glob("chat.live_chat.json*"))
                if not found:
                    continue
                if path is not None and found[0] != path:
                    pos = 0                       # .part was renamed when the stream ended
                path = found[0]
            with path.open(encoding="utf-8", errors="replace") as f:
                f.seek(pos)
                chunk = f.read()
                pos = f.tell()
            if not chunk:
                continue
            buf += chunk
            *lines, buf = buf.split("\n")
            now = time.time()
            for line in lines:
                if not line.strip():
                    continue
                self.last_rx = now
                for item in parse_line(line, self.channel, now):
                    self.messages += isinstance(item, ChatMessage)
                    if self.log:
                        self.log.write(item)
                    if self.on_item:
                        self.on_item(item)
