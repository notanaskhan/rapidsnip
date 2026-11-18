from clipper.captions import Word, build_ass, group


def words(spec):
    return [Word(t, a, b, 0.9) for t, a, b in spec]


def test_group_by_count_and_uppercase():
    caps = group(words([("hey,", 0, .3), ("what's", .3, .6), ("up", .6, .8), ("chat", .8, 1.1)]), 3)
    assert [c.text for c in caps] == ["HEY WHAT'S UP", "CHAT"]
    assert caps[0].start == 0 and caps[0].end == caps[1].start   # held until the next caption


def test_group_breaks_on_pause():
    caps = group(words([("no", 0, .3), ("way", .3, .6), ("bro", 2.0, 2.3)]), 3)
    assert [c.text for c in caps] == ["NO WAY", "BRO"]
    assert caps[0].end < 1.0                                      # not held across the pause


def test_ass_escapes_and_positions():
    caps = group(words([("{evil}", 0, .5), ("text", .5, 1)]), 3)
    ass = build_ass(caps, 1080, 1920, 80, y=700)
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "{\\pos(540,700)}" in ass
    line = [ln for ln in ass.splitlines() if ln.startswith("Dialogue")][0]
    assert "EVIL TEXT" in line and "{evil" not in line
    assert line.startswith("Dialogue: 0,0:00:00.00,0:00:01.25,")
