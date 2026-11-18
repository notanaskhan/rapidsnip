"""M6 check: caption a clip and save frames to eyeball.

    uv run python tools/test_captions.py <video.mp4> [--out DIR]

Prints the transcript and caption timings, writes horizontal.mp4 with burned-in
captions (or a plain copy if nothing survived the hallucination filter), and saves
a frame from the middle of each of the first few captions.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clipper import captions  # noqa: E402
from clipper.config import load_config  # noqa: E402
from clipper.cutter import probe_size, run_ffmpeg  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("video", type=Path)
    p.add_argument("--out", type=Path)
    p.add_argument("--frames", type=int, default=3)
    a = p.parse_args()
    cfg = load_config().captions
    out = a.out or a.video.parent / "captions_test"
    out.mkdir(parents=True, exist_ok=True)

    t = time.time()
    captions.load_model(cfg.model)
    print(f"model load {time.time() - t:.1f}s")
    t = time.time()
    wav = captions.extract_wav(a.video, out / "audio.wav")
    words, transcript = captions.transcribe(wav, cfg.model)
    caps = captions.group(words, cfg.words_per_caption)
    print(f"transcribe {time.time() - t:.1f}s; {len(words)} words kept, {len(caps)} captions")
    print(f"transcript: {transcript!r}")
    for c in caps[:12]:
        print(f"  {c.start:6.2f}-{c.end:6.2f}  {c.text}")

    horizontal = out / "horizontal.mp4"
    t = time.time()
    if caps:
        w, h = probe_size(a.video)
        ass = out / "captions_h.ass"
        ass.write_text(captions.build_ass(caps, w, h, font_size=round(h * 0.065), margin_v=round(h * 0.08)))
        run_ffmpeg(["-i", str(a.video), "-vf", captions.subtitles_filter(ass), "-c:v", "h264_videotoolbox",
                    "-b:v", "8M", "-c:a", "copy", str(horizontal)])
        for i, c in enumerate(caps[:a.frames]):
            mid = (c.start + c.end) / 2
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(horizontal), "-ss", f"{mid:.2f}",
                            "-frames:v", "1", "-vf", "scale=960:-2", str(out / f"frame_{i}.jpg")], check=True)
    else:
        run_ffmpeg(["-i", str(a.video), "-c", "copy", str(horizontal)])
        print("no captions: rendered a plain copy")
    print(f"render {time.time() - t:.1f}s -> {horizontal}")


if __name__ == "__main__":
    main()
