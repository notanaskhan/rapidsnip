import asyncio
import email
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipper import telegram as tg


def row(**kw):
    r = {"id": 7, "folder": "2026-11-19/ishowspeed_031220_5.6x", "channel": "ishowspeed", "moment": 1795000000.0,
         "ratio": 5.6, "seconds": 38.0, "status": "new", "title": 'IShowSpeed in GTA 6 "hey" #GTA6',
         "description": "IShowSpeed playing GTA 6 live.\nClip from https://twitch.tv/ishowspeed"}
    r.update(kw)
    return r


def test_multipart_round_trips(tmp_path):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"\x00\x01video")
    body, ctype = tg.multipart({"chat_id": "42", "caption": "<b>hi</b> ✓"}, {"video": f})
    msg = email.message_from_bytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + body)
    parts = {p.get_param("name", header="content-disposition"): p for p in msg.get_payload()}
    assert parts["chat_id"].get_payload() == "42"
    assert parts["caption"].get_payload(decode=True).decode() == "<b>hi</b> ✓"
    assert parts["video"].get_filename() == "v.mp4"
    assert parts["video"].get_payload(decode=True) == b"\x00\x01video"


def test_caption_escapes_and_fits():
    c = tg.caption(row(title="a <b> & c", description="x" * 5000), "IShowSpeed")
    assert "<code>a &lt;b&gt; &amp; c</code>" in c
    assert c.startswith("<b>IShowSpeed</b> · 5.6× chat spike")
    assert len(c) <= tg.CAPTION_LIMIT
    assert c.endswith("…</code>")


def test_keyboard_marks_status_and_offers_undo():
    kb = tg.keyboard(7, "approved")["inline_keyboard"][0]
    assert [b["callback_data"] for b in kb] == ["7:approved", "7:posted", "7:rejected"]
    assert kb[0]["text"].startswith("●") and not kb[1]["text"].startswith("●")
    assert tg.keyboard(7, "rejected") == {"inline_keyboard": [[{"text": "↩ Undo reject", "callback_data": "7:new"}]]}


def test_parse_callback():
    assert tg.parse_callback("12:posted") == (12, "posted")
    assert tg.parse_callback("junk") is None
    assert tg.parse_callback("x:posted") is None


def test_phone_bitrate_fits_limit():
    for seconds in (5, 30, 45, 60, 120):
        total = (tg.phone_bitrate(seconds) + tg.AUDIO_BITRATE) * seconds / 8
        assert total <= tg.TARGET_BYTES * 1.01
    assert tg.phone_bitrate(30) == tg.MAX_VIDEO_BITRATE     # short clips keep the cap, not more


def test_phone_copy_keeps_small_files(tmp_path):
    src = tmp_path / "v.mp4"
    src.write_bytes(b"small")
    assert tg.phone_copy(src, tmp_path / "out.mp4", 30) == src


def test_phone_copy_reencodes_big_files(tmp_path, monkeypatch):
    src = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30",
                    "-f", "lavfi", "-i", "sine", "-t", "3", "-c:v", "h264_videotoolbox", "-b:v", "20M",
                    "-c:a", "aac", "-shortest", str(src)], check=True)
    monkeypatch.setattr(tg, "MAX_BYTES", src.stat().st_size - 1)
    monkeypatch.setattr(tg, "TARGET_BYTES", src.stat().st_size // 4)
    out = tg.phone_copy(src, tmp_path / "out.mp4", 3)
    assert out == tmp_path / "out.mp4"
    assert out.stat().st_size < src.stat().st_size
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height",
                            "-of", "json", str(out)], capture_output=True, text=True, check=True)
    streams = json.loads(probe.stdout)["streams"]
    assert {s["codec_type"] for s in streams} == {"video", "audio"}
    assert [(s["width"], s["height"]) for s in streams if s["codec_type"] == "video"] == [(1080, 1920)]


class FakeDB:
    def __init__(self, rows):
        self.rows = {r["id"]: r for r in rows}

    def clip(self, cid):
        return self.rows.get(cid)

    def clip_by_folder(self, folder):
        return next((r for r in self.rows.values() if r["folder"] == folder), None)


@pytest.fixture
def bot(tmp_path, monkeypatch):
    calls = []

    def fake_call(token, method, fields=None, files=None, timeout=30):
        calls.append((method, fields or {}, files or {}))
        return {"message_id": 99} if method == "sendVideo" else True

    monkeypatch.setattr(tg, "call", fake_call)
    out = tmp_path / "clips"
    (out / row()["folder"]).mkdir(parents=True)
    (out / row()["folder"] / "vertical.mp4").write_bytes(b"video")
    tg.save_settings(tmp_path, "T", 42, "Me")
    statuses = []

    async def set_status(cid, status):
        statuses.append((cid, status))

    cfg = SimpleNamespace(data_dir=tmp_path, output_dir=out)
    b = tg.TelegramBot(cfg, FakeDB([row()]), set_status, display_name=lambda login: "IShowSpeed")
    b.settings = tg.load_settings(tmp_path)
    return b, calls, statuses


def test_sends_clip_once_with_buttons(bot):
    b, calls, _ = bot
    asyncio.run(b._send_clip(row()["folder"]))
    asyncio.run(b._send_clip(row()["folder"]))          # already sent: no second message
    sends = [c for c in calls if c[0] == "sendVideo"]
    assert len(sends) == 1
    method, fields, files = sends[0]
    assert fields["chat_id"] == "42" and fields["parse_mode"] == "HTML"
    assert fields["reply_markup"] == tg.keyboard(7, "new")
    assert files["video"].name == "vertical.mp4"
    assert json.loads((b.cfg.data_dir / "telegram_sent.json").read_text()) == {"7": 99}


def test_taps_only_from_paired_chat(bot):
    b, calls, statuses = bot
    tap = {"id": "q1", "data": "7:approved", "message": {"chat": {"id": 42}}}
    asyncio.run(b._on_tap(tap))
    asyncio.run(b._on_tap({**tap, "id": "q2", "message": {"chat": {"id": 666}}}))
    assert statuses == [(7, "approved")]
    answers = [c[1] for c in calls if c[0] == "answerCallbackQuery"]
    assert answers[0] == {"callback_query_id": "q1", "text": "Approved"}
    assert answers[1] == {"callback_query_id": "q2"}


def test_unpaired_bot_sends_nothing(bot):
    b, calls, _ = bot
    b.settings = {}
    assert not b.paired
    asyncio.run(b._edit_buttons(7, "posted"))
    assert calls == []
