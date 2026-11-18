import json
import random
from datetime import datetime

from clipper.config import ClipConfig, DetectorConfig
from clipper.links import logged_chat_candidates, merge_candidates

VOD_START = 1_763_500_000.0


def write_log(tmp_path, rows):
    d = tmp_path / "chat" / "chan"
    d.mkdir(parents=True)
    day = datetime.fromtimestamp(VOD_START).strftime("%Y-%m-%d")
    with (d / f"{day}.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return tmp_path / "chat"


def test_saved_chat_finds_spike_at_vod_offset(tmp_path):
    rng = random.Random(1)
    rows = []
    for k in range(0, 1200, 2):                      # 20 min of ordinary chat
        for _ in range(rng.randint(2, 5)):
            u = f"u{rng.randrange(300)}"
            rows.append({"type": "msg", "channel": "chan", "user_id": u, "login": u, "text": "hi",
                         "ts": VOD_START + k + rng.random() * 2})
    for k in range(8):                               # burst 600 s into the VOD
        for j in range(25):
            u = f"b{k}_{j}"
            rows.append({"type": "msg", "channel": "chan", "user_id": u, "login": u, "text": "KEKW NO WAY",
                         "ts": VOD_START + 600 + k + rng.random()})
    rows.sort(key=lambda r: r["ts"])
    chat_dir = write_log(tmp_path, rows)
    cands, n, covered = logged_chat_candidates(str(chat_dir), "chan", VOD_START, 1200.0,
                                               DetectorConfig(), ClipConfig())
    assert n == len(rows)
    assert covered > 0.95
    assert len(cands) == 1
    assert 590 <= cands[0]["moment"] + ClipConfig().chat_delay_seconds <= 606
    assert cands[0]["source"] == "chat"


def test_saved_chat_outside_window_is_ignored(tmp_path):
    chat_dir = write_log(tmp_path, [{"type": "msg", "user_id": "a", "login": "a", "text": "hi", "ts": VOD_START - 50}])
    assert logged_chat_candidates(str(chat_dir), "chan", VOD_START, 600.0, DetectorConfig(), ClipConfig())[1] == 0


def test_merge_combines_nearby_signals():
    c = [{"moment": 100, "score": 0.6, "source": "chat", "detail": "chat 3.5x", "start": 80, "end": 110},
         {"moment": 110, "score": 0.5, "source": "loud", "detail": "loud +12 dB"},
         {"moment": 500, "score": 0.4, "source": "replayed", "detail": "most replayed 60%"}]
    m = merge_candidates(c, 1000, ClipConfig())
    assert len(m) == 2
    assert m[0]["sources"] == ["chat", "loud"] and m[0]["score"] == 0.75
    assert (m[0]["start"], m[0]["end"]) == (80, 110)
    assert m[1]["start"] == 480 and m[1]["end"] == 510


def test_merge_same_signal_does_not_double_count():
    c = [{"moment": 100, "score": 0.5, "source": "loud", "detail": "loud +14 dB"},
         {"moment": 110, "score": 0.5, "source": "loud", "detail": "loud +12 dB"}]
    m = merge_candidates(c, 1000, ClipConfig())
    assert len(m) == 1 and m[0]["score"] == 0.5 and m[0]["sources"] == ["loud"]
