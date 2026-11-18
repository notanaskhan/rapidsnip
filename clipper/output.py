"""Clip folders and metadata."""
from __future__ import annotations

import json
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


def suggest_title(display_name: str, reactions: dict[str, int], transcript: str = "") -> str:
    hook = ""
    words = transcript.strip().split()
    if 2 <= len(words):
        hook = " ".join(words[:8]).strip(" ,.") + ("…" if len(words) > 8 else "")
        hook = f'"{hook}"'
    elif reactions:
        top = Counter(reactions).most_common(1)[0][0]
        hook = HOOKS.get(top, top)
    return f"{display_name} in GTA 6 {hook} #GTA6".replace("  ", " ").strip()


def suggest_description(display_name: str, credit_url: str) -> str:
    return (f"{display_name} playing GTA 6 live.\n"
            f"Clip from {credit_url}, used with permission.\n"
            f"Watch the full stream: {credit_url}\n\n#GTA6 #GTAVI #{display_name.replace(' ', '')}")


def write_meta(folder: Path, job: dict, transcript: str = "", timings: dict | None = None) -> Path:
    t = job["trigger"]
    meta = {
        "channel": job["channel"],
        "display_name": job["display_name"],
        "credit_url": job["credit_url"],
        "permission": job["permission"],
        "moment": t["moment"],
        "moment_local": datetime.fromtimestamp(t["moment"]).isoformat(timespec="seconds"),
        "window": {"start": job["start"], "end": job["end"], "seconds": round(job["end"] - job["start"], 2)},
        "onset": t["onset"],
        "detected_at": t["detected_at"],
        "score": round(t["peak_score"], 1),
        "baseline": round(t["baseline"], 1),
        "ratio": round(t["ratio"], 2),
        "top_messages": t["top_messages"],
        "reaction_counts": t["reaction_counts"],
        "transcript": transcript,
        "suggested_title": suggest_title(job["display_name"], t["reaction_counts"], transcript),
        "suggested_description": suggest_description(job["display_name"], job["credit_url"]),
        "timings": timings or {},
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    path = folder / "meta.json"
    path.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    return path
