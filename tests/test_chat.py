import json

from clipper.chat import ChatEvent, ChatLog, ChatMessage, parse_line, to_item


def test_privmsg_with_tags():
    line = ("@badge-info=;display-name=Foo\\sBar;tmi-sent-ts=1763500000123;user-id=42 "
            ":foo!foo@foo.tmi.twitch.tv PRIVMSG #SomeChan :KEKW no way :)")
    item = to_item(*parse_line(line), recv_ts=1763500000.5)
    assert isinstance(item, ChatMessage)
    assert item.channel == "somechan"
    assert item.login == "foo"
    assert item.user_id == "42"
    assert item.text == "KEKW no way :)"
    assert item.ts == 1763500000.123


def test_action_message():
    line = "@tmi-sent-ts=1000;user-id=1 :a!a@a PRIVMSG #c :\x01ACTION dances\x01"
    assert to_item(*parse_line(line), recv_ts=0).text == "dances"


def test_usernotice_raid():
    line = ("@login=raider;msg-id=raid;msg-param-viewerCount=1500;tmi-sent-ts=2000 "
            ":tmi.twitch.tv USERNOTICE #c")
    item = to_item(*parse_line(line), recv_ts=0)
    assert isinstance(item, ChatEvent)
    assert item.kind == "raid"
    assert item.params["viewerCount"] == "1500"
    assert item.ts == 2.0


def test_tag_unescape():
    tags, *_ = parse_line("@system-msg=a\\sb\\:c\\\\d :tmi.twitch.tv USERNOTICE #c")
    assert tags["system-msg"] == "a b;c\\d"


def test_other_commands_ignored():
    assert to_item(*parse_line(":tmi.twitch.tv 001 justinfan1 :Welcome"), recv_ts=0) is None
    assert to_item(*parse_line("PING :tmi.twitch.tv"), recv_ts=0) is None


def test_log_writes_jsonl(tmp_path):
    lg = ChatLog(tmp_path)
    lg.write(ChatMessage("c", "1", "a", "hi", 1763500000.0, 1763500000.2))
    lg.close()
    files = list((tmp_path / "c").glob("*.jsonl"))
    assert len(files) == 1
    row = json.loads(files[0].read_text())
    assert row["text"] == "hi" and row["type"] == "msg"
