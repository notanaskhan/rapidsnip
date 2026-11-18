"""SQLite store: streamers, clips (review state), link jobs.

Clip files and meta.json stay the source of truth for what a clip *is*; the database
adds what people do with it (status, edited title/description) and fast listing.
Each call opens its own connection, so this is safe from the event loop and threads.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS streamers (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL DEFAULT 'twitch',
    login TEXT NOT NULL,
    display_name TEXT NOT NULL,
    permission TEXT NOT NULL DEFAULT '',
    credit_url TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    facecam TEXT,                 -- JSON [x, y, w, h] or NULL
    gameplay TEXT,
    overrides TEXT NOT NULL DEFAULT '{}',   -- JSON: ratio_threshold, chat_delay_seconds, ...
    created REAL NOT NULL,
    UNIQUE (platform, login)
);
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY,
    folder TEXT NOT NULL UNIQUE,  -- relative to the clips dir, e.g. 2026-11-18/kaicenat_234056_4.0x
    channel TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'live',    -- live | link
    link_job_id INTEGER,
    moment REAL NOT NULL,
    ratio REAL NOT NULL DEFAULT 0,
    seconds REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'new',     -- new | approved | posted | rejected
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS clips_moment ON clips (moment DESC);
CREATE TABLE IF NOT EXISTS link_jobs (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL,
    streamer_id INTEGER,
    permission TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued',  -- queued | downloading | analyzing | ready | rendering | done | failed
    progress REAL NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    duration REAL NOT NULL DEFAULT 0,
    video_path TEXT NOT NULL DEFAULT '',
    info TEXT NOT NULL DEFAULT '{}',        -- JSON: uploader, platform, thumbnail, ...
    candidates TEXT NOT NULL DEFAULT '[]',  -- JSON list of candidate moments
    created REAL NOT NULL,
    updated REAL NOT NULL
);
"""

CLIP_STATUSES = ("new", "approved", "posted", "rejected")
STREAMER_JSON = ("facecam", "gameplay", "overrides")
JOB_JSON = ("info", "candidates")


class DB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        try:
            yield c
            c.commit()
        finally:
            c.close()

    # ---- streamers ----------------------------------------------------

    @staticmethod
    def _streamer(row) -> dict:
        d = dict(row)
        for k in STREAMER_JSON:
            d[k] = json.loads(d[k]) if d[k] else (None if k != "overrides" else {})
        d["enabled"] = bool(d["enabled"])
        return d

    def streamers(self) -> list[dict]:
        with self.conn() as c:
            return [self._streamer(r) for r in c.execute("SELECT * FROM streamers ORDER BY display_name COLLATE NOCASE")]

    def streamer(self, sid: int) -> dict | None:
        with self.conn() as c:
            r = c.execute("SELECT * FROM streamers WHERE id = ?", (sid,)).fetchone()
            return self._streamer(r) if r else None

    def add_streamer(self, data: dict) -> dict:
        with self.conn() as c:
            cur = c.execute(
                "INSERT INTO streamers (platform, login, display_name, permission, credit_url, enabled,"
                " facecam, gameplay, overrides, created) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (data["platform"], data["login"], data["display_name"], data.get("permission", ""),
                 data.get("credit_url", ""), int(data.get("enabled", True)),
                 json.dumps(data["facecam"]) if data.get("facecam") else None,
                 json.dumps(data["gameplay"]) if data.get("gameplay") else None,
                 json.dumps(data.get("overrides") or {}), time.time()))
            sid = cur.lastrowid
        return self.streamer(sid)

    def update_streamer(self, sid: int, data: dict) -> dict | None:
        fields = {k: v for k, v in data.items() if k in (
            "platform", "login", "display_name", "permission", "credit_url", "enabled",
            "facecam", "gameplay", "overrides")}
        if fields:
            sets, vals = [], []
            for k, v in fields.items():
                if k in STREAMER_JSON:
                    v = json.dumps(v) if v not in (None, [], "") or k == "overrides" else None
                elif k == "enabled":
                    v = int(bool(v))
                sets.append(f"{k} = ?")
                vals.append(v)
            with self.conn() as c:
                c.execute(f"UPDATE streamers SET {', '.join(sets)} WHERE id = ?", (*vals, sid))
        return self.streamer(sid)

    def delete_streamer(self, sid: int) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM streamers WHERE id = ?", (sid,))

    # ---- clips ----------------------------------------------------------

    def upsert_clip_from_meta(self, folder_rel: str, meta: dict, source: str = "live",
                              link_job_id: int | None = None) -> None:
        now = time.time()
        with self.conn() as c:
            c.execute(
                "INSERT INTO clips (folder, channel, source, link_job_id, moment, ratio, seconds, title,"
                " description, created, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(folder) DO NOTHING",
                (folder_rel, meta.get("channel", ""), meta.get("source", source),
                 meta.get("link_job_id", link_job_id), meta.get("moment", now), meta.get("ratio", 0),
                 meta.get("window", {}).get("seconds", 0), meta.get("suggested_title", ""),
                 meta.get("suggested_description", ""), now, now))

    def clips(self, status: str | None = None, channel: str | None = None, source: str | None = None,
              limit: int = 200) -> list[dict]:
        q, args = "SELECT * FROM clips WHERE 1=1", []
        if status:
            q += " AND status = ?"
            args.append(status)
        if channel:
            q += " AND channel = ?"
            args.append(channel)
        if source:
            q += " AND source = ?"
            args.append(source)
        q += " ORDER BY moment DESC LIMIT ?"
        args.append(limit)
        with self.conn() as c:
            return [dict(r) for r in c.execute(q, args)]

    def clip(self, cid: int) -> dict | None:
        with self.conn() as c:
            r = c.execute("SELECT * FROM clips WHERE id = ?", (cid,)).fetchone()
            return dict(r) if r else None

    def update_clip(self, cid: int, data: dict) -> dict | None:
        fields = {k: v for k, v in data.items() if k in ("status", "title", "description")}
        if "status" in fields and fields["status"] not in CLIP_STATUSES:
            raise ValueError(f"status must be one of {CLIP_STATUSES}")
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields) + ", updated = ?"
            with self.conn() as c:
                c.execute(f"UPDATE clips SET {sets} WHERE id = ?", (*fields.values(), time.time(), cid))
        return self.clip(cid)

    def delete_clip(self, cid: int) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM clips WHERE id = ?", (cid,))

    def clip_counts(self) -> dict:
        with self.conn() as c:
            return {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM clips GROUP BY status")}

    def sync_clips(self, clips_dir: Path) -> int:
        """Index every clips/*/*/meta.json that isn't in the database yet."""
        clips_dir = Path(clips_dir)
        with self.conn() as c:
            known = {r["folder"] for r in c.execute("SELECT folder FROM clips")}
        added = 0
        for meta_path in clips_dir.glob("*/*/meta.json"):
            rel = str(meta_path.parent.relative_to(clips_dir))
            if rel in known:
                continue
            try:
                meta = json.loads(meta_path.read_text())
            except (OSError, ValueError):
                continue
            self.upsert_clip_from_meta(rel, meta)
            added += 1
        return added

    # ---- link jobs ----------------------------------------------------

    @staticmethod
    def _job(row) -> dict:
        d = dict(row)
        for k in JOB_JSON:
            d[k] = json.loads(d[k]) if d[k] else ({} if k == "info" else [])
        return d

    def add_job(self, url: str, streamer_id: int | None, permission: str) -> dict:
        now = time.time()
        with self.conn() as c:
            cur = c.execute("INSERT INTO link_jobs (url, streamer_id, permission, created, updated)"
                            " VALUES (?,?,?,?,?)", (url, streamer_id, permission, now, now))
            jid = cur.lastrowid
        return self.job(jid)

    def job(self, jid: int) -> dict | None:
        with self.conn() as c:
            r = c.execute("SELECT * FROM link_jobs WHERE id = ?", (jid,)).fetchone()
            return self._job(r) if r else None

    def jobs(self, limit: int = 50) -> list[dict]:
        with self.conn() as c:
            return [self._job(r) for r in c.execute("SELECT * FROM link_jobs ORDER BY created DESC LIMIT ?", (limit,))]

    def update_job(self, jid: int, **fields) -> None:
        for k in JOB_JSON:
            if k in fields:
                fields[k] = json.dumps(fields[k])
        sets = ", ".join(f"{k} = ?" for k in fields) + ", updated = ?"
        with self.conn() as c:
            c.execute(f"UPDATE link_jobs SET {sets} WHERE id = ?", (*fields.values(), time.time(), jid))

    def delete_job(self, jid: int) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM link_jobs WHERE id = ?", (jid,))


def import_config_channels(db: DB, channels) -> int:
    """First start: copy config.yaml channels into an empty streamers table."""
    if db.streamers():
        return 0
    n = 0
    for ch in channels:
        db.add_streamer({
            "platform": ch.platform, "login": ch.name, "display_name": ch.display_name,
            "permission": ch.permission, "credit_url": ch.credit_url, "enabled": ch.enabled,
            "facecam": list(ch.layout.facecam) if ch.layout.facecam else None,
            "gameplay": list(ch.layout.gameplay) if ch.layout.gameplay else None,
        })
        n += 1
    return n
