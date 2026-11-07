"""Round-trip parsing tests for SRT and WebVTT, plus the cleaning passes."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from caption_tidier import formats
from caption_tidier.cli import expand_globs, sidecar_path
from caption_tidier.llm import is_suspect
import caption_tidier.llm as llm_mod
from caption_tidier.tidy import (
    detect_drift,
    ensure_timings,
    fix_overlaps,
    fix_punctuation,
    fix_punctuation_cues,
    reflow,
    rescale_fps,
    snap_fps,
    sort_cues,
    strip_speakers,
)

SRT_SAMPLE = """1
00:00:01,000 --> 00:00:03,250
Hello island,
welcome aboard.

2
00:00:03,500 --> 00:00:06,000
Second cue is here
and fairly long.

3
00:00:23,976 --> 00:00:27,000
Snappy.
"""

VTT_SAMPLE = """WEBVTT

00:01:02.500 --> 00:01:05.000 position:50% align:center
Hello island,
welcome aboard.

00:01:05.250 --> 00:01:08.000
Second cue.

NOTE this is dropped

00:01:08.500 --> 00:01:09.000
>Third cue <with junk>
"""


def _cues_text(cues):
    return [c.text for c in cues]


# ---------------------------------------------------------------------------
# SRT round-trip
# ---------------------------------------------------------------------------


def test_srt_roundtrip_identical():
    parsed = formats.parse_srt(SRT_SAMPLE)
    assert len(parsed) == 3
    out = formats.format_srt(parsed)
    reparsed = formats.parse_srt(out)
    assert [(c.start, c.end) for c in reparsed] == [(c.start, c.end) for c in parsed]
    assert _cues_text(reparsed) == _cues_text(parsed)
    assert formats.format_srt(reparsed) == out


def test_srt_roundtrip_timestamps():
    parsed = formats.parse_srt(SRT_SAMPLE)
    assert parsed[0].start == 1_000
    assert parsed[0].end == 3_250
    assert parsed[2].start == 23_976
    assert parsed[0].lines == ["Hello island,", "welcome aboard."]


def test_srt_multiline_and_blank_lines_preserved():
    text = (
        "1\n00:00:01,000 --> 00:00:02,000\nline one\nline two\n\n"
        "2\n00:00:02,000 --> 00:00:03,000\nnext\n"
    )
    out = formats.format_srt(formats.parse_srt(text))
    assert out == text


# ---------------------------------------------------------------------------
# VTT round-trip
# ---------------------------------------------------------------------------


def test_vtt_roundtrip_identical():
    parsed = formats.parse_vtt(VTT_SAMPLE)
    assert len(parsed) == 3
    out = formats.format_vtt(parsed)
    reparsed = formats.parse_vtt(out)
    assert [(c.start, c.end) for c in reparsed] == [(c.start, c.end) for c in parsed]
    assert _cues_text(reparsed) == _cues_text(parsed)
    assert formats.format_vtt(reparsed) == out


def test_vtt_parsing_details():
    parsed = formats.parse_vtt(VTT_SAMPLE)
    assert parsed[0].start == 62_500
    assert parsed[0].settings == "position:50% align:center"
    assert "<with junk>" in parsed[2].text  # raw line preserved; the tidier strips bracket noise
    # NOTE block was dropped, counts stay at 3 cues
    assert formats.format_vtt(parsed).startswith("WEBVTT")


def test_vtt_short_timestamps():
    parsed = formats.parse_vtt("WEBVTT\n\n02:04.500 --> 02:06.000\nshort time\n")
    assert parsed[0].start == (2 * 60 + 4) * 1000 + 500


# ---------------------------------------------------------------------------
# format detection and conversion
# ---------------------------------------------------------------------------


def test_detect_format():
    assert formats.detect_format(SRT_SAMPLE, "x.srt") == "srt"
    assert formats.detect_format(SRT_SAMPLE, "") == "srt"
    assert formats.detect_format(VTT_SAMPLE, "") == "vtt"
    assert formats.detect_format("just some text", "notes.txt") == "txt"


def test_srt_to_vtt_to_srt_roundtrip():
    cues = formats.parse_srt(SRT_SAMPLE)
    vtt_text = formats.format_vtt(cues)
    back = formats.format_srt(formats.parse_vtt(vtt_text))
    assert [(c.start, c.end) for c in formats.parse_srt(back)] == [
        (c.start, c.end) for c in cues
    ]
    for c in formats.parse_srt(back):
        assert "," in formats.ms_to_ts(c.start)


# ---------------------------------------------------------------------------
# plain text
# ---------------------------------------------------------------------------


def test_plain_text_parse_and_format():
    text = "First paragraph here.\n\n\nSecond one.\n"
    cues = formats.parse_text(text)
    assert len(cues) == 2
    assert cues[0].start is None
    assert formats.format_text(cues) == "First paragraph here.\n\nSecond one.\n"


def test_auto_timings_monotonic():
    cues = formats.parse_text("Short.\n\nA considerably longer paragraph of speech.\n\nHi.\n")
    timed = ensure_timings(cues)
    assert timed == 3
    starts = [c.start for c in cues]
    assert starts == sorted(starts)
    assert all(c.end > c.start for c in cues)


# ---------------------------------------------------------------------------
# cleaning passes
# ---------------------------------------------------------------------------


def test_fix_overlaps():
    cues = [
        formats.Cue(0, 5_000, ["one"]),
        formats.Cue(3_000, 7_000, ["two"]),  # overlaps -> end one at 2999
        formats.Cue(8_000, 9_000, ["three"]),
    ]
    changed = fix_overlaps(cues)
    assert changed == 1
    assert cues[0].end == 2_999
    by_start = sorted(cues, key=lambda c: c.start)
    assert all(a.end <= b.start for a, b in zip(by_start, by_start[1:]))


def test_fix_overlaps_nested_and_start_clamp():
    cues = [
        formats.Cue(1_000, 10_000, ["long"]),
        formats.Cue(2_000, 3_000, ["nested"]),
    ]
    fix_overlaps(cues)
    assert cues[0].end == 1_999
    assert cues[1].end == 3_000


def test_rescale_fps():
    cues = [formats.Cue(0, 4_000, ["a"]), formats.Cue(10_000, 12_000, ["b"])]
    rescale_fps(cues, 24, 30)
    assert cues[1].start == round(10_000 * 30 / 24)
    assert cues[0].end == 5_000


def test_snap_fps():
    cues = [formats.Cue(0, 100, ["a"]), formats.Cue(1_023, 3_993, ["b"])]
    moved = snap_fps(cues, 25)  # 40ms frames
    assert moved > 0
    for cue in cues:
        for ts in (cue.start, cue.end):
            assert ts % 40 == 0


def test_detect_drift_needs_data():
    assert detect_drift([formats.Cue(0, 1_000, ["a"])]) is None
    even = [formats.Cue(i * 2_000, i * 2_000 + 1_000, ["x"]) for i in range(10)]
    drift = detect_drift(even)
    assert drift is not None and drift == 0


def test_strip_speakers():
    cues = [
        formats.Cue(0, 1_000, ["MARY-JANE: Hi there!"]),
        formats.Cue(1_000, 2_000, ["[laughter]"]),
        formats.Cue(2_000, 3_000, [">> John: Welcome to the show."]),
        formats.Cue(3_000, 4_000, ["NARRATOR:"]),
    ]
    strip_speakers(cues)
    assert cues[0].lines == ["Hi there!"]
    assert cues[1].lines == []
    assert cues[2].lines == ["Welcome to the show."]
    assert cues[3].lines == []


def test_punctuation_and_reflow():
    text, changed = fix_punctuation("hi there  ...come in ?ok")
    assert changed
    assert " ?" not in text
    assert text[0].isupper()
    assert "..." in text

    cues = [formats.Cue(0, 2_000, ["This line is", "split across the", "screen oddly."])]
    assert fix_punctuation_cues(cues) >= 0
    reflow(cues, max_chars=20, max_lines=2)
    assert len(cues[0].lines) <= 3
    assert all(len(ln) <= 30 for ln in cues[0].lines)
    assert cues[0].text == "This line is split across the screen oddly."


def test_sort_cues():
    cues = [formats.Cue(5_000, 6_000), formats.Cue(1_000, 2_000), formats.Cue(None, None, ["t"])]
    sorted_cues = sort_cues(cues)
    assert [c.start for c in sorted_cues[:2]] == [1_000, 5_000]


def test_sidecar_naming_and_globs(tmp_path):
    assert sidecar_path("/tmp/movie.srt", "vtt") == "/tmp/movie.tidy.vtt"
    (tmp_path / "a.srt").write_text(SRT_SAMPLE, encoding="utf-8")
    (tmp_path / "b.srt").write_text(SRT_SAMPLE, encoding="utf-8")
    found = expand_globs([str(tmp_path / "*.srt")])
    assert len(found) == 2 and all(p.endswith(".srt") for p in found)


def test_llm_suspect_heuristic():
    assert is_suspect("I appreaate thaaat a looot, yeeeessss")
    assert is_suspect("your obvxcfgs were taped")
    assert not is_suspect("Hello, and welcome back to the program.")
    assert not is_suspect("Strength, honor, discipline.")


class _InputRecorder:
    """Pretend stdin for interactive review tests."""

    def __init__(self, replies):
        self.replies = list(replies)

    def __call__(self, _prompt=""):
        if not self.replies:
            return "n"
        return self.replies.pop(0)


def test_llm_review_applies_approved_fixes(monkeypatch):
    cues = [
        formats.Cue(0, 1_000, ["Thaaat sounds greaaat"]),
        formats.Cue(1_000, 2_000, ["Perfectly fine line"]),
    ]
    monkeypatch.setattr(llm_mod, "request_fix", lambda cfg, text: text.replace("Thaaat", "That").replace("greaaat", "great"))
    config = llm_mod.LLMConfig()
    suspect, changed, skipped = llm_mod.review_cues(
        cues, config, out=lambda *a, **k: None, input_fn=_InputRecorder(["y"])
    )
    assert (suspect, changed) == (1, 1)
    assert cues[0].text == "That sounds great"
    assert cues[1].text == "Perfectly fine line"


def test_llm_review_rejected_fix_is_kept_out(monkeypatch):
    cues = [formats.Cue(0, 1_000, ["Thaaat is off"])]
    monkeypatch.setattr(llm_mod, "request_fix", lambda cfg, text: "completely different")
    suspect, changed, skipped = llm_mod.review_cues(
        cues, llm_mod.LLMConfig(), out=lambda *a, **k: None, input_fn=_InputRecorder(["n"])
    )
    assert (suspect, changed, skipped) == (1, 0, 1)
    assert cues[0].text == "Thaaat is off"


def test_llm_review_handles_unreachable_endpoint(monkeypatch):
    cues = [formats.Cue(0, 1_000, ["Thaaat is off"])]
    monkeypatch.setattr(llm_mod, "request_fix", lambda cfg, text: None)
    messages = []
    suspect, changed, skipped = llm_mod.review_cues(
        cues, llm_mod.LLMConfig(), out=lambda *a: messages.append(a), input_fn=_InputRecorder([])
    )
    assert (changed, skipped) == (0, 1)
    assert any("unreachable" in str(m[0]) for m in messages)
