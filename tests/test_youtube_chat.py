import json

from clipper.chat import ChatEvent, ChatMessage
from clipper.config import live_url, streamlink_args
from clipper.youtube_chat import parse_line


def action(kind, body):
    return json.dumps({"replayChatItemAction": {"actions": [{"addChatItemAction": {"item": {kind: body}}}]}})


def test_text_message_with_emoji():
    line = action("liveChatTextMessageRenderer", {
        "message": {"runs": [{"text": "NO WAY "}, {"emoji": {"emojiId": "😂", "shortcuts": [":joy:"]}},
                             {"emoji": {"emojiId": "UCabc/xyz-custom-emoji-id", "shortcuts": [":hype:"]}}]},
        "authorName": {"simpleText": "@SomeViewer"}, "authorExternalChannelId": "UC123",
        "timestampUsec": "1795036044151341"})
    (m,) = parse_line(line, "carryislive", recv_ts=0)
    assert isinstance(m, ChatMessage)
    assert m.text == "NO WAY 😂:hype:"
    assert m.login == "someviewer" and m.user_id == "UC123" and m.channel == "carryislive"
    assert abs(m.ts - 1795036044.151341) < 1e-6


def test_membership_and_gift_events():
    member = action("liveChatMembershipItemRenderer", {"authorName": {"simpleText": "@a"}, "timestampUsec": "1000000"})
    gift = action("liveChatSponsorshipsGiftPurchaseAnnouncementRenderer", {
        "timestampUsec": "2000000",
        "header": {"liveChatSponsorshipsHeaderRenderer": {
            "authorName": {"simpleText": "@rich"}, "primaryText": {"runs": [{"text": "Gifted "}, {"text": "50"}, {"text": " memberships"}]}}}})
    (e1,) = parse_line(member, "c", 0)
    (e2,) = parse_line(gift, "c", 0)
    assert isinstance(e1, ChatEvent) and e1.kind == "sub" and e1.ts == 1.0
    assert e2.kind == "submysterygift" and e2.params["mass-gift-count"] == "50" and e2.login == "rich"


def test_superchat_is_message_and_event():
    line = action("liveChatPaidMessageRenderer", {"message": {"runs": [{"text": "W"}]},
                                                  "authorName": {"simpleText": "@b"}, "timestampUsec": "3000000"})
    items = parse_line(line, "c", 0)
    assert [type(i).__name__ for i in items] == ["ChatMessage", "ChatEvent"]


def test_ignores_junk_and_other_renderers():
    assert parse_line("not json", "c", 0) == []
    assert parse_line(action("liveChatViewerEngagementMessageRenderer", {"message": {}}), "c", 0) == []


def test_live_urls_and_args():
    assert live_url("youtube", "CarryisLive") == "https://www.youtube.com/@CarryisLive/live"
    assert live_url("twitch", "kaicenat") == "https://twitch.tv/kaicenat"
    assert "--twitch-low-latency" in streamlink_args("twitch")
    assert streamlink_args("youtube") == []
