"""9:16 vertical layout (facecam over gameplay) and 16:9 horizontal, rendered in one FFmpeg pass.

With a facecam: facecam crop fills the top 1080x672, gameplay crop fills the
bottom 1080x1248 (scale to cover, then centre-crop, so nothing stretches);
captions sit just below the band boundary.
Without a facecam: gameplay scaled to 1080 wide in the middle, over a blurred,
enlarged copy of itself; captions sit in the blurred band under the video.

Crop boxes in config.yaml are in 1920x1080 source pixels and are scaled if the
stream comes in at another resolution.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import captions as cap
from .captions import Caption
from .cutter import Source, run_ffmpeg, source_from_file

REF_WIDTH = 1920
TOP_FRACTION = 0.35          # facecam band = 672 of 1920
VERTICAL_BITRATE = "10M"
HORIZONTAL_BITRATE = "8M"


@dataclass
class Box:
    x: int
    y: int
    w: int
    h: int

    def scaled(self, k: float, src_w: int, src_h: int) -> "Box":
        x, y = round(self.x * k), round(self.y * k)
        w, h = round(self.w * k), round(self.h * k)
        x, y = max(0, min(x, src_w - 2)), max(0, min(y, src_h - 2))
        w, h = min(w, src_w - x), min(h, src_h - y)
        return Box(x, y, w - w % 2, h - h % 2)

    def crop(self) -> str:
        return f"crop={self.w}:{self.h}:{self.x}:{self.y}"


def _cover(w: int, h: int) -> str:
    return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"


@dataclass
class VerticalPlan:
    filter: str                # filter graph from [src] to [v] (without captions)
    caption_y: int
    font_size: int


def plan_vertical(src_w: int, src_h: int, facecam, gameplay, out_w: int, out_h: int) -> VerticalPlan:
    k = src_w / REF_WIDTH
    game = Box(*gameplay).scaled(k, src_w, src_h) if gameplay else Box(0, 0, src_w, src_h)
    font = round(out_w * 0.074)          # ~80 at 1080 wide
    if facecam:
        face = Box(*facecam).scaled(k, src_w, src_h)
        top_h = round(out_h * TOP_FRACTION / 2) * 2
        bot_h = out_h - top_h
        f = (f"[src]split=2[fsrc][gsrc];"
             f"[fsrc]{face.crop()},{_cover(out_w, top_h)}[top];"
             f"[gsrc]{game.crop()},{_cover(out_w, bot_h)}[bot];"
             f"[top][bot]vstack=inputs=2,setsar=1[v]")
        return VerticalPlan(f, caption_y=top_h + round(font * 1.1), font_size=font)
    fg_h = round(out_w * game.h / game.w / 2) * 2
    fg_y = (out_h - fg_h) // 2
    f = (f"[src]{game.crop()},split=2[bgsrc][fgsrc];"
         f"[bgsrc]{_cover(out_w // 4, out_h // 4)},boxblur=10:2,scale={out_w}:{out_h},eq=brightness=-0.12[bg];"
         f"[fgsrc]scale={out_w}:{fg_h}[fg];"
         f"[bg][fg]overlay=0:{fg_y},setsar=1[v]")
    return VerticalPlan(f, caption_y=fg_y + fg_h + round(font * 1.4), font_size=font)


def render(src: Source | Path, out_dir: Path, caps: list[Caption], facecam, gameplay,
           output: tuple[int, int] = (1080, 1920), vertical: bool = True) -> dict[str, Path]:
    """Write vertical.mp4 (if enabled) and horizontal.mp4, both with captions if any, in one pass."""
    if isinstance(src, Path):
        src = source_from_file(src)
    src_w, src_h = src.width, src.height
    out_w, out_h = output
    outputs: dict[str, Path] = {}
    graph = []
    splits = ["[h0]"] + (["[v0]"] if vertical else [])
    graph.append(f"[{src.vin}]{src.vtrim}split={len(splits)}{''.join(splits)}")
    if src.has_audio:
        graph.append(f"[{src.ain}]{src.atrim}asplit={len(splits)}" + "".join(f"[a{i}]" for i in range(len(splits))))

    # Horizontal: same frame, captions bottom-centre.
    if caps:
        ass_h = out_dir / "captions_h.ass"
        ass_h.write_text(cap.build_ass(caps, src_w, src_h, font_size=round(src_h * 0.065),
                                       margin_v=round(src_h * 0.08)))
        graph.append(f"[h0]{cap.subtitles_filter(ass_h)}[h]")
    else:
        graph.append("[h0]null[h]")

    if vertical:
        plan = plan_vertical(src_w, src_h, facecam, gameplay, out_w, out_h)
        graph.append(plan.filter.replace("[src]", "[v0]", 1).replace("[v]", "[vraw]"))
        if caps:
            ass_v = out_dir / "captions_v.ass"
            ass_v.write_text(cap.build_ass(caps, out_w, out_h, font_size=plan.font_size, y=plan.caption_y))
            graph.append(f"[vraw]{cap.subtitles_filter(ass_v)}[v]")
        else:
            graph.append("[vraw]null[v]")

    args = [*src.input_args, "-filter_complex", ";".join(graph)]
    audio = ["-c:a", "aac", "-b:a", "160k"]
    horizontal = out_dir / "horizontal.mp4"
    args += ["-map", "[h]", *(["-map", "[a0]"] if src.has_audio else []), "-c:v", "h264_videotoolbox",
             "-b:v", HORIZONTAL_BITRATE, *audio, "-movflags", "+faststart", str(horizontal)]
    outputs["horizontal"] = horizontal
    if vertical:
        vpath = out_dir / "vertical.mp4"
        args += ["-map", "[v]", *(["-map", "[a1]"] if src.has_audio else []), "-c:v", "h264_videotoolbox",
                 "-b:v", VERTICAL_BITRATE, *audio, "-movflags", "+faststart", str(vpath)]
        outputs["vertical"] = vpath
    run_ffmpeg(args)
    return outputs
