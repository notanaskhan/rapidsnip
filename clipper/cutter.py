"""Join the covering segments and cut the exact window, re-encoding on the Apple encoder."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .recorder import Segment


@dataclass
class CutResult:
    video: Path
    thumb: Path
    duration: float
    moment_offset: float     # seconds into the clip where the estimated moment is


def run_ffmpeg(args: list[str]) -> None:
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.strip()[-2000:]}")


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def probe_size(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True,
    ).stdout.strip()
    w, h = out.split(",")[:2]
    return int(w), int(h)


def cut(segments: list[Segment], start: float, end: float, moment: float, out_dir: Path) -> CutResult:
    """Cut [start, end] (wall clock) from a contiguous run of segments into out_dir."""
    if not segments:
        raise ValueError("no segments")
    out_dir.mkdir(parents=True, exist_ok=True)
    concat = out_dir / "concat.txt"
    concat.write_text("".join(f"file '{s.path.resolve()}'\n" for s in segments))
    offset = max(0.0, start - segments[0].wall_start)
    duration = end - start
    video = out_dir / "horizontal_raw.mp4"
    run_ffmpeg([
        "-f", "concat", "-safe", "0", "-i", str(concat),
        "-ss", f"{offset:.3f}", "-t", f"{duration:.3f}",
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c:v", "h264_videotoolbox", "-b:v", "8M", "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart", str(video),
    ])
    concat.unlink(missing_ok=True)
    moment_offset = min(max(0.0, moment - start), duration)
    thumb = out_dir / "thumb.jpg"
    run_ffmpeg(["-ss", f"{moment_offset:.3f}", "-i", str(video), "-frames:v", "1", "-q:v", "3", str(thumb)])
    return CutResult(video, thumb, probe_duration(video), moment_offset)
