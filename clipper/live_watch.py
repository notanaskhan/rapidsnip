"""Polls Streamlink to see which channels are live."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

import streamlink

log = logging.getLogger(__name__)

OFFLINE_POLLS = 2            # consecutive offline polls before stopping a session


def is_live(channel: str, url: str | None = None) -> bool | None:
    """True/False, or None if the check itself failed (network, platform error)."""
    try:
        return bool(streamlink.Streamlink().streams(url or f"https://twitch.tv/{channel}"))
    except streamlink.NoPluginError:
        raise
    except Exception as e:  # noqa: BLE001 - any failure means "unknown"
        log.warning("live check for %s failed: %r", channel, e)
        return None


class LiveWatcher:
    def __init__(self, channels: list[str], poll_seconds: float,
                 on_live: Callable[[str], Awaitable[None]],
                 on_offline: Callable[[str], Awaitable[None]]):
        self.channels = channels
        self.urls: dict[str, str] = {}          # channel -> live URL (default: Twitch)
        self.poll_seconds = poll_seconds
        self.on_live = on_live
        self.on_offline = on_offline
        self.live: set[str] = set()
        self._offline_count: dict[str, int] = {}

    async def poll_once(self) -> None:
        results = await asyncio.gather(*(asyncio.to_thread(is_live, ch, self.urls.get(ch))
                                         for ch in self.channels))
        for ch, state in zip(self.channels, results):
            if state is None:
                continue
            if state:
                self._offline_count[ch] = 0
                if ch not in self.live:
                    self.live.add(ch)
                    log.info("%s went live", ch)
                    await self.on_live(ch)
            elif ch in self.live:
                self._offline_count[ch] = self._offline_count.get(ch, 0) + 1
                if self._offline_count[ch] >= OFFLINE_POLLS:
                    self.live.discard(ch)
                    log.info("%s went offline", ch)
                    await self.on_offline(ch)

    async def run(self) -> None:
        while True:
            await self.poll_once()
            await asyncio.sleep(self.poll_seconds)
