"""caption-tidier: clean up caption files without touching the originals."""

from .formats import Cue, format_srt, format_text, format_vtt, parse_auto, parse_srt, parse_text, parse_vtt
from .tidy import (
    ensure_timings,
    fix_overlaps,
    fix_punctuation,
    normalize,
    reflow,
    rescale_fps,
    snap_fps,
    sort_cues,
    strip_speakers,
)

__version__ = "0.1.0"

__all__ = [
    "Cue",
    "format_srt",
    "format_text",
    "format_vtt",
    "parse_auto",
    "parse_srt",
    "parse_text",
    "parse_vtt",
    "ensure_timings",
    "fix_overlaps",
    "fix_punctuation",
    "normalize",
    "reflow",
    "rescale_fps",
    "snap_fps",
    "sort_cues",
    "strip_speakers",
    "__version__",
]
