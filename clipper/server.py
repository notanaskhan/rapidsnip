"""HTTP API for the web UI. `uv run python -m clipper serve` runs it with the live supervisor inside.

Routes (all JSON unless noted):
  GET    /api/status                      live channels, queue, chat, disk, clip counts
  GET    /api/streamers                   list
  POST   /api/streamers                   add {platform, login, display_name?, permission, credit_url?, ...}
  PATCH  /api/streamers/{id}              edit any field (applied to the running recorder immediately)
  DELETE /api/streamers/{id}
  GET    /api/clips?status=&channel=&source=
  GET    /api/clips/{id}
  PATCH  /api/clips/{id}                  {status?, title?, description?}
  DELETE /api/clips/{id}?files=true       remove from the list (and delete files)
  GET    /api/clips/{id}/download/{kind}  vertical | horizontal | thumb (attachment)
  GET    /api/settings                    global detector / clip / captions / layout settings
  GET    /media/<folder>/<file>           clip files (range requests, so video seeks)
  + link clipper routes from clipper.links
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import PLATFORMS, LIVE_PLATFORMS, ChannelConfig, Config, OVERRIDE_KEYS, channel_from_dict
from .db import CLIP_STATUSES, DB
from .output import write_index
from .supervisor import Supervisor

log = logging.getLogger(__name__)

LOGIN_RE = {
    "twitch": re.compile(r"^[a-z0-9_]{3,25}$"),
    "youtube": re.compile(r"^[A-Za-z0-9_.\-]{3,100}$"),
    "kick": re.compile(r"^[a-z0-9_\-]{3,25}$"),
}
FILES = {"vertical": "vertical.mp4", "horizontal": "horizontal.mp4", "thumb": "thumb.jpg"}


class StreamerIn(BaseModel):
    platform: str = "twitch"
    login: str
    display_name: str | None = None
    permission: str = ""
    credit_url: str | None = None
    enabled: bool = True
    facecam: list[int] | None = Field(default=None, min_length=4, max_length=4)
    gameplay: list[int] | None = Field(default=None, min_length=4, max_length=4)
    overrides: dict = Field(default_factory=dict)


class StreamerPatch(BaseModel):
    platform: str | None = None
    login: str | None = None
    display_name: str | None = None
    permission: str | None = None
    credit_url: str | None = None
    enabled: bool | None = None
    facecam: list[int] | None = Field(default=None, min_length=4, max_length=4)
    gameplay: list[int] | None = Field(default=None, min_length=4, max_length=4)
    overrides: dict | None = None
    clear_facecam: bool = False
    clear_gameplay: bool = False


class ClipPatch(BaseModel):
    status: str | None = None
    title: str | None = None
    description: str | None = None


def streamer_channel(cfg: Config, row: dict) -> ChannelConfig:
    d = {"name": row["login"], "display_name": row["display_name"], "permission": row["permission"],
         "credit_url": row["credit_url"] or None, "platform": row["platform"], "enabled": row["enabled"],
         "layout": {"facecam": row["facecam"], "gameplay": row["gameplay"]}}
    d.update({k: v for k, v in (row.get("overrides") or {}).items() if k in OVERRIDE_KEYS})
    return channel_from_dict(d, cfg.detector, cfg.clip)


def db_channels(cfg: Config, db: DB) -> list[ChannelConfig]:
    out = []
    for row in db.streamers():
        try:
            out.append(streamer_channel(cfg, row))
        except ValueError as e:
            log.warning("skipping streamer %s: %s", row.get("login"), e)
    return out


def _validate_streamer(data: dict) -> dict:
    platform = (data.get("platform") or "twitch").lower()
    if platform not in PLATFORMS:
        raise HTTPException(422, f"platform must be one of {', '.join(PLATFORMS)}")
    login = (data.get("login") or "").strip()
    login = re.sub(r"^https?://(www\.)?(twitch\.tv|youtube\.com|kick\.com)/@?", "", login).strip("/@ ")
    if platform in ("twitch", "kick"):
        login = login.lower()
    if not LOGIN_RE[platform].match(login):
        raise HTTPException(422, f"'{login}' doesn't look like a {platform} channel name")
    unknown = set(data.get("overrides") or {}) - OVERRIDE_KEYS
    if unknown:
        raise HTTPException(422, f"unknown overrides: {', '.join(sorted(unknown))}")
    data["platform"], data["login"] = platform, login
    if not data.get("display_name"):
        data["display_name"] = login
    if not data.get("credit_url"):
        data["credit_url"] = {"twitch": f"https://twitch.tv/{login}", "youtube": f"https://www.youtube.com/@{login}",
                              "kick": f"https://kick.com/{login}"}[platform]
    return data


def create_app(cfg: Config, db: DB, live: bool = True, links=None) -> FastAPI:
    sup = Supervisor(cfg, db=db)
    clips_dir = cfg.output_dir

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        added = db.sync_clips(clips_dir)
        if added:
            log.info("indexed %d existing clips", added)
        await sup.set_channels(db_channels(cfg, db))
        await sup.start(live=live)
        if links:
            links.attach(sup, app)
            await links.start()
        log.info("API ready (live recording %s)", "on" if live else "off")
        try:
            yield
        finally:
            if links:
                await links.stop()
            await sup.stop()

    app = FastAPI(title="Clipper", lifespan=lifespan)
    app.state.sup, app.state.db, app.state.cfg = sup, db, cfg
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3210", "http://127.0.0.1:3210"],
                       allow_methods=["*"], allow_headers=["*"])

    async def apply_streamers():
        await sup.set_channels(db_channels(cfg, db))

    # ---- status -------------------------------------------------------

    @app.get("/api/status")
    def status():
        du = shutil.disk_usage(cfg.data_dir)
        st = sup.status()
        st["live_recording"] = live
        st["disk"] = {"free_gb": round(du.free / 1e9, 1), "total_gb": round(du.total / 1e9, 1),
                      "min_free_gb": cfg.min_free_gb}
        st["clips"] = db.clip_counts()
        st["now"] = time.time()
        return st

    # ---- streamers ------------------------------------------------------

    def streamer_out(row: dict) -> dict:
        row = dict(row)
        live_row = next((c for c in sup.status()["channels"] if c["name"] == row["login"]), None)
        row["live"] = bool(live_row and live_row["live"])
        row["recordable"] = bool(row["permission"].strip() and row["enabled"] and row["platform"] in LIVE_PLATFORMS)
        row["note"] = ("" if row["recordable"] else
                       "needs a permission note" if not row["permission"].strip() else
                       "disabled" if not row["enabled"] else
                       f"live recording on {row['platform']} isn't supported yet; use Links for its videos")
        return row

    @app.get("/api/streamers")
    def list_streamers():
        return [streamer_out(r) for r in db.streamers()]

    @app.post("/api/streamers", status_code=201)
    async def add_streamer(body: StreamerIn):
        data = _validate_streamer(body.model_dump())
        if any(s["platform"] == data["platform"] and s["login"] == data["login"] for s in db.streamers()):
            raise HTTPException(409, f"{data['login']} on {data['platform']} is already added")
        row = db.add_streamer(data)
        await apply_streamers()
        return streamer_out(row)

    @app.patch("/api/streamers/{sid}")
    async def edit_streamer(sid: int, body: StreamerPatch):
        cur = db.streamer(sid)
        if not cur:
            raise HTTPException(404, "no such streamer")
        patch = body.model_dump(exclude_unset=True)
        clear_f, clear_g = patch.pop("clear_facecam", False), patch.pop("clear_gameplay", False)
        merged = _validate_streamer({**cur, **{k: v for k, v in patch.items() if v is not None}})
        if clear_f:
            merged["facecam"] = None
        if clear_g:
            merged["gameplay"] = None
        clash = [s for s in db.streamers() if s["id"] != sid and s["platform"] == merged["platform"]
                 and s["login"] == merged["login"]]
        if clash:
            raise HTTPException(409, f"{merged['login']} on {merged['platform']} is already added")
        row = db.update_streamer(sid, merged)
        await apply_streamers()
        return streamer_out(row)

    @app.delete("/api/streamers/{sid}", status_code=204)
    async def delete_streamer(sid: int):
        db.delete_streamer(sid)
        await apply_streamers()

    # ---- clips ------------------------------------------------------------

    def clip_out(row: dict) -> dict:
        folder = clips_dir / row["folder"]
        meta = {}
        try:
            meta = json.loads((folder / "meta.json").read_text())
        except (OSError, ValueError):
            pass
        files = {k: f"/media/{row['folder']}/{name}" for k, name in FILES.items() if (folder / name).exists()}
        if "horizontal" not in files and (folder / "horizontal_raw.mp4").exists():
            files["horizontal"] = f"/media/{row['folder']}/horizontal_raw.mp4"
        ready_after = None
        try:
            from datetime import datetime
            ready_after = round(datetime.fromisoformat(meta["created"]).timestamp() - meta["moment"])
        except (KeyError, ValueError, TypeError):
            pass
        return {
            **row,
            "display_name": meta.get("display_name", row["channel"]),
            "credit_url": meta.get("credit_url", ""),
            "permission": meta.get("permission", ""),
            "files": files,
            "exists": folder.exists(),
            "top_messages": meta.get("top_messages", [])[:10],
            "reaction_counts": meta.get("reaction_counts", {}),
            "transcript": meta.get("transcript", ""),
            "timings": meta.get("timings", {}),
            "ready_after": ready_after if row["source"] == "live" else None,
            "source_url": meta.get("source_url", ""),
            "source_title": meta.get("source_title", ""),
            "source_offset": meta.get("source_offset"),
            "suggested_title": meta.get("suggested_title", ""),
        }

    @app.get("/api/clips")
    def list_clips(status: str | None = None, channel: str | None = None, source: str | None = None,
                   limit: int = 200):
        db.sync_clips(clips_dir)
        return [clip_out(r) for r in db.clips(status, channel, source, limit)]

    @app.get("/api/clips/{cid}")
    def get_clip(cid: int):
        row = db.clip(cid)
        if not row:
            raise HTTPException(404, "no such clip")
        return clip_out(row)

    @app.patch("/api/clips/{cid}")
    def edit_clip(cid: int, body: ClipPatch):
        if not db.clip(cid):
            raise HTTPException(404, "no such clip")
        patch = body.model_dump(exclude_unset=True)
        if "status" in patch and patch["status"] not in CLIP_STATUSES:
            raise HTTPException(422, f"status must be one of {', '.join(CLIP_STATUSES)}")
        return clip_out(db.update_clip(cid, patch))

    @app.delete("/api/clips/{cid}", status_code=204)
    def delete_clip(cid: int, files: bool = False):
        row = db.clip(cid)
        if not row:
            return
        if files:
            folder = (clips_dir / row["folder"]).resolve()
            if clips_dir.resolve() in folder.parents and folder.exists():
                shutil.rmtree(folder)
            write_index(clips_dir)
        db.delete_clip(cid)

    @app.get("/api/clips/{cid}/download/{kind}")
    def download(cid: int, kind: str):
        row = db.clip(cid)
        if not row or kind not in FILES:
            raise HTTPException(404, "not found")
        path = clips_dir / row["folder"] / FILES[kind]
        if not path.exists() and kind == "horizontal":
            path = clips_dir / row["folder"] / "horizontal_raw.mp4"
        if not path.exists():
            raise HTTPException(404, f"{kind} not rendered for this clip")
        name = f"{row['folder'].split('/')[-1]}_{kind}{path.suffix}"
        return FileResponse(path, filename=name)

    # ---- settings -----------------------------------------------------------

    @app.get("/api/settings")
    def settings():
        return {"detector": asdict(cfg.detector), "clip": asdict(cfg.clip), "captions": asdict(cfg.captions),
                "layout": asdict(cfg.layout), "buffer_minutes": cfg.buffer_minutes, "quality": cfg.quality,
                "platforms": list(PLATFORMS), "live_platforms": list(LIVE_PLATFORMS),
                "override_keys": sorted(OVERRIDE_KEYS)}

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.exception_handler(ValueError)
    async def value_error(_: Request, exc: ValueError):
        raise HTTPException(422, str(exc))

    clips_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=clips_dir), name="media")
    return app
