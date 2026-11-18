"""Anonymous Twitch chat reader over IRC/TLS.

One connection serves every channel. Messages and events are appended to
data/chat/<channel>/<date>.jsonl and handed to an optional callback.

Run standalone to watch a channel:  uv run python -m clipper.chat <channel> [--seconds N]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import ssl
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, TextIO

log = logging.getLogger(__name__)

HOST = "irc.chat.twitch.tv"
PORT = 6697
JOIN_INTERVAL = 0.5          # 20 joins per 10 s
IDLE_PING_SECONDS = 30       # send our own PING after this long with no data
PONG_TIMEOUT = 10            # reconnect if nothing comes back within this
MAX_BACKOFF = 30


@dataclass
class ChatMessage:
    channel: str
    user_id: str
    login: str
    text: str
    ts: float                # tmi-sent-ts / 1000 (Twitch server time)
    recv_ts: float = 0.0     # local time.time() when received
    type: str = "msg"


@dataclass
class ChatEvent:
    channel: str
    kind: str                # USERNOTICE msg-id: raid, sub, resub, subgift, submysterygift, ...
    ts: float
    login: str = ""
    params: dict = field(default_factory=dict)   # msg-param-* tags, e.g. viewerCount, mass-gift-count
    recv_ts: float = 0.0
    type: str = "event"


ChatItem = ChatMessage | ChatEvent

_TAG_ESCAPES = {":": ";", "s": " ", "\\": "\\", "r": "\r", "n": "\n"}


def _unescape_tag(value: str) -> str:
    out = []
    i = 0
    while i < len(value):
        c = value[i]
        if c == "\\" and i + 1 < len(value):
            out.append(_TAG_ESCAPES.get(value[i + 1], value[i + 1]))
            i += 2
        else:
            if c != "\\":
                out.append(c)
            i += 1
    return "".join(out)


def parse_line(line: str) -> tuple[dict, str, str, list[str]]:
    """Split a raw IRC line into (tags, prefix, command, params). The trailing param is last."""
    tags: dict[str, str] = {}
    prefix = ""
    if line.startswith("@"):
        raw_tags, _, line = line[1:].partition(" ")
        for item in raw_tags.split(";"):
            k, _, v = item.partition("=")
            tags[k] = _unescape_tag(v)
    if line.startswith(":"):
        prefix, _, line = line[1:].partition(" ")
    if " :" in line:
        head, _, trailing = line.partition(" :")
        parts = head.split()
        parts.append(trailing)
    else:
        parts = line.split()
    command = parts[0] if parts else ""
    return tags, prefix, command, parts[1:]


def to_item(tags: dict, prefix: str, command: str, params: list[str], recv_ts: float) -> ChatItem | None:
    if command not in ("PRIVMSG", "USERNOTICE") or not params:
        return None
    channel = params[0].lstrip("#").lower()
    ts = int(tags["tmi-sent-ts"]) / 1000 if tags.get("tmi-sent-ts") else recv_ts
    if command == "PRIVMSG":
        text = params[-1] if len(params) > 1 else ""
        if text.startswith("\x01ACTION ") and text.endswith("\x01"):
            text = text[8:-1]
        login = prefix.split("!", 1)[0].lower()
        return ChatMessage(channel, tags.get("user-id", ""), login, text, ts, recv_ts)
    params_tags = {k[len("msg-param-"):]: v for k, v in tags.items() if k.startswith("msg-param-")}
    return ChatEvent(channel, tags.get("msg-id", ""), ts, tags.get("login", ""), params_tags, recv_ts)


class ChatLog:
    """Appends items to data/chat/<channel>/<YYYY-MM-DD>.jsonl (local date of the item)."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._files: dict[str, tuple[str, TextIO]] = {}

    def write(self, item: ChatItem) -> None:
        day = datetime.fromtimestamp(item.ts).strftime("%Y-%m-%d")
        cur = self._files.get(item.channel)
        if cur is None or cur[0] != day:
            if cur:
                cur[1].close()
            path = self.root / item.channel / f"{day}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            cur = (day, path.open("a", encoding="utf-8", buffering=1))
            self._files[item.channel] = cur
        cur[1].write(json.dumps(asdict(item), ensure_ascii=False) + "\n")

    def close(self) -> None:
        for _, f in self._files.values():
            f.close()
        self._files.clear()


class TwitchChat:
    """Shared anonymous chat connection. Use join()/part() while running; call run() as a task."""

    def __init__(self, log_dir: Path | None = None, on_item: Callable[[ChatItem], None] | None = None):
        self.channels: set[str] = set()
        self.on_item = on_item
        self.log = ChatLog(log_dir) if log_dir else None
        self.connected = asyncio.Event()
        self.reconnects = 0
        self._writer: asyncio.StreamWriter | None = None
        self._joined: set[str] = set()
        self._stop = False

    async def join(self, channel: str) -> None:
        channel = channel.lower()
        self.channels.add(channel)
        if self.connected.is_set() and channel not in self._joined:
            await self._send(f"JOIN #{channel}")
            self._joined.add(channel)

    async def part(self, channel: str) -> None:
        channel = channel.lower()
        self.channels.discard(channel)
        if self.connected.is_set() and channel in self._joined:
            await self._send(f"PART #{channel}")
        self._joined.discard(channel)

    def stop(self) -> None:
        self._stop = True
        if self._writer:
            self._writer.close()

    async def _send(self, line: str) -> None:
        if self._writer is None:
            return
        self._writer.write((line + "\r\n").encode())
        await self._writer.drain()

    async def run(self) -> None:
        backoff = 1
        while not self._stop:
            started = time.time()
            try:
                await self._session()
            except (OSError, asyncio.TimeoutError, ConnectionError) as e:
                if not self._stop:
                    log.warning("chat connection lost: %r", e)
            finally:
                self.connected.clear()
                self._joined.clear()
                if self._writer:
                    self._writer.close()
                    self._writer = None
            if self._stop:
                break
            if time.time() - started > 60:
                backoff = 1          # it was healthy for a while; retry fast
            self.reconnects += 1
            log.warning("chat reconnecting in %ss", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)
        if self.log:
            self.log.close()

    async def _session(self) -> None:
        ctx = ssl.create_default_context()
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(HOST, PORT, ssl=ctx), timeout=10
        )
        self._writer = writer
        await self._send("CAP REQ :twitch.tv/tags twitch.tv/commands")
        await self._send(f"NICK justinfan{random.randint(10000, 99999)}")
        self.connected.set()
        log.info("chat connected")
        join_task = asyncio.create_task(self._join_all())
        try:
            await self._read_loop(reader)
        finally:
            join_task.cancel()

    async def _join_all(self) -> None:
        for channel in sorted(self.channels):
            if channel not in self._joined and channel in self.channels:
                await self._send(f"JOIN #{channel}")
                self._joined.add(channel)
                await asyncio.sleep(JOIN_INTERVAL)

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        awaiting_pong = False
        while not self._stop:
            timeout = PONG_TIMEOUT if awaiting_pong else IDLE_PING_SECONDS
            try:
                raw = await asyncio.wait_for(reader.readline(), timeout=timeout)
            except asyncio.TimeoutError:
                if awaiting_pong:
                    raise ConnectionError("no PONG from server")
                await self._send("PING :clipper")
                awaiting_pong = True
                continue
            if not raw:
                raise ConnectionError("server closed connection")
            awaiting_pong = False
            recv_ts = time.time()
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            tags, prefix, command, params = parse_line(line)
            if command == "PING":
                await self._send("PONG :" + (params[-1] if params else "tmi.twitch.tv"))
            elif command == "RECONNECT":
                raise ConnectionError("server asked to reconnect")
            else:
                item = to_item(tags, prefix, command, params, recv_ts)
                if item is not None:
                    if self.log:
                        self.log.write(item)
                    if self.on_item:
                        self.on_item(item)


async def _main(channels: list[str], seconds: float | None, data_dir: Path) -> None:
    counts = {"msg": 0, "event": 0}

    def show(item: ChatItem) -> None:
        counts[item.type] += 1
        clock = datetime.fromtimestamp(item.ts).strftime("%H:%M:%S.%f")[:-3]
        lag = item.recv_ts - item.ts
        if isinstance(item, ChatMessage):
            print(f"{clock} (+{lag:4.2f}s) #{item.channel} {item.login}: {item.text}")
        else:
            print(f"{clock} (+{lag:4.2f}s) #{item.channel} ** {item.kind} {item.login} {item.params}")

    chat = TwitchChat(data_dir / "chat", on_item=show)
    for ch in channels:
        await chat.join(ch)
    task = asyncio.create_task(chat.run())
    started = time.time()
    try:
        if seconds:
            await asyncio.sleep(seconds)
        else:
            await task
    finally:
        chat.stop()
        await asyncio.gather(task, return_exceptions=True)
        mins = (time.time() - started) / 60
        print(f"\n{counts['msg']} messages, {counts['event']} events in {mins:.1f} min, "
              f"{chat.reconnects} reconnects. Log: {data_dir / 'chat'}")


def main() -> None:
    p = argparse.ArgumentParser(description="Print a Twitch channel's chat live.")
    p.add_argument("channels", nargs="+")
    p.add_argument("--seconds", type=float, default=None, help="stop after this long")
    p.add_argument("--data-dir", type=Path, default=Path("data"))
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(_main(args.channels, args.seconds, args.data_dir))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
