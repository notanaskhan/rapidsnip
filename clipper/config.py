"""Load and validate config.yaml into dataclasses."""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class DetectorConfig:
    window_seconds: float = 10
    step_seconds: float = 2
    baseline_minutes: float = 5
    warmup_minutes: float = 3
    ratio_threshold: float = 3.0
    z_threshold: float = 4.0
    min_unique_chatters: int = 8
    cooldown_seconds: float = 60
    pause_after_event_seconds: float = 90
    extra_bots: list[str] = field(default_factory=list)
    reactions: list[str] = field(default_factory=lambda: [
        "LUL", "LULW", "OMEGALUL", "KEKW", "LMAO", "LMFAO", "💀", "😂", "WTF",
        "NO WAY", "HOLY", "CLIP", "CLIP IT", "Pog", "PogChamp", "monkaS", "W", "L",
        "LOL", "LOLW", "ICANT", "MegaLUL", "KEKL", "😭", "OMG", "D:", "monkaW",
    ])


@dataclass
class ClipConfig:
    pre_seconds: float = 20
    post_seconds: float = 10
    chat_delay_seconds: float = 6
    max_length_seconds: float = 45
    merge_gap_seconds: float = 15


@dataclass
class CaptionsConfig:
    enabled: bool = True
    model: str = "mlx-community/whisper-large-v3-turbo"
    words_per_caption: int = 3


@dataclass
class LayoutConfig:
    enabled: bool = True
    output: tuple[int, int] = (1080, 1920)


@dataclass
class ChannelLayout:
    facecam: tuple[int, int, int, int] | None = None
    gameplay: tuple[int, int, int, int] | None = None


PLATFORMS = ("twitch", "youtube", "kick")
LIVE_PLATFORMS = ("twitch",)        # platforms the live recorder supports today


@dataclass
class ChannelConfig:
    name: str
    display_name: str
    permission: str
    credit_url: str
    detector: DetectorConfig
    clip: ClipConfig
    layout: ChannelLayout
    platform: str = "twitch"
    enabled: bool = True

    @property
    def allowed(self) -> bool:
        return bool(self.permission and self.permission.strip())

    @property
    def recordable(self) -> bool:
        return self.allowed and self.enabled and self.platform in LIVE_PLATFORMS


@dataclass
class Config:
    output_dir: Path
    data_dir: Path
    poll_live_seconds: float
    buffer_minutes: float
    quality: str
    min_free_gb: float
    detector: DetectorConfig
    clip: ClipConfig
    captions: CaptionsConfig
    layout: LayoutConfig
    channels: list[ChannelConfig]


def _build(cls, data: dict | None):
    data = data or {}
    names = {f.name for f in dataclasses.fields(cls)}
    unknown = set(data) - names
    if unknown:
        raise ValueError(f"unknown {cls.__name__} keys: {sorted(unknown)}")
    return cls(**data)


def _overrides(cls, base, raw: dict):
    """Apply per-channel flat overrides (e.g. ratio_threshold) to a section."""
    names = {f.name for f in dataclasses.fields(cls)}
    picked = {k: v for k, v in raw.items() if k in names}
    return dataclasses.replace(base, **picked)


CHANNEL_KEYS = {"name", "display_name", "permission", "credit_url", "layout", "platform", "enabled"}
OVERRIDE_KEYS = {f.name for f in dataclasses.fields(DetectorConfig)} | {f.name for f in dataclasses.fields(ClipConfig)}


def channel_from_dict(ch: dict, detector: DetectorConfig, clip: ClipConfig) -> ChannelConfig:
    """One channel entry (from config.yaml or the database) merged over the global sections."""
    name = str(ch["name"]).lower().strip()
    unknown = set(ch) - CHANNEL_KEYS - OVERRIDE_KEYS
    if unknown:
        raise ValueError(f"channel {name}: unknown keys {sorted(unknown)}")
    platform = str(ch.get("platform") or "twitch").lower()
    if platform not in PLATFORMS:
        raise ValueError(f"channel {name}: platform must be one of {PLATFORMS}")
    lay = ch.get("layout") or {}
    default_url = {"twitch": f"https://twitch.tv/{name}", "youtube": f"https://www.youtube.com/@{name}",
                   "kick": f"https://kick.com/{name}"}[platform]
    return ChannelConfig(
        name=name,
        display_name=str(ch.get("display_name") or name),
        permission=str(ch.get("permission") or ""),
        credit_url=ch.get("credit_url") or default_url,
        detector=_overrides(DetectorConfig, detector, ch),
        clip=_overrides(ClipConfig, clip, ch),
        layout=ChannelLayout(
            facecam=tuple(lay["facecam"]) if lay.get("facecam") else None,
            gameplay=tuple(lay["gameplay"]) if lay.get("gameplay") else None,
        ),
        platform=platform,
        enabled=bool(ch.get("enabled", True)),
    )


def load_config(path: str | Path = "config.yaml") -> Config:
    path = Path(path)
    raw = yaml.safe_load(path.read_text()) or {}
    root = path.parent

    detector = _build(DetectorConfig, raw.get("detector"))
    clip = _build(ClipConfig, raw.get("clip"))
    captions = _build(CaptionsConfig, raw.get("captions"))
    layout_raw = dict(raw.get("layout") or {})
    if "output" in layout_raw:
        layout_raw["output"] = tuple(layout_raw["output"])
    layout = _build(LayoutConfig, layout_raw)

    channels = []
    seen = set()
    for ch in raw.get("channels") or []:
        c = channel_from_dict(ch, detector, clip)
        if c.name in seen:
            raise ValueError(f"duplicate channel {c.name}")
        seen.add(c.name)
        channels.append(c)

    return Config(
        output_dir=(root / raw.get("output_dir", "./clips")).resolve(),
        data_dir=(root / raw.get("data_dir", "./data")).resolve(),
        poll_live_seconds=float(raw.get("poll_live_seconds", 60)),
        buffer_minutes=float(raw.get("buffer_minutes", 10)),
        quality=str(raw.get("quality", "best")),
        min_free_gb=float(raw.get("min_free_gb", 20)),
        detector=detector,
        clip=clip,
        captions=captions,
        layout=layout,
        channels=channels,
    )
