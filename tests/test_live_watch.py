import asyncio

import clipper.live_watch as lw


def run_polls(states, monkeypatch):
    """states: list of {channel: True/False/None} per poll. Returns the event log."""
    events = []
    seq = iter(states)
    current = {}

    def fake_is_live(ch):
        return current[ch]

    monkeypatch.setattr(lw, "is_live", fake_is_live)

    async def on_live(ch):
        events.append(("live", ch))

    async def on_offline(ch):
        events.append(("offline", ch))

    async def go():
        w = lw.LiveWatcher(["a", "b"], 0, on_live, on_offline)
        for st in seq:
            current.update(st)
            await w.poll_once()

    asyncio.run(go())
    return events


def test_live_then_offline_after_two_polls(monkeypatch):
    ev = run_polls([{"a": True, "b": False}, {"a": False, "b": False}, {"a": False, "b": False}], monkeypatch)
    assert ev == [("live", "a"), ("offline", "a")]


def test_single_offline_blip_is_ignored(monkeypatch):
    ev = run_polls([{"a": True, "b": False}, {"a": False, "b": False}, {"a": True, "b": False}], monkeypatch)
    assert ev == [("live", "a")]


def test_failed_check_changes_nothing(monkeypatch):
    ev = run_polls([{"a": True, "b": True}, {"a": None, "b": None}, {"a": None, "b": None}], monkeypatch)
    assert ev == [("live", "a"), ("live", "b")]
