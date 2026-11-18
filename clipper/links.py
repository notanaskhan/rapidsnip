"""Link clipper: paste a YouTube video, a past livestream or a Twitch VOD, get clips.

Pipeline per link (one link analyzed at a time; renders share the live worker queue):
1. Info (yt-dlp, no download): title, duration, uploader, YouTube "Most replayed" heatmap.
2. Signals, without downloading the video:
   - chat replay (Twitch VODs, YouTube streams with chat replay) via chat-downloader,
     run through the same Detector as the live app;
   - "Most replayed" peaks;
   - loud moments from an audio-only read of the source.
3. Candidates: the signals merged into a ranked list of moments.
4. Render the picked candidates (or custom start/end): FFmpeg reads just that window
   straight from the source URL, so a 3-hour VOD never has to be downloaded.

Every link needs a permitted streamer or its own permission note.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import math
import re
import statistics
import subprocess
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .chat import ChatMessage
from .config import ClipConfig, Config, DetectorConfig
from .detector import Detector

log = logging.getLogger(__name__)

FORMAT = "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b"
AUDIO_FORMAT = "ba[ext=m4a]/ba/audio_only/b[height<=480]/b"
# Streamlink (same process) patches urllib3 in a way that breaks yt-dlp's requests handler;
# yt-dlp's own urllib handler is unaffected.
YDL_BASE = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
            "compat_opts": {"prefer-legacy-http-handler"}}
LOUD_RATE = 2000            # Hz for the loudness pass (plenty for an RMS envelope)
MERGE_SECONDS = 25          # candidates closer than this are the same moment
MAX_CANDIDATES = 25
URL_RE = re.compile(r"^https?://\S+$")


class LinkIn(BaseModel):
    url: str
    streamer_id: int | None = None
    permission: str = ""


class RenderItem(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    label: str = ""
    candidate: int | None = None     # index into the job's candidates, for chat stats


class RenderIn(BaseModel):
    items: list[RenderItem] = Field(min_length=1, max_length=30)


# ---- signals ------------------------------------------------------------

def ytdlp_info(url: str) -> dict:
    import yt_dlp
    with yt_dlp.YoutubeDL({**YDL_BASE, "format": FORMAT}) as y:
        return y.sanitize_info(y.extract_info(url, download=False))


def media_inputs(info: dict) -> tuple[list[str], str, str]:
    """FFmpeg input args for the source's video+audio streams, and the stream labels to use."""
    def one(f: dict) -> list[str]:
        headers = "".join(f"{k}: {v}\r\n" for k, v in (f.get("http_headers") or {}).items())
        return (["-headers", headers] if headers else []) + ["-i", f["url"]]

    fmts = info.get("requested_formats")
    if fmts and len(fmts) == 2:
        v = next(f for f in fmts if f.get("vcodec") not in (None, "none"))
        a = next(f for f in fmts if f is not v)
        return one(v) + one(a), "0:v:0", "1:a:0"
    return one(info), "0:v:0", "0:a:0"


def heatmap_candidates(info: dict) -> list[dict]:
    pts = info.get("heatmap") or []
    if len(pts) < 10:
        return []
    vals = [p["value"] for p in pts]
    floor = max(statistics.median(vals) * 1.6, 0.35)
    out = []
    for i, p in enumerate(pts):
        left = vals[i - 1] if i else -1
        right = vals[i + 1] if i + 1 < len(vals) else -1
        if p["value"] >= floor and p["value"] >= left and p["value"] >= right:
            mid = (p["start_time"] + p["end_time"]) / 2
            out.append({"moment": mid, "score": min(1.0, p["value"]), "source": "replayed",
                        "detail": f"most replayed {p['value']:.0%}"})
    return out


def chat_candidates(url: str, duration: float, det_cfg: DetectorConfig, clip_cfg: ClipConfig,
                    progress=None) -> tuple[list[dict], int]:
    """Chat replay through the live detector. Returns (candidates, messages seen)."""
    from chat_downloader import ChatDownloader
    from chat_downloader.errors import ChatDownloaderError

    det = Detector("vod", dataclasses.replace(det_cfg, warmup_minutes=min(det_cfg.warmup_minutes, 1.0)),
                   clip_cfg, started_at=0.0)
    triggers = {}
    n = 0
    try:
        chat = ChatDownloader().get_chat(url, message_types=["text_message"], inactivity_timeout=60)
        for m in chat:
            t = m.get("time_in_seconds")
            if t is None or t < 0:
                continue
            author = (m.get("author") or {})
            det.feed(ChatMessage("vod", str(author.get("id", "")), str(author.get("name", "")).lower(),
                                 m.get("message") or "", float(t)))
            for tr in det.tick(float(t)):
                triggers[tr.id] = tr
            n += 1
            if progress and n % 2000 == 0 and duration:
                progress(min(0.99, t / duration), f"reading chat replay: {n:,} messages")
    except ChatDownloaderError as e:
        log.info("no chat replay for %s: %s", url, e)
        return [], 0
    for tr in det.tick(duration + 60) + det.flush():
        triggers[tr.id] = tr
    out = []
    for tr in triggers.values():
        if not tr.final:
            continue
        out.append({"moment": tr.moment, "start": tr.start, "end": tr.end,
                    "score": min(1.0, (tr.ratio - 1) / 4), "source": "chat", "ratio": round(tr.ratio, 2),
                    "detail": f"chat {tr.ratio:.1f}x", "top_messages": tr.top_messages[:8],
                    "reaction_counts": tr.reaction_counts})
    return out, n


def loud_candidates(info: dict, duration: float, progress=None) -> list[dict]:
    """Loudest stretches relative to their surroundings, from an audio-only read."""
    import numpy as np
    import yt_dlp

    with yt_dlp.YoutubeDL({**YDL_BASE, "format": AUDIO_FORMAT}) as y:
        a = y.sanitize_info(y.extract_info(info["webpage_url"], download=False))
    headers = "".join(f"{k}: {v}\r\n" for k, v in (a.get("http_headers") or {}).items())
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
    if headers:
        cmd += ["-headers", headers]
    cmd += ["-i", a["url"], "-vn", "-ac", "1", "-ar", str(LOUD_RATE), "-f", "s16le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    rms = []
    chunk = LOUD_RATE * 2
    while True:
        buf = proc.stdout.read(chunk)
        if not buf:
            break
        x = np.frombuffer(buf, dtype=np.int16).astype(np.float32)
        rms.append(float(np.sqrt(np.mean(x * x))) if len(x) else 0.0)
        if progress and len(rms) % 120 == 0 and duration:
            progress(min(0.99, len(rms) / duration), "measuring loudness")
    proc.wait()
    if len(rms) < 120:
        return []
    db = 20 * np.log10(np.maximum(np.array(rms), 1.0))
    smooth = np.convolve(db, np.ones(3) / 3, mode="same")          # 3 s loudness
    out = []
    for i in range(60, len(smooth) - 5):
        ctx = smooth[max(0, i - 120):i - 10]                         # the 2 minutes before
        if len(ctx) < 30:
            continue
        med = float(np.median(ctx))
        mad = float(np.median(np.abs(ctx - med))) or 1.0
        z = (smooth[i] - med) / mad
        if z >= 6 and smooth[i] == smooth[max(0, i - 8):i + 8].max():
            out.append({"moment": float(i), "score": min(1.0, z / 15), "source": "loud",
                        "detail": f"loud +{smooth[i] - med:.0f} dB"})
    return out


def merge_candidates(cands: list[dict], duration: float, clip_cfg: ClipConfig) -> list[dict]:
    cands = sorted(cands, key=lambda c: -c["score"])
    merged: list[dict] = []
    for c in cands:
        hit = next((m for m in merged if abs(m["moment"] - c["moment"]) <= MERGE_SECONDS), None)
        if hit:
            hit["sources"].append(c["source"])
            hit["details"].append(c["detail"])
            hit["score"] = min(1.0, hit["score"] + 0.15)
            for k in ("top_messages", "reaction_counts", "ratio"):
                if k in c and k not in hit:
                    hit[k] = c[k]
            continue
        start = c.get("start", c["moment"] - clip_cfg.pre_seconds)
        end = c.get("end", c["moment"] + clip_cfg.post_seconds)
        merged.append({**{k: v for k, v in c.items() if k not in ("source", "detail")},
                       "start": max(0.0, start), "end": min(duration or end, end),
                       "sources": [c["source"]], "details": [c["detail"]]})
    merged = sorted(merged, key=lambda m: -m["score"])[:MAX_CANDIDATES]
    for m in merged:
        m["score"] = round(m["score"], 3)
        m["start"], m["end"], m["moment"] = round(m["start"], 1), round(m["end"], 1), round(m["moment"], 1)
    return sorted(merged, key=lambda m: m["moment"])


# ---- manager ------------------------------------------------------------

class LinkManager:
    def __init__(self, cfg: Config, db):
        self.cfg = cfg
        self.db = db
        self.sup = None
        self.queue: asyncio.Queue[int] = asyncio.Queue()
        self.task: asyncio.Task | None = None
        self.renders: dict[int, dict] = {}     # job id -> {"total", "done", "failed"}

    def attach(self, sup, app: FastAPI) -> None:
        self.sup = sup
        self._routes(app)

    async def start(self) -> None:
        for j in self.db.jobs(200):            # resume anything interrupted by a restart
            if j["status"] in ("queued", "analyzing"):
                self.db.update_job(j["id"], status="queued", progress=0, message="waiting")
                self.queue.put_nowait(j["id"])
            elif j["status"] == "rendering":
                self.db.update_job(j["id"], status="ready", message="render interrupted by a restart")
        self.task = asyncio.create_task(self._loop(), name="links")

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()

    # ---- analysis -------------------------------------------------------

    async def _loop(self) -> None:
        while True:
            jid = await self.queue.get()
            try:
                await self._analyze(jid)
            except Exception as e:  # noqa: BLE001 - a bad link must not stop the queue
                log.exception("link job %d failed", jid)
                self.db.update_job(jid, status="failed", message=str(e)[:500])

    def _channel_for(self, job: dict):
        if not job.get("streamer_id"):
            return None
        return next((c for c in self.sup.channels.values() if c.name == self._streamer_login(job)), None) \
            or self._channel_from_db(job)

    def _streamer_login(self, job: dict) -> str | None:
        s = self.db.streamer(job["streamer_id"]) if job.get("streamer_id") else None
        return s["login"] if s else None

    def _channel_from_db(self, job: dict):
        from .server import streamer_channel
        s = self.db.streamer(job["streamer_id"])
        return streamer_channel(self.cfg, s) if s else None

    async def _analyze(self, jid: int) -> None:
        job = self.db.job(jid)
        if not job:
            return
        loop = asyncio.get_running_loop()

        def progress(p: float, msg: str) -> None:
            loop.call_soon_threadsafe(lambda: self.db.update_job(jid, progress=round(p, 3), message=msg))

        self.db.update_job(jid, status="analyzing", progress=0, message="reading video info")
        info = await asyncio.to_thread(ytdlp_info, job["url"])
        duration = float(info.get("duration") or 0)
        thumb = info.get("thumbnail") or ""
        meta = {"uploader": info.get("uploader") or info.get("channel") or "", "extractor": info.get("extractor_key"),
                "webpage_url": info.get("webpage_url") or job["url"], "thumbnail": thumb,
                "was_live": bool(info.get("was_live")), "upload_date": info.get("upload_date"),
                "height": info.get("height"), "has_heatmap": bool(info.get("heatmap"))}
        self.db.update_job(jid, title=info.get("title") or job["url"], duration=duration, info=meta)
        ch = self._channel_for(job)
        det_cfg = ch.detector if ch else self.cfg.detector
        clip_cfg = ch.clip if ch else self.cfg.clip

        cands = heatmap_candidates(info)
        signals = {"replayed": len(cands)}
        chat_n = 0
        if meta["was_live"] or "twitch" in (meta["extractor"] or "").lower():
            progress(0.0, "reading chat replay")
            chat, chat_n = await asyncio.to_thread(chat_candidates, meta["webpage_url"], duration,
                                                   det_cfg, clip_cfg, progress)
            cands += chat
            signals["chat"] = len(chat)
        progress(0.0, "measuring loudness")
        try:
            loud = await asyncio.to_thread(loud_candidates, info, duration, progress)
        except Exception as e:  # noqa: BLE001 - loudness is a bonus signal
            log.warning("loudness pass failed for job %d: %r", jid, e)
            loud = []
        cands += loud
        signals["loud"] = len(loud)
        merged = merge_candidates(cands, duration, clip_cfg)
        meta.update({"signals": signals, "chat_messages": chat_n})
        self.db.update_job(jid, status="ready", progress=1, candidates=merged, info=meta,
                           message=f"{len(merged)} candidate moments")

    # ---- rendering ------------------------------------------------------

    async def render(self, jid: int, items: list[RenderItem]) -> int:
        job = self.db.job(jid)
        if not job:
            raise HTTPException(404, "no such link")
        if job["status"] in ("queued", "analyzing"):
            raise HTTPException(409, "still analyzing")
        info = await asyncio.to_thread(ytdlp_info, job["url"])       # fresh URLs: they expire
        inputs, vmap, amap = media_inputs(info)
        ch = self._channel_for(job)
        streamer = self.db.streamer(job["streamer_id"]) if job.get("streamer_id") else None
        login = streamer["login"] if streamer else re.sub(r"[^a-z0-9_]+", "", (job["info"].get("uploader") or "link").lower())[:30] or "link"
        display = streamer["display_name"] if streamer else (job["info"].get("uploader") or login)
        credit = streamer["credit_url"] if streamer else job["info"].get("webpage_url", job["url"])
        permission = (streamer["permission"] if streamer else "") or job["permission"]
        duration = job["duration"] or info.get("duration") or 0
        now = time.time()
        day = datetime.now().strftime("%Y-%m-%d")
        st = self.renders.setdefault(jid, {"total": 0, "done": 0, "failed": 0})
        st["total"] += len(items)
        self.db.update_job(jid, status="rendering", message=f"rendering {st['done']}/{st['total']}")
        for i, it in enumerate(items):
            start, end = max(0.0, it.start), min(it.end, duration) if duration else it.end
            if end - start < 3 or end - start > 180:
                raise HTTPException(422, "clips must be 3 s to 3 min long")
            cand = job["candidates"][it.candidate] if it.candidate is not None and \
                it.candidate < len(job["candidates"]) else {}
            hh = int(start // 3600)
            mm, ss = divmod(int(start % 3600), 60)
            folder = self.cfg.output_dir / day / f"{login}_link{jid}_{hh:01d}h{mm:02d}m{ss:02d}s"
            moment = cand.get("moment", start + (end - start) * 2 / 3)
            payload = {
                "id": f"link-{jid}-{i}-{int(now)}", "source": "link", "link_job_id": jid,
                "source_url": job["info"].get("webpage_url", job["url"]), "source_title": job["title"],
                "source_offset": round(start, 1),
                "channel": login, "display_name": display, "credit_url": credit, "permission": permission,
                "input": {"args": inputs, "video": vmap, "audio": amap},
                "start": start, "end": end, "moment_wall": now + i * 0.001,
                "trigger": {"moment": moment, "onset": moment, "detected_at": now,
                            "peak_score": cand.get("score", 0), "baseline": 0, "ratio": cand.get("ratio", 0),
                            "top_messages": cand.get("top_messages", []),
                            "reaction_counts": cand.get("reaction_counts", {})},
                "label": it.label or ", ".join(cand.get("details", [])),
                "folder": str(folder),
                "layout": self.sup.layout_for(ch),
            }
            self.sup.submit(payload, score=-1.0, on_done=lambda msg, jid=jid: self._render_done(jid, msg))
        return len(items)

    def _render_done(self, jid: int, msg: dict) -> None:
        st = self.renders.setdefault(jid, {"total": 0, "done": 0, "failed": 0})
        st["done" if msg.get("ok") else "failed"] += 1
        finished = st["done"] + st["failed"] >= st["total"]
        text = f"rendered {st['done']}/{st['total']}" + (f", {st['failed']} failed" if st["failed"] else "")
        if not msg.get("ok"):
            text += f" (last error: {msg.get('error', '')[:200]})"
        self.db.update_job(jid, status="done" if finished else "rendering", message=text)

    # ---- routes ---------------------------------------------------------

    def _routes(self, app: FastAPI) -> None:
        @app.get("/api/links")
        def list_links():
            return [self._job_out(j) for j in self.db.jobs()]

        @app.post("/api/links", status_code=201)
        def add_link(body: LinkIn):
            url = body.url.strip()
            if not URL_RE.match(url):
                raise HTTPException(422, "paste a full link starting with https://")
            streamer = self.db.streamer(body.streamer_id) if body.streamer_id else None
            if body.streamer_id and not streamer:
                raise HTTPException(404, "no such streamer")
            permission = (streamer["permission"] if streamer else "") or body.permission.strip()
            if not permission:
                raise HTTPException(422, "pick a streamer who gave permission, or write a permission note")
            job = self.db.add_job(url, body.streamer_id, body.permission.strip())
            self.queue.put_nowait(job["id"])
            return self._job_out(job)

        @app.get("/api/links/{jid}")
        def get_link(jid: int):
            job = self.db.job(jid)
            if not job:
                raise HTTPException(404, "no such link")
            return self._job_out(job)

        @app.post("/api/links/{jid}/render")
        async def render_link(jid: int, body: RenderIn):
            n = await self.render(jid, body.items)
            return {"queued": n}

        @app.post("/api/links/{jid}/retry")
        def retry_link(jid: int):
            job = self.db.job(jid)
            if not job:
                raise HTTPException(404, "no such link")
            self.db.update_job(jid, status="queued", progress=0, message="waiting")
            self.queue.put_nowait(jid)
            return self._job_out(self.db.job(jid))

        @app.delete("/api/links/{jid}", status_code=204)
        def delete_link(jid: int):
            self.db.delete_job(jid)

    def _job_out(self, job: dict) -> dict:
        clips = self.db.clips(source="link", limit=500)
        job = dict(job)
        job["clip_ids"] = [c["id"] for c in clips if c.get("link_job_id") == job["id"]]
        job["queue_position"] = None
        return job


def fmt_time(t: float) -> str:
    t = max(0, int(math.floor(t)))
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
