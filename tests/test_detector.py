import random

import pytest

from clipper.chat import ChatEvent, ChatMessage
from clipper.config import ClipConfig, DetectorConfig
from clipper.detector import Detector, ReactionMatcher

T0 = 1_763_500_000.0          # stream start (aligned to the 2 s step)
SPIKE = T0 + 400              # well after 3 min warm-up and 5 min of baseline


def flat_chat(rng, start, end, per_bin=(2, 5), pool=300):
    """Background chat: a few random chatters per 2 s bin saying ordinary things."""
    msgs = []
    t = start
    while t < end:
        for _ in range(rng.randint(*per_bin)):
            u = f"user{rng.randrange(pool)}"
            msgs.append(ChatMessage("c", u, u, rng.choice(["hi", "nice", "lol ok", "what game"]),
                                    t + rng.random() * 2))
        t += 2
    return msgs


def burst(rng, start, seconds, per_bin=25, text="KEKW NO WAY"):
    msgs = []
    t = start
    n = 0
    while t < start + seconds:
        for _ in range(per_bin):
            u = f"burst{n}"
            n += 1
            msgs.append(ChatMessage("c", u, u, text, t + rng.random() * 2))
        t += 2
    return msgs


def run(items, end=None):
    det = Detector("c", DetectorConfig(), ClipConfig(), started_at=T0)
    items = sorted(items, key=lambda m: m.ts)
    triggers = {}
    for it in items:
        det.feed(it)
        for t in det.tick(it.ts):
            triggers[t.id] = t
    for t in det.tick(end or items[-1].ts + 120):
        triggers[t.id] = t
    for t in det.flush():
        triggers[t.id] = t
    return list(triggers.values())


def test_flat_chat_no_triggers():
    rng = random.Random(1)
    assert run(flat_chat(rng, T0, T0 + 900)) == []


def test_clear_spike_one_trigger_with_onset():
    rng = random.Random(2)
    items = flat_chat(rng, T0, T0 + 600) + burst(rng, SPIKE, 8)
    trigs = run(items)
    assert len(trigs) == 1
    t = trigs[0]
    assert t.final
    assert SPIKE - 2 <= t.onset <= SPIKE + 2
    assert t.moment == pytest.approx(t.onset - 6)
    assert t.start == pytest.approx(t.moment - 20)
    assert t.end >= t.moment + 10
    assert t.end - t.start <= 45
    assert t.ratio >= 3
    assert t.top_messages[0][0] == "KEKW NO WAY"
    assert t.reaction_counts["KEKW"] > 0 and t.reaction_counts["NO WAY"] > 0


def test_one_spammer_no_trigger():
    rng = random.Random(3)
    spam = [ChatMessage("c", "spam", "spammer", "KEKW KEKW", SPIKE + i * 0.05) for i in range(400)]
    assert run(flat_chat(rng, T0, T0 + 600) + spam) == []


def test_bots_and_commands_ignored():
    rng = random.Random(4)
    noise = []
    for i in range(30):
        noise.append(ChatMessage("c", "b", "nightbot", "KEKW", SPIKE + i * 0.2))
        noise.append(ChatMessage("c", f"x{i}", f"x{i}", "!uptime", SPIKE + i * 0.2))
    assert run(flat_chat(rng, T0, T0 + 600) + noise) == []


def test_raid_pauses_detection():
    rng = random.Random(5)
    raid = ChatEvent("c", "raid", SPIKE - 1, "raider", {"viewerCount": "2000"})
    items = flat_chat(rng, T0, T0 + 600) + [raid] + burst(rng, SPIKE, 20, text="raid hype")
    assert run(items) == []


def test_spike_after_pause_still_triggers():
    rng = random.Random(6)
    raid = ChatEvent("c", "raid", SPIKE - 1, "raider")
    items = flat_chat(rng, T0, T0 + 900) + [raid] + burst(rng, SPIKE + 150, 8)
    assert len(run(items)) == 1


def test_sub_burst_pauses():
    rng = random.Random(7)
    subs = [ChatEvent("c", "sub", SPIKE - 10 + i, f"s{i}") for i in range(5)]
    items = flat_chat(rng, T0, T0 + 600) + subs + burst(rng, SPIKE, 8)
    assert run(items) == []


def test_mass_gift_pauses():
    rng = random.Random(12)
    gift = ChatEvent("c", "submysterygift", SPIKE - 1, "rich", {"mass-gift-count": "50"})
    items = flat_chat(rng, T0, T0 + 600) + [gift] + burst(rng, SPIKE, 8)
    assert run(items) == []


def test_small_gifts_do_not_pause():
    """Big channels get small gift bombs every minute; they must not blind the detector."""
    rng = random.Random(13)
    gifts = []
    for k in range(0, 600, 40):
        gifts.append(ChatEvent("c", "submysterygift", T0 + k, "g",
                               {"mass-gift-count": "5", "community-gift-id": str(k)}))
        gifts += [ChatEvent("c", "subgift", T0 + k + 0.5, "g", {"community-gift-id": str(k)})
                  for _ in range(5)]
    items = flat_chat(rng, T0, T0 + 600) + gifts + burst(rng, SPIKE, 8)
    assert len(run(items)) == 1


def test_two_close_spikes_merged():
    rng = random.Random(8)
    items = flat_chat(rng, T0, T0 + 700) + burst(rng, SPIKE, 6) + burst(rng, SPIKE + 20, 6)
    trigs = run(items)
    assert len(trigs) == 1
    t = trigs[0]
    assert t.end >= SPIKE + 20 - 6      # extended to cover the second spike
    assert t.end - t.start <= 45


def test_far_spikes_are_separate():
    rng = random.Random(9)
    items = flat_chat(rng, T0, T0 + 800) + burst(rng, SPIKE, 6) + burst(rng, SPIKE + 120, 6)
    assert len(run(items)) == 2


def test_no_trigger_during_warmup():
    rng = random.Random(10)
    items = flat_chat(rng, T0, T0 + 400) + burst(rng, T0 + 60, 8)
    assert run(items) == []


def test_late_delivery_does_not_fake_a_spike():
    """After a network drop, messages arrive late in one burst, but are binned by send time."""
    rng = random.Random(11)
    items = flat_chat(rng, T0, T0 + 700)
    det = Detector("c", DetectorConfig(), ClipConfig(), started_at=T0)
    out = []
    held = [m for m in items if SPIKE <= m.ts < SPIKE + 15]
    for m in sorted(items, key=lambda m: m.ts):
        if m in held:
            continue
        if m.ts >= SPIKE + 18 and held:
            for h in held:           # backlog delivered 18 s late
                det.feed(h)
            held = []
        det.feed(m)
        out += det.tick(m.ts)
    assert out == []


def test_reaction_matcher():
    m = ReactionMatcher(["KEKW", "W", "NO WAY", "💀", "CLIP IT"])
    assert m.find("kekw that was insane") == {"KEKW"}
    assert m.find("W") == {"W"}
    assert m.find("wow") == set()                 # W only as a whole word
    assert m.find("no   way bro 💀") == {"NO WAY", "💀"}
    assert m.find("nowayyy") == set()
    assert m.find("CLIP IT CLIP IT") == {"CLIP IT"}
