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
    ).stdout
    # mpegts files list the stream once per program as well; take the first "w,h" line.
    line = next(ln for ln in out.splitlines() if "," in ln)
    w, h = line.split(",")[:2]
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


# ---- direct render source (no intermediate encode) ----------------------

@dataclass
class Source:
    """An FFmpeg input plus the filters that trim it to the clip window (frame-exact).

    Trimming happens inside the filter graph, after resetting timestamps to zero, so
    downstream filters (captions) see the clip's own timeline starting at 0.
    """
    input_args: list[str]
    width: int
    height: int
    has_audio: bool
    duration: float
    vtrim: str = ""          # filter prefix for [0:v], ends with "," when set
    atrim: str = ""          # filter prefix for [0:a], ends with "," when set


def has_audio(path: Path) -> bool:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip()
    return bool(out)


def source_from_file(path: Path) -> Source:
    w, h = probe_size(path)
    return Source(["-i", str(path)], w, h, has_audio(path), probe_duration(path))


def source_from_segments(segments: list[Segment], start: float, end: float, out_dir: Path) -> Source:
    """Window [start, end] (wall clock) of a contiguous run of segments, without re-encoding first."""
    if not segments:
        raise ValueError("no segments")
    out_dir.mkdir(parents=True, exist_ok=True)
    concat = out_dir / "concat.txt"
    concat.write_text("".join(f"file '{s.path.resolve()}'\n" for s in segments))
    offset = max(0.0, start - segments[0].wall_start)
    duration = end - start
    w, h = probe_size(segments[0].path)
    trim = f"trim=start={offset:.3f}:duration={duration:.3f}"
    atrim = f"atrim=start={offset:.3f}:duration={duration:.3f}"
    return Source(
        ["-f", "concat", "-safe", "0", "-i", str(concat)], w, h, has_audio(segments[0].path), duration,
        vtrim=f"setpts=PTS-STARTPTS,{trim},setpts=PTS-STARTPTS,",
        atrim=f"asetpts=PTS-STARTPTS,{atrim},asetpts=PTS-STARTPTS,",
    )


def extract_wav(src: Source, wav: Path) -> Path | None:
    """16 kHz mono WAV of the clip window, for Whisper. None if there is no audio."""
    if not src.has_audio:
        return None
    run_ffmpeg([*src.input_args, "-vn", "-af", f"{src.atrim}aresample=16000", "-ac", "1",
                "-c:a", "pcm_s16le", str(wav)])
    return wav


def thumbnail(video: Path, at: float, out: Path) -> Path:
    run_ffmpeg(["-i", str(video), "-ss", f"{max(0.0, at):.3f}", "-frames:v", "1", "-q:v", "3", str(out)])
    return out
