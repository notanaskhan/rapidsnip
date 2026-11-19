"""Send every new clip to the owner's Telegram, with Approve / Posted / Reject buttons.

Bot API over HTTPS with urllib (no extra dependency). Button taps come back by long
polling getUpdates, so the Mac needs no public URL or tunnel. Pairing lives in
data/telegram.json ({"token", "chat_id"}), written by `python -m clipper telegram`;
the running server picks it up without a restart. Only the paired chat is obeyed.

Bots may upload at most 50 MB, so clips above MAX_BYTES are re-encoded to a phone copy
(same 1080x1920, lower bitrate) in data/telegram_tmp/ and deleted once sent.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"
MAX_BYTES = 49_000_000          # Bot API upload limit is 50 MB
TARGET_BYTES = 40_000_000       # aim the phone copy well under it (VideoToolbox overshoots a little)
MAX_VIDEO_BITRATE = 7_000_000
AUDIO_BITRATE = 160_000
CAPTION_LIMIT = 1024
POLL_SECONDS = 50
STATUS_LABELS = {"approved": "Approved", "posted": "Posted", "rejected": "Rejected", "new": "Back to review"}


class TelegramError(RuntimeError):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


# ---- Bot API ------------------------------------------------------------

def multipart(fields: dict[str, str], files: dict[str, Path]) -> tuple[bytes, str]:
    """Encode a multipart/form-data body (files are read whole; phone copies are < 50 MB)."""
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for k, path in files.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{path.name}"\r\n'
                     f"Content-Type: application/octet-stream\r\n\r\n".encode())
        parts.append(path.read_bytes())
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def call(token: str, method: str, fields: dict | None = None, files: dict[str, Path] | None = None,
         timeout: float = 30) -> object:
    """One Bot API call. Non-string fields are sent as JSON (reply_markup, allowed_updates)."""
    fields = {k: v if isinstance(v, str) else json.dumps(v) for k, v in (fields or {}).items()}
    if files:
        body, ctype = multipart(fields, files)
    else:
        body, ctype = json.dumps(fields).encode(), "application/json"
    req = urllib.request.Request(API.format(token=token, method=method), data=body,
                                 headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read())
        except ValueError:
            raise TelegramError(f"{method}: HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise TelegramError(f"{method}: {e}") from e
    if not data.get("ok"):
        raise TelegramError(f"{method}: {data.get('description', data)}",
                            (data.get("parameters") or {}).get("retry_after"))
    return data["result"]


def load_settings(data_dir: Path) -> dict:
    try:
        s = json.loads((data_dir / "telegram.json").read_text())
    except (OSError, ValueError):
        return {}
    return s if s.get("token") and s.get("chat_id") else {}


def save_settings(data_dir: Path, token: str, chat_id: int, name: str) -> Path:
    path = data_dir / "telegram.json"
    path.write_text(json.dumps({"token": token, "chat_id": chat_id, "name": name}, indent=2))
    path.chmod(0o600)
    return path


# ---- message content ------------------------------------------------------

def phone_bitrate(seconds: float, target_bytes: int = TARGET_BYTES) -> int:
    """Video bitrate that lands a clip of this length near target_bytes."""
    bits = target_bytes * 8 / max(seconds, 1.0) - AUDIO_BITRATE
    return int(max(1_000_000, min(MAX_VIDEO_BITRATE, bits)))


def phone_copy(src: Path, dst: Path, seconds: float) -> Path:
    """src itself if it fits Telegram's limit, else a smaller re-encode at dst."""
    if src.stat().st_size <= MAX_BYTES:
        return src
    dst.parent.mkdir(parents=True, exist_ok=True)
    target = TARGET_BYTES
    for _ in range(3):
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), "-map", "0:v:0", "-map", "0:a?",
                        "-c:v", "h264_videotoolbox", "-b:v", str(phone_bitrate(seconds, target)),
                        "-c:a", "copy", "-movflags", "+faststart", str(dst)], check=True, timeout=300)
        if dst.stat().st_size <= MAX_BYTES:
            return dst
        target = int(target * 0.75)
    raise TelegramError(f"phone copy of {src} is still {dst.stat().st_size / 1e6:.0f} MB")


def caption(row: dict, display_name: str) -> str:
    """HTML caption: who and how big, then title and description as tap-to-copy code blocks."""
    when = datetime.fromtimestamp(row["moment"]).strftime("%H:%M")
    head = f"<b>{html.escape(display_name)}</b> · {row['ratio']:.1f}× chat spike · {when} · {row['seconds']:.0f}s"
    title = f"<code>{html.escape(row['title'])}</code>"
    room = CAPTION_LIMIT - len(head) - len(title) - 40
    desc = row["description"]
    if len(desc) > room:
        desc = desc[:max(0, room - 1)] + "…"
    return f"{head}\n\n{title}\n\n<code>{html.escape(desc)}</code>"


def keyboard(cid: int, status: str) -> dict:
    """Buttons for a clip. A rejected clip only offers Undo while it waits to be deleted."""
    if status == "rejected":
        return {"inline_keyboard": [[{"text": "↩ Undo reject", "callback_data": f"{cid}:new"}]]}

    def b(label: str, st: str) -> dict:
        return {"text": ("● " if status == st else "") + label, "callback_data": f"{cid}:{st}"}
    return {"inline_keyboard": [[b("✓ Approve", "approved"), b("Posted", "posted"), b("✕ Reject", "rejected")]]}


def parse_callback(data: str) -> tuple[int, str] | None:
    try:
        cid, status = data.split(":", 1)
        return int(cid), status
    except ValueError:
        return None


# ---- the bot --------------------------------------------------------------

class TelegramBot:
    """Runs inside `clipper serve`. set_status(cid, status) is the server's own status
    change (so a reject from the phone gets the same undo grace as one from the Mac)."""

    def __init__(self, cfg, db, set_status, display_name):
        self.cfg, self.db = cfg, db
        self.set_status = set_status
        self.display_name = display_name             # channel login -> display name
        self.sent_path = cfg.data_dir / "telegram_sent.json"
        self.tmp_dir = cfg.data_dir / "telegram_tmp"
        try:
            self.sent: dict[str, int] = json.loads(self.sent_path.read_text())
        except (OSError, ValueError):
            self.sent = {}
        self.queue: asyncio.Queue = asyncio.Queue()
        self.tasks: list[asyncio.Task] = []
        self.loop: asyncio.AbstractEventLoop | None = None
        self.settings: dict = {}

    @property
    def paired(self) -> bool:
        return bool(self.settings)

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.settings = load_settings(self.cfg.data_dir)
        log.info("telegram: %s", f"sending clips to {self.settings.get('name')}" if self.paired
                 else "not paired (run: uv run python -m clipper telegram --token <token>)")
        self.tasks = [asyncio.create_task(self._send_loop()), asyncio.create_task(self._poll_loop())]

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    # Called from the supervisor and server (any thread).

    def clip_ready(self, folder_rel: str) -> None:
        self._threadsafe(self.queue.put_nowait, folder_rel)

    def status_changed(self, cid: int, status: str) -> None:
        self._threadsafe(lambda: asyncio.ensure_future(self._edit_buttons(cid, status)))

    def clip_deleted(self, cid: int) -> None:
        self._threadsafe(lambda: asyncio.ensure_future(self._delete_message(cid)))

    def _threadsafe(self, fn, *args) -> None:
        if self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(fn, *args)

    async def _api(self, method: str, fields: dict | None = None, files: dict[str, Path] | None = None,
                   timeout: float = 30, tries: int = 4) -> object:
        for attempt in range(tries):
            try:
                return await asyncio.to_thread(call, self.settings["token"], method, fields, files, timeout)
            except TelegramError as e:
                if attempt == tries - 1 or "message is not modified" in str(e):
                    raise
                wait = e.retry_after or 2 ** attempt * 2
                log.warning("telegram %s failed (%s); retrying in %ss", method, e, wait)
                await asyncio.sleep(wait)

    # ---- sending clips -----------------------------------------------------

    async def _send_loop(self) -> None:
        while True:
            folder_rel = await self.queue.get()
            if not self.paired:
                continue
            try:
                await self._send_clip(folder_rel)
            except Exception:  # noqa: BLE001 - a failed send must never stop the next one
                log.exception("telegram: could not send %s", folder_rel)

    async def _send_clip(self, folder_rel: str) -> None:
        row = self.db.clip_by_folder(folder_rel)
        if not row or str(row["id"]) in self.sent or row["status"] == "rejected":
            return
        src = self.cfg.output_dir / folder_rel / "vertical.mp4"
        if not src.exists():
            return
        t0 = time.monotonic()
        tmp = self.tmp_dir / f"{row['id']}.mp4"
        video = await asyncio.to_thread(phone_copy, src, tmp, row["seconds"])
        size = video.stat().st_size
        try:
            msg = await self._api("sendVideo", {
                "chat_id": str(self.settings["chat_id"]), "caption": caption(row, self.display_name(row["channel"])),
                "parse_mode": "HTML", "supports_streaming": "true", "width": "1080", "height": "1920",
                "duration": str(round(row["seconds"])), "reply_markup": keyboard(row["id"], row["status"]),
            }, files={"video": video}, timeout=300)
        finally:
            if video == tmp:
                tmp.unlink(missing_ok=True)
        self.sent[str(row["id"])] = msg["message_id"]
        self._save_sent()
        log.info("telegram: sent %s (%.1f MB, %.0fs)", folder_rel, size / 1e6, time.monotonic() - t0)

    def _save_sent(self) -> None:
        self.sent_path.write_text(json.dumps(self.sent))

    async def _edit_buttons(self, cid: int, status: str) -> None:
        mid = self.sent.get(str(cid))
        if not (self.paired and mid):
            return
        try:
            await self._api("editMessageReplyMarkup", {"chat_id": str(self.settings["chat_id"]), "message_id": mid,
                                                       "reply_markup": keyboard(cid, status)}, tries=2)
        except TelegramError as e:
            if "not modified" not in str(e):
                log.warning("telegram: could not update buttons for clip %s: %s", cid, e)

    async def _delete_message(self, cid: int) -> None:
        mid = self.sent.pop(str(cid), None)
        if not (self.paired and mid):
            return
        self._save_sent()
        try:
            await self._api("deleteMessage", {"chat_id": str(self.settings["chat_id"]), "message_id": mid}, tries=2)
        except TelegramError as e:              # bots can't delete messages older than 48 h
            log.warning("telegram: could not delete message for clip %s: %s", cid, e)

    # ---- button taps -------------------------------------------------------

    async def _poll_loop(self) -> None:
        offset = 0
        while True:
            if not self.paired:                  # pairing written by the CLI while we run
                await asyncio.sleep(10)
                self.settings = load_settings(self.cfg.data_dir)
                if self.paired:
                    log.info("telegram: paired with %s", self.settings.get("name"))
                continue
            try:
                updates = await asyncio.to_thread(
                    call, self.settings["token"], "getUpdates",
                    {"offset": offset, "timeout": POLL_SECONDS, "allowed_updates": ["callback_query"]},
                    None, POLL_SECONDS + 15)
            except TelegramError as e:
                log.warning("telegram: polling failed (%s)", e)
                await asyncio.sleep(e.retry_after or 10)
                continue
            for u in updates:
                offset = u["update_id"] + 1
                if "callback_query" in u:
                    try:
                        await self._on_tap(u["callback_query"])
                    except Exception:  # noqa: BLE001
                        log.exception("telegram: button tap failed")

    async def _on_tap(self, q: dict) -> None:
        chat = (q.get("message") or {}).get("chat", {}).get("id")
        parsed = parse_callback(q.get("data", ""))
        if chat != self.settings["chat_id"] or not parsed:
            await self._api("answerCallbackQuery", {"callback_query_id": q["id"]}, tries=1)
            return
        cid, status = parsed
        if not self.db.clip(cid):
            text = "That clip is already deleted."
        else:
            await self.set_status(cid, status)
            text = STATUS_LABELS.get(status, status)
            if status == "rejected":
                text += " · deleting in a few seconds, tap Undo to keep it"
        await self._api("answerCallbackQuery", {"callback_query_id": q["id"], "text": text}, tries=1)


# ---- pairing (CLI) ----------------------------------------------------------

def pair(data_dir: Path, token: str, wait_seconds: float = 300) -> None:
    """Check the token, wait for the owner to press Start in the bot's chat, save the pairing."""
    me = call(token, "getMe")
    print(f"Bot: @{me['username']}\nOpen https://t.me/{me['username']} on your phone and press Start "
          f"(waiting {wait_seconds / 60:.0f} min)…", flush=True)
    offset, deadline = 0, time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        for u in call(token, "getUpdates", {"offset": offset, "timeout": 30, "allowed_updates": ["message"]},
                      timeout=45):
            offset = u["update_id"] + 1
            msg = u.get("message") or {}
            chat = msg.get("chat", {})
            if chat.get("type") == "private" and msg.get("text", "").startswith("/start"):
                who = " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x)
                name = f"{who} (@{chat['username']})" if chat.get("username") else who
                call(token, "getUpdates", {"offset": offset, "timeout": 0})     # confirm, so it's not seen again
                path = save_settings(data_dir, token, chat["id"], name)
                call(token, "sendMessage", {"chat_id": chat["id"], "text":
                     "Paired with Clipper. Every new clip will arrive here with Approve / Posted / Reject buttons."})
                print(f"Paired with {name}. Saved to {path}. A running `clipper serve` picks it up within 10 s.")
                return
    raise SystemExit("No /start received; run the command again.")
