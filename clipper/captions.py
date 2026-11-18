"""MLX Whisper -> word-timed .ass captions (bold, white, thick black outline, uppercase).

Whisper invents text on silence, music and screaming ("Thank you.", "Thanks for
watching!"), so words are filtered by confidence and segments by no-speech
probability. If too little survives, the clip renders without captions.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

FONT_DIR = "/System/Library/Fonts/Supplemental"
FONT_NAME = "Arial Black"

MIN_WORD_PROB = 0.35
MAX_NO_SPEECH = 0.6
MIN_AVG_LOGPROB = -1.0
MIN_WORDS = 2                  # fewer surviving words than this = no captions
MAX_GAP = 0.6                  # a pause this long starts a new caption
HOLD = 0.25                    # keep a caption up this long after its last word, if nothing follows
HALLUCINATIONS = {
    "thank you", "thank you.", "thanks for watching", "thanks for watching!",
    "thank you for watching", "please subscribe", "subscribe", "you", "bye", "bye.",
    "so", "okay", "oh", "uh", "um",
}


@dataclass
class Word:
    text: str
    start: float
    end: float
    prob: float


@dataclass
class Caption:
    text: str
    start: float
    end: float


def extract_wav(video: Path, wav: Path) -> Path:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(video),
                    "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)], check=True)
    return wav


def load_model(model: str) -> None:
    """Load the model once (mlx_whisper caches it per process) by transcribing 1 s of silence."""
    import numpy as np
    import mlx_whisper
    mlx_whisper.transcribe(np.zeros(16000, dtype=np.float32), path_or_hf_repo=model, verbose=None)


def transcribe(wav: Path, model: str) -> tuple[list[Word], str]:
    """Returns (kept words, kept transcript text)."""
    import mlx_whisper
    result = mlx_whisper.transcribe(
        str(wav), path_or_hf_repo=model, word_timestamps=True, verbose=None,
        condition_on_previous_text=False, hallucination_silence_threshold=1.0,
    )
    words: list[Word] = []
    for seg in result.get("segments", []):
        if seg.get("no_speech_prob", 0) > MAX_NO_SPEECH and seg.get("avg_logprob", 0) < MIN_AVG_LOGPROB:
            continue
        if seg.get("text", "").strip().lower() in HALLUCINATIONS:
            continue
        for w in seg.get("words", []):
            text = w["word"].strip()
            if text and w.get("probability", 1) >= MIN_WORD_PROB and w["end"] > w["start"]:
                words.append(Word(text, float(w["start"]), float(w["end"]), float(w["probability"])))
    transcript = " ".join(w.text for w in words).strip()
    if len(words) < MIN_WORDS or transcript.lower() in HALLUCINATIONS:
        return [], ""
    return words, transcript


def group(words: list[Word], per_caption: int) -> list[Caption]:
    caps: list[Caption] = []
    chunk: list[Word] = []

    def flush():
        if chunk:
            text = " ".join(w.text for w in chunk)
            text = re.sub(r"[^\w'’!?%$#@&+\- ]", "", text, flags=re.UNICODE).upper().strip()
            if text:
                caps.append(Caption(text, chunk[0].start, chunk[-1].end))
            chunk.clear()

    for w in words:
        if chunk and (len(chunk) >= per_caption or w.start - chunk[-1].end > MAX_GAP):
            flush()
        chunk.append(w)
    flush()
    # Hold each caption until the next one starts (or briefly after its last word).
    for a, b in zip(caps, caps[1:]):
        a.end = b.start if b.start - a.end < MAX_GAP else a.end + HOLD
    if caps:
        caps[-1].end += HOLD
    return caps


def _ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def build_ass(caps: list[Caption], width: int, height: int, font_size: int,
              y: int | None = None, margin_v: int = 120) -> str:
    """ASS text. With y, captions are centred on that line; otherwise bottom-centred."""
    outline = max(5, font_size // 9)
    align = 5 if y is not None else 2
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{FONT_NAME},{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{outline},2,{align},60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    pos = f"{{\\pos({width // 2},{y})}}" if y is not None else ""
    lines = []
    for c in caps:
        text = c.text.replace("\\", "").replace("{", "").replace("}", "")
        lines.append(f"Dialogue: 0,{_ts(c.start)},{_ts(c.end)},Cap,,0,0,0,,{pos}{text}")
    return head + "\n".join(lines) + "\n"


def subtitles_filter(ass: Path) -> str:
    """FFmpeg filter that burns an .ass file, with the font directory pinned."""
    def esc(p: str) -> str:
        return p.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return f"subtitles=filename='{esc(str(ass))}':fontsdir='{esc(FONT_DIR)}'"
