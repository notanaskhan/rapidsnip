"""Grab a frame so facecam/gameplay crop boxes can be read off a pixel grid.

    uv run python tools/calibrate.py <channel>                 # live frame -> calibrate_<channel>.jpg
    uv run python tools/calibrate.py <channel> --from clip.mp4  # use a saved video instead
    uv run python tools/calibrate.py <channel> --preview        # also render the vertical layout
                                                                #   with the boxes in config.yaml

Grid lines every 100 px, labelled every 200 px, in 1920x1080 coordinates (the
units config.yaml uses). Paste the boxes as [x, y, w, h] under the channel's layout.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clipper.config import load_config  # noqa: E402
from clipper.layout import plan_vertical  # noqa: E402
from clipper.recorder import _tool  # noqa: E402

FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def grab_live(channel: str, out: Path) -> None:
    sl = subprocess.Popen([_tool("streamlink"), "--loglevel", "error", "--twitch-supported-codecs", "h264",
                           "--stdout", f"https://twitch.tv/{channel}", "1080p60,1080p,720p60,best"],
                          stdout=subprocess.PIPE)
    try:
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", "pipe:0", "-ss", "2",
                        "-frames:v", "1", "-vf", "scale=1920:1080", str(out)], stdin=sl.stdout, check=True,
                       timeout=60)
    finally:
        sl.kill()


def grab_file(video: Path, out: Path, at: float) -> None:
    # Output seeking: input seeking on a lone .ts segment returns no frame.
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(video), "-ss", str(at),
                    "-frames:v", "1", "-vf", "scale=1920:1080", str(out)], check=True)


def grid(frame: Path, out: Path) -> None:
    parts = ["drawgrid=w=100:h=100:t=1:c=white@0.35"]
    for x in range(200, 1920, 200):
        parts.append(f"drawbox=x={x - 1}:y=0:w=3:h=1080:c=yellow@0.6:t=fill")
        parts.append(f"drawtext=fontfile='{FONT}':text='{x}':x={x + 4}:y=6:fontsize=24:fontcolor=yellow:"
                     f"box=1:boxcolor=black@0.6")
    for y in range(200, 1080, 200):
        parts.append(f"drawbox=x=0:y={y - 1}:w=1920:h=3:c=yellow@0.6:t=fill")
        parts.append(f"drawtext=fontfile='{FONT}':text='{y}':x=6:y={y + 4}:fontsize=24:fontcolor=yellow:"
                     f"box=1:boxcolor=black@0.6")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(frame), "-vf", ",".join(parts),
                    "-q:v", "3", str(out)], check=True)


def preview(frame: Path, out: Path, facecam, gameplay) -> None:
    plan = plan_vertical(1920, 1080, facecam, gameplay, 1080, 1920)
    graph = plan.filter.replace("[src]", "[0:v]", 1)
    graph += f";[v]drawbox=x=0:y={plan.caption_y - 45}:w=1080:h=90:c=red@0.35:t=fill[out]"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(frame), "-filter_complex", graph,
                    "-map", "[out]", "-q:v", "3", str(out)], check=True)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("channel")
    p.add_argument("--from", dest="src", type=Path, help="video file instead of the live stream")
    p.add_argument("--at", type=float, default=1.0, help="seconds into --from")
    p.add_argument("--preview", action="store_true")
    a = p.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        frame = Path(tmp) / "frame.png"
        if a.src:
            grab_file(a.src, frame, a.at)
        else:
            grab_live(a.channel, frame)
        out = Path(f"calibrate_{a.channel}.jpg")
        grid(frame, out)
        print(f"grid: {out}")
        if a.preview:
            ch = next((c for c in load_config().channels if c.name == a.channel), None)
            facecam = ch.layout.facecam if ch else None
            gameplay = ch.layout.gameplay if ch else None
            pv = Path(f"calibrate_{a.channel}_preview.jpg")
            preview(frame, pv, facecam, gameplay)
            print(f"preview (facecam={facecam}, gameplay={gameplay}; red band = captions): {pv}")


if __name__ == "__main__":
    main()
