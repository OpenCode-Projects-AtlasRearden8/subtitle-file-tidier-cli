"""Formatting fixes you can apply to parsed cues.

Each function is pure: it takes a list of cues and returns (possibly the
same) list of cues, mutating cue objects in place for simplicity.
"""

from __future__ import annotations

import re
from statistics import median

from .formats import Cue, ms_to_ts

CUES_PER_SECOND_FALLBACK = 14.0  # chars of caption text we allow per second


def sort_cues(cues: list[Cue]) -> list[Cue]:
    untimed = [c for c in cues if c.start is None]
    timed = sorted(
        (c for c in cues if c.start is not None),
        key=lambda c: (c.start, c.end if c.end is not None else 0),
    )
    return timed + untimed


# --------------------------------------------------------------------------
# timing repairs
# --------------------------------------------------------------------------


def fix_overlaps(cues: list[Cue], gap_ms: int = 1) -> int:
    """Clamp cue ends so no cue starts before the previous one ends.

    Returns the number of cues whose end time was changed.
    """
    fixed = 0
    timed = [c for c in cues if c.start is not None and c.end is not None and c.end >= c.start]
    timed.sort(key=lambda c: (c.start, c.end))
    prev = None
    for cue in timed:
        if prev is None:
            prev = cue
            continue
        if cue.start < prev.end:
            new_end = max(cue.start - gap_ms, prev.start)
            if new_end < prev.end:
                prev.end = new_end
                fixed += 1
        prev = cue
    return fixed


def rescale_fps(cues: list[Cue], source_fps: float, target_fps: float) -> list[Cue]:
    """Shift all timestamps by the frame-rate ratio target/source.

    This is the standard "subtitles made for a 24fps edit of a video now
    playing at 25fps" repair: every time value is multiplied by
    target/source so cue N lines up with frame N again.
    """
    if source_fps <= 0 or target_fps <= 0:
        raise ValueError("frame rates must be positive")
    ratio = target_fps / source_fps
    for cue in cues:
        if cue.start is not None:
            cue.start = round(cue.start * ratio)
        if cue.end is not None:
            cue.end = round(cue.end * ratio)
    return cues


def snap_fps(cues: list[Cue], fps: float) -> int:
    """Snap every timestamp to the nearest frame boundary of ``fps``.

    Cleans up the small (1-2 frame) jitter that speech-recognition
    timestampers accumulate and that fractional frame rates produce.
    Returns the number of timestamps actually moved.
    """
    if fps <= 0:
        raise ValueError("fps must be positive")
    moved = 0
    for cue in cues:
        if cue.start is not None:
            snapped = round(round(cue.start * fps / 1000.0) * 1000.0 / fps)
            if snapped != cue.start:
                cue.start = snapped
                moved += 1
        if cue.end is not None:
            snapped = round(round(cue.end * fps / 1000.0) * 1000.0 / fps)
            if snapped != cue.end:
                cue.end = snapped
                moved += 1
    return moved


def detect_drift(cues: list[Cue]) -> float | None:
    """Rough drift detector: returns average gap deviation in ms, or None
    if there is not enough data. A big value means timing drifted from the
    frame grid (see snap_fps/rescale_fps).
    """
    timed = sorted(
        (c for c in cues if c.start is not None), key=lambda c: c.start
    )
    if len(timed) < 5:
        return None
    gaps = [
        timed[i].start - timed[i - 1].start
        for i in range(1, len(timed))
        if timed[i].start >= timed[i - 1].start
    ]
    gaps = [g for g in gaps if g > 0]
    if len(gaps) < 4:
        return None
    med = median(gaps)
    return sum(abs(g - med) for g in gaps) / len(gaps)


def ensure_timings(cues: list[Cue], chars_per_second: float = CUES_PER_SECOND_FALLBACK, gap_ms: int = 50) -> int:
    """Give untimed (plain-text) cues plausible sequential timings.

    Returns the number of cues timed.
    """
    timed_count = sum(1 for c in cues if c.start is not None)
    if timed_count and timed_count != len(cues):
        raise ValueError("cannot auto-time a file that mixes timed and untimed cues")
    if timed_count == len(cues) and timed_count > 0:
        return 0
    cursor = 0
    timed = 0
    for cue in cues:
        text = cue.text
        duration = max(700, round(len(text) * 1000.0 / max(chars_per_second, 0.1)))
        cue.start = cursor
        cue.end = cursor + duration
        cursor += duration + gap_ms
        timed += 1
    return timed


# --------------------------------------------------------------------------
# text repairs
# --------------------------------------------------------------------------

_SPEAKER_LINE = re.compile(
    r"^\s*(?:[-\u2013\u2014]\s*)?(?:>>\s*)?[A-Z][\w .,'`-]{0,39}?:\s*(.+)$"
)
_SPEAKER_ONLY = re.compile(r"^\s*(?:[-\u2013\u2014]\s*)?(?:>>\s*)?[A-Z][\w .,'`-]{0,39}?:\s*$")
_BRACKET_NOISE = re.compile(r"\s*(?:\[[^\[\]]{0,40}\]|\([^)]{0,30}\))")
_DASH_PREFIX = re.compile(r"(?m)^\s*[-\u2013\u2014]\s+")
_MUSIC_INLINE = re.compile(r"\s*[\u266a\u266b\u266c][^\u266a\u266b\u266c]{0,40}[\u266a\u266b\u266c]\s*")
_MUSIC_LINE = re.compile(r"^(?:[\s\u266a\u266b\u266c]|music|instrumental)+$", re.IGNORECASE)


def strip_speakers(cues: list[Cue]) -> int:
    """Remove speaker-name prefixes, ``>>`` markers and ``[notes]`` noise.

    Returns the number of cues touched.
    """
    touched = 0
    for cue in cues:
        original = list(cue.lines)
        cleaned: list[str] = []
        for line in cue.lines:
            m = _SPEAKER_LINE.match(line)
            if m:
                line = m.group(1).rstrip()
            elif _SPEAKER_ONLY.match(line):
                line = ""
            # ">> " camera-switch markers often double as speaker markers
            if line.strip().startswith(">>"):
                line = line.strip().lstrip(">").strip()
            # sequential ">>" markers inside a line
            if ">>" in line:
                line = re.sub(r">\s*>\s*", "", line)
            line = _BRACKET_NOISE.sub("", line)
            line = _MUSIC_INLINE.sub(" ", line)
            line = _DASH_PREFIX.sub("", line)
            line = line.strip()
            if line and _MUSIC_LINE.match(line):
                line = ""
            if line:
                cleaned.append(line)
        cue.lines = cleaned
        if cue.lines != original:
            touched += 1
    return touched


_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.!?;:])")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")
_MULTI_PUNCT = re.compile(r"([,.!?;]){4,}")
_AFTER_PUNCT = re.compile(r"([,.!?;:])([A-Za-z])")
_ELLIPSIS = re.compile(r"\.{2,}")
_SENTENCE_END = re.compile(r"[.!?;:]")
_QUOTES_NORMALIZE = {
    "\u201c": '"',
    "\u201d": '"',
    "\u2018": "'",
    "\u2019": "'",
    "\u00ab": '"',
    "\u00bb": '"',
}


def fix_punctuation(text: str, normalize_quotes: bool = True) -> tuple[str, bool]:
    """Tidy punctuation of one cue's text. Returns (new_text, changed)."""
    changed = text != text.strip()
    original = text
    text = text.strip()
    if normalize_quotes:
        for smart, plain in _QUOTES_NORMALIZE.items():
            text = text.replace(smart, plain)
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _MULTI_SPACE.sub(" ", text)
    text = _MULTI_PUNCT.sub(lambda m: m.group(1) * 3, text)
    text = _ELLIPSIS.sub("...", text)
    text = _AFTER_PUNCT.sub(lambda m: m.group(1) + " " + m.group(2), text)
    text = re.sub(r"[ \t]+", " ", text)
    text = text.strip()
    # Start-of-cue capitalisation, but only when the cue starts a sentence
    # visibly in caps/absence rather than mid-ellipsis.
    if text and text[0].isalpha() and text[0].islower() and not text.startswith("..."):
        text = text[0].upper() + text[1:]
        changed = True
    changed = changed or text != original
    return text, changed


def fix_punctuation_cues(cues: list[Cue]) -> int:
    """Apply fix_punctuation to every cue. Returns number of cues changed."""
    changed_count = 0
    for cue in cues:
        text = cue.text
        new_text, changed = fix_punctuation(text)
        if changed:
            cue.with_text(new_text)
            changed_count += 1
    return changed_count


def reflow(cues: list[Cue], max_chars: int = 42, max_lines: int = 2) -> int:
    """Re-wrap cue text: one logical string, greedily split into balanced lines.

    Returns the number of cues re-flowed.
    """
    touched = 0
    for cue in cues:
        text = " ".join(ln.strip() for ln in cue.lines if ln.strip())
        if not text:
            continue
        if len(text) <= max_chars:
            new_lines = [text]
        else:
            limit = min(max_chars, max(1, round(len(text) / max(max_lines, 1))))
            new_lines = _wrap(text, limit, max_lines=max_lines)
        if new_lines != cue.lines:
            cue.lines = new_lines
            touched += 1
    return touched


def _wrap(text: str, limit: int, max_lines: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= limit or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    # merge any overflow into the final lines
    while len(lines) > max_lines:
        tail = lines.pop()
        head = lines[-1] if lines else ""
        split = head.rsplit(" ", 1)
        if len(split) > 1 and len(split[-1]) + len(tail) + 1 <= limit + 12:
            lines[-1] = split[0]
            lines.append(f"{split[-1]} {tail}".strip())
        else:
            lines.append(tail)
            break
    return lines or [text]


def normalize(cues: list[Cue], **kwargs) -> list[Cue]:
    """One-stop filter: sort + fix spans + punctuation + reflow."""
    sort_cues(cues)
    fix_punctuation_cues(cues)
    reflow(cues, max_chars=kwargs.get("max_chars", 42), max_lines=kwargs.get("max_lines", 2))
    return cues


def describe_change(cue: Cue) -> str:
    """Short human-readable description of a cue, for reports/diffs."""
    start = ms_to_ts(cue.start) if cue.start is not None else "???"
    first = cue.lines[0] if cue.lines else ""
    return f"{start} {first[:60]}"
