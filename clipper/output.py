"""Clip folders, metadata, and the clips/index.html review page."""
from __future__ import annotations

import html
import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path

HOOKS = {
    "KEKW": "😂", "LUL": "😂", "LULW": "😂", "OMEGALUL": "😂", "LMAO": "😂", "LMFAO": "😂", "😂": "😂",
    "💀": "💀", "WTF": "WTF just happened", "NO WAY": "NO WAY 😳", "HOLY": "HOLY 😳",
    "Pog": "INSANE", "PogChamp": "INSANE", "CLIP": "CLIP IT", "CLIP IT": "CLIP IT",
    "monkaS": "😬", "W": "W", "L": "L",
}


def clip_folder(output_dir: Path, channel: str, moment: float, ratio: float) -> Path:
    dt = datetime.fromtimestamp(moment)
    return Path(output_dir) / dt.strftime("%Y-%m-%d") / f"{channel}_{dt.strftime('%H%M%S')}_{ratio:.1f}x"


SKIP_HOOKS = {"W", "L"}           # one-letter reactions make meaningless titles


def suggest_title(display_name: str, reactions: dict[str, int], transcript: str = "",
                  source: str = "live") -> str:
    hook = ""
    words = transcript.strip().split()
    if len(words) >= 2:
        hook = " ".join(words[:8]).strip(" ,.") + ("…" if len(words) > 8 else "")
        hook = f'"{hook}"'
    else:
        top = next((r for r, _ in Counter(reactions).most_common() if r not in SKIP_HOOKS), None)
        if top:
            hook = HOOKS.get(top, top)
    if source == "link":                       # any video: don't assume the game
        return f"{display_name} {hook}".strip() or display_name
    return f"{display_name} in GTA 6 {hook} #GTA6".replace("  ", " ").strip()


def suggest_description(display_name: str, credit_url: str) -> str:
    return (f"{display_name} playing GTA 6 live.\n"
            f"Clip from {credit_url}, used with permission.\n"
            f"Watch the full stream: {credit_url}\n\n#GTA6 #GTAVI #{display_name.replace(' ', '')}")


def write_meta(folder: Path, job: dict, transcript: str = "", timings: dict | None = None) -> Path:
    t = job["trigger"]
    meta = {
        "source": job.get("source", "live"),
        "channel": job["channel"],
        "display_name": job["display_name"],
        "credit_url": job["credit_url"],
        "permission": job["permission"],
        "moment": job.get("moment_wall", t["moment"]),
        "moment_local": datetime.fromtimestamp(job.get("moment_wall", t["moment"])).isoformat(timespec="seconds"),
        "window": {"start": job["start"], "end": job["end"], "seconds": round(job["end"] - job["start"], 2)},
        "onset": t["onset"],
        "detected_at": t["detected_at"],
        "score": round(t["peak_score"], 1),
        "baseline": round(t["baseline"], 1),
        "ratio": round(t["ratio"], 2),
        "top_messages": t["top_messages"],
        "reaction_counts": t["reaction_counts"],
        "transcript": transcript,
        "suggested_title": suggest_title(job["display_name"], t["reaction_counts"], transcript,
                                         job.get("source", "live")),
        "suggested_description": suggest_description(job["display_name"], job["credit_url"]),
        "timings": timings or {},
        **({"link_job_id": job["link_job_id"], "source_url": job.get("source_url", ""),
            "source_title": job.get("source_title", ""), "source_offset": job.get("source_offset")}
           if job.get("source") == "link" else {}),
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    path = folder / "meta.json"
    path.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    return path


# ---- review page ------------------------------------------------------

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Clip Review</title>
<style>
:root { --bg:#f6f6f4; --card:#fff; --text:#1b1b1b; --muted:#6b6b6b; --line:#e3e3df; --accent:#9146ff; --ok:#1f8a4c; }
@media (prefers-color-scheme: dark) { :root { --bg:#111214; --card:#1b1c1f; --text:#ececec; --muted:#9a9a9a; --line:#2c2d31; --accent:#a970ff; --ok:#4cc38a; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
header { position:sticky; top:0; z-index:1; background:var(--bg); border-bottom:1px solid var(--line); padding:12px 16px; display:flex; gap:12px; align-items:baseline; flex-wrap:wrap; }
header h1 { font-size:18px; margin:0; }
header .meta { color:var(--muted); font-size:13px; }
main { padding:16px; display:grid; gap:16px; grid-template-columns:repeat(auto-fill,minmax(min(100%,640px),1fr)); }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px; display:grid; grid-template-columns:minmax(0,270px) minmax(0,1fr); gap:14px; }
@media (max-width:640px) { .card { grid-template-columns:1fr; } }
video { width:100%; max-height:480px; background:#000; border-radius:8px; aspect-ratio:9/16; object-fit:contain; }
video.wide { aspect-ratio:16/9; }
h2 { font-size:16px; margin:0 0 4px; }
.sub { color:var(--muted); font-size:13px; margin-bottom:8px; }
.ratio { display:inline-block; background:var(--accent); color:#fff; border-radius:999px; padding:1px 8px; font-size:12px; font-weight:600; margin-left:6px; }
.chat { margin:8px 0; padding:0; list-style:none; font-size:13px; max-height:150px; overflow:auto; border-left:3px solid var(--line); padding-left:8px; }
.chat li span { color:var(--muted); }
label { display:block; font-size:12px; color:var(--muted); margin-top:8px; }
textarea { width:100%; border:1px solid var(--line); border-radius:6px; background:var(--bg); color:var(--text); font-family:inherit; font-size:13px; line-height:1.4; padding:6px; resize:vertical; }
.row { display:flex; gap:8px; flex-wrap:wrap; margin-top:8px; align-items:center; }
button { border:1px solid var(--accent); background:var(--accent); color:#fff; border-radius:6px; padding:6px 12px; font-size:13px; cursor:pointer; }
button.ghost { background:transparent; color:var(--accent); }
a { color:var(--accent); }
.copied { color:var(--ok); font-size:13px; }
.empty { color:var(--muted); padding:40px 16px; }
</style></head><body>
<header><h1>Clip Review</h1><span class="meta">__COUNT__ · updated __UPDATED__ · newest first · watch every clip to the end before posting</span></header>
<main>
__CARDS__
</main>
<script>
async function copyText(text, el) {
  let ok = false;
  try { await navigator.clipboard.writeText(text); ok = true; } catch (e) {}
  if (!ok) {
    const t = document.createElement("textarea"); t.value = text; document.body.appendChild(t);
    t.select(); try { ok = document.execCommand("copy"); } catch (e) {} t.remove();
  }
  const note = el.parentElement.querySelector(".copied");
  note.textContent = ok ? "Copied" : "Copy failed: select the text and press \u2318C";
  setTimeout(() => note.textContent = "", 2500);
}
document.addEventListener("click", e => {
  const b = e.target.closest("button[data-copy]"); if (!b) return;
  const card = b.closest(".card");
  const title = card.querySelector("textarea.title").value, desc = card.querySelector("textarea.desc").value;
  copyText(b.dataset.copy === "title" ? title : b.dataset.copy === "desc" ? desc : title + "\n\n" + desc, b);
});
</script></body></html>
"""


def _card(folder: Path, meta: dict, root: Path) -> str:
    rel = lambda name: html.escape(os.path.relpath(folder / name, root))  # noqa: E731
    if (folder / "vertical.mp4").exists():
        # No poster: thumb.jpg is 16:9. The #t fragment makes the player show a real vertical frame.
        video = f'<video controls preload="metadata" src="{rel("vertical.mp4")}#t=0.5"></video>'
    else:
        name = "horizontal.mp4" if (folder / "horizontal.mp4").exists() else "horizontal_raw.mp4"
        video = (f'<video class="wide" controls preload="metadata" src="{rel(name)}" '
                 f'poster="{rel("thumb.jpg")}"></video>')
    links = [f'<a href="{rel(n)}">{n}</a>' for n in ("vertical.mp4", "horizontal.mp4", "horizontal_raw.mp4")
             if (folder / n).exists()]
    moment = datetime.fromtimestamp(meta["moment"])
    ready = ""
    try:
        ready_s = datetime.fromisoformat(meta["created"]).timestamp() - meta["moment"]
        ready = f" · ready {ready_s:.0f}s after the moment"
    except (KeyError, ValueError):
        pass
    chat = "".join(f"<li>{html.escape(str(t))} <span>×{n}</span></li>" for t, n in meta.get("top_messages", [])[:10])
    transcript = html.escape(meta.get("transcript") or "(no captions)")
    return f"""<article class="card">
  <div>{video}</div>
  <div>
    <h2>{html.escape(meta.get("display_name", meta["channel"]))}<span class="ratio">{meta.get("ratio", 0):.1f}x</span></h2>
    <div class="sub">{moment:%a %H:%M:%S} · {meta["window"]["seconds"]:.0f}s{ready} · {" · ".join(links)}</div>
    <div class="sub">Credit: <a href="{html.escape(meta["credit_url"])}">{html.escape(meta["credit_url"])}</a> · permission: {html.escape(meta.get("permission", ""))}</div>
    <ul class="chat">{chat}</ul>
    <div class="sub">Transcript: {transcript}</div>
    <label>Title</label><textarea class="title" rows="2">{html.escape(meta.get("suggested_title", ""))}</textarea>
    <label>Description</label><textarea class="desc" rows="4">{html.escape(meta.get("suggested_description", ""))}</textarea>
    <div class="row"><button data-copy="both">Copy title + description</button>
      <button class="ghost" data-copy="title">Title</button><button class="ghost" data-copy="desc">Description</button>
      <span class="copied" aria-live="polite"></span></div>
  </div>
</article>"""


def write_index(output_dir: Path) -> Path:
    """Regenerate clips/index.html from every clip's meta.json, newest moment first."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    clips = []
    for meta_path in root.glob("*/*/meta.json"):
        try:
            clips.append((meta_path.parent, json.loads(meta_path.read_text())))
        except (OSError, ValueError):
            continue
    clips.sort(key=lambda c: c[1].get("moment", 0), reverse=True)
    cards = "\n".join(_card(f, m, root) for f, m in clips) or '<p class="empty">No clips yet.</p>'
    page = (PAGE.replace("__COUNT__", f"{len(clips)} clip{'s' if len(clips) != 1 else ''}")
                .replace("__UPDATED__", datetime.now().strftime("%H:%M:%S"))
                .replace("__CARDS__", cards))
    out = root / "index.html"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(page, encoding="utf-8")
    tmp.replace(out)
    return out
