"""Parsing and formatting for SRT, WebVTT and plain-text caption files.

Everything here is pure standard library and works on milliseconds as
integers so arithmetic (overlaps, frame-rate math) stays exact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Cue:
    """One caption cue. start/end are milliseconds since zero (None = untimed)."""

    start: int | None = None
    end: int | None = None
    lines: list[str] = field(default_factory=list)
    settings: str = ""  # extra VTT cue settings, e.g. "position:50%"

    @property
    def text(self) -> str:
        return " ".join(part for part in (line.strip() for line in self.lines) if part)

    def with_text(self, text: str) -> "Cue":
        self.lines = text.split("\n") if text else []
        return self


# --------------------------------------------------------------------------
# timestamp helpers
# --------------------------------------------------------------------------


def _ms(h: str | None, m: str, s: str, frac: str) -> int:
    parts = [h or "00", m, s, frac]
    hours, mins, secs, millis = (int(p) for p in parts)
    return hours * 3_600_000 + mins * 60_000 + secs * 1000 + millis


def ms_to_ts(ms: int, sep: str = ",") -> str:
    ms = max(0, int(ms))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms2 = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms2:03d}"


_ARROW_RE = re.compile(
    r"^(?:(\d{1,3}):)?(\d{1,2}):(\d{2})[,.](\d{1,3})"
    r"\s*-->\s*"
    r"(?:(\d{1,3}):)?(\d{1,2}):(\d{2})[,.](\d{1,3})"
    r"\s?(.*)$"
)


def parse_arrow(line: str) -> tuple[int, int, str] | None:
    """Parse "00:01:02,500 --> 00:01:04,000 [settings]" -> (start, end, settings)."""
    match = _ARROW_RE.match(line.strip())
    if not match:
        return None
    h1, m1, s1, f1, h2, m2, s2, f2, rest = match.groups()
    start = _ms(h1, m1, s1, f1)
    end = _ms(h2, m2, s2, f2)
    return start, end, rest.strip()


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------


def _parse_blocks(text: str, with_settings: bool = False) -> list[tuple[int, int, list[str], str]]:
    """Shared block parser: blank-line-separated blocks containing one arrow line."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", text)
    cues: list[tuple[int, int, list[str], str]] = []
    for block in blocks:
        lines = [ln.rstrip() for ln in block.split("\n")]
        arrow_idx = None
        for i, ln in enumerate(lines):
            if "-->" in ln:
                parsed = parse_arrow(ln)
                if parsed:
                    arrow_idx = i
                    break
        if arrow_idx is None:
            continue
        start, end, settings = parse_arrow(lines[arrow_idx])
        settings = settings if with_settings else ""
        body = [ln.strip() for ln in lines[arrow_idx + 1 :] if ln.strip()]
        cues.append((start, end, body, settings))
    return cues


def parse_srt(text: str) -> list[Cue]:
    cues = []
    for start, end, lines, _settings in _parse_blocks(text):
        cues.append(Cue(start, end, lines))
    return cues


def parse_vtt(text: str) -> list[Cue]:
    body = text.lstrip("\ufeff \t")
    if body.upper().startswith("WEBVTT"):
        body = body.split("\n", 1)[1] if "\n" in body else ""
    # drop NOTE / STYLE / REGION blocks (terminated by a blank line)
    body = re.sub(r"(?ms)^(?:NOTE|STYLE|REGION)\b.*?(?=^\s*$|\Z)", "", body)
    cues = []
    for start, end, lines, settings in _parse_blocks(body, with_settings=True):
        cues.append(Cue(start, end, lines, settings))
    return cues


def parse_text(text: str) -> list[Cue]:
    """Plain text: paragraphs separated by blank lines become untimed cues."""
    paragraphs = re.split(r"\n\s*\n", text.strip())
    cues = []
    for para in paragraphs:
        lines = [line.strip() for line in para.split("\n") if line.strip()]
        if lines:
            cues.append(Cue(None, None, lines))
    return cues


def parse_auto(text: str, fmt: str) -> list[Cue]:
    if fmt == "srt":
        return parse_srt(text)
    if fmt == "vtt":
        return parse_vtt(text)
    return parse_text(text)


# --------------------------------------------------------------------------
# formatting
# --------------------------------------------------------------------------


def format_srt(cues: list[Cue]) -> str:
    out = []
    for i, cue in enumerate(cues, 1):
        start = cue.start if cue.start is not None else 0
        end = max(cue.end if cue.end is not None else start, start)
        out.append(f"{i}\n{ms_to_ts(start)} --> {ms_to_ts(end)}")
        if cue.lines:
            out.append("\n".join(cue.lines))
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def format_vtt(cues: list[Cue]) -> str:
    out = ["WEBVTT", ""]
    for cue in cues:
        start = cue.start if cue.start is not None else 0
        end = max(cue.end if cue.end is not None else start, start)
        header = f"{ms_to_ts(start, '.')} --> {ms_to_ts(end, '.')}"
        if cue.settings:
            header += " " + cue.settings
        out.append(header)
        if cue.lines:
            out.append("\n".join(cue.lines))
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def format_text(cues: list[Cue]) -> str:
    paragraphs = [cue.text for cue in cues if cue.text]
    if not paragraphs:
        return ""
    return "\n\n".join(paragraphs) + "\n"


# --------------------------------------------------------------------------
# format detection
# --------------------------------------------------------------------------


def detect_format(text: str, filename: str = "") -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    ext = ext if ext in ("srt", "vtt", "txt") else ""
    body = text.lstrip()
    if body.startswith("WEBVTT") or body.startswith("\ufeffWEBVTT"):
        return "vtt"
    for line in body.splitlines():
        if "-->" in line and parse_arrow(line) is not None:
            if "," in line.split("-->")[0]:
                return "srt"
            return "vtt"
    if re.search(r"(?m)^WEBVTT", body):
        return "vtt"
    return ext or "txt"
