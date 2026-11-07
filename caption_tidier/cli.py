"""Command-line interface for caption-tidier.

Batch-safe by design: every input file is read-only, and output goes to
a sidecar file (``video.srt`` -> ``video.tidy.srt``) that is never an
input path. An existing sidecar is skipped unless ``--force``.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys

from . import __version__
from . import llm as llm_mod
from .formats import detect_format, format_srt, format_text, format_vtt, parse_auto
from .tidy import (
    detect_drift,
    ensure_timings,
    fix_overlaps,
    fix_punctuation_cues,
    reflow,
    rescale_fps,
    snap_fps,
    sort_cues,
    strip_speakers,
)

FORMATS = ("srt", "vtt", "txt")


def sidecar_path(path: str, to_fmt: str, stem_suffix: str = "tidy") -> str:
    base, _ext = os.path.splitext(path)
    return f"{base}.{stem_suffix}.{to_fmt}"


def clean_file(
    path: str,
    to_fmt: str,
    *,
    fps: float | None,
    source_fps: float | None,
    target_fps: float | None,
    strip_names: bool,
    max_chars: int,
    max_lines: int,
    auto_time: bool,
    chars_per_second: float,
    dry_run: bool,
    force: bool,
    suffix: str,
    llm: llm_mod.LLMConfig | None,
) -> int:
    """Clean one file; returns 0 ok, 1 skipped / error (for batch counts)."""
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read()
    except OSError as exc:
        print(f"[error] cannot read {path}: {exc}", file=sys.stderr)
        return 1

    fmt = detect_format(text, path)
    cues = parse_auto(text, fmt)
    if not cues:
        print(f"[skip] {path}: no cues found")
        return 1

    report: list[str] = []

    was_untimed = all(cue.start is None for cue in cues)
    if was_untimed and (to_fmt in ("srt", "vtt")) and auto_time:
        timed = ensure_timings(cues, chars_per_second=chars_per_second)
        report.append(f"auto-timed {timed} cues ({chars_per_second:.0f} chars/sec)")
    elif any(cue.start is None for cue in cues) and not was_untimed:
        cues = [c for c in cues if c.start is not None]
        report.append("dropped untimed fragments")

    cues = sort_cues(cues)
    overlaps = fix_overlaps(cues)
    if overlaps:
        report.append(f"fixed {overlaps} timing overlap(s)")

    drift = detect_drift(cues)
    if drift is not None and drift > 400:
        report.append(f"wary: uneven cue spacing detected (~{drift:.0f} ms jitter)")

    if source_fps and target_fps:
        cues = rescale_fps(cues, source_fps, target_fps)
        report.append(f"rescaled timings {source_fps} fps -> {target_fps} fps")
    if fps:
        moved = snap_fps(cues, fps)
        report.append(f"snap {fps} fps: {moved} timestamp(s) moved")

    if strip_names:
        stripped = strip_speakers(cues)
        if stripped:
            report.append(f"stripped speaker/noise text in {stripped} cue(s)")

    punct = fix_punctuation_cues(cues)
    if punct:
        report.append(f"normalized punctuation in {punct} cue(s)")

    flowed = reflow(cues, max_chars=max_chars, max_lines=max_lines)
    if flowed:
        report.append(f"re-flowed {flowed} cue(s)")

    if llm is not None:
        suspect, changed, skipped = llm_mod.review_cues(cues, llm)
        report.append(f"LLM review: {suspect} suspect, {changed} fixed, {skipped} skipped")

    out_path = sidecar_path(path, to_fmt, suffix)
    if os.path.abspath(out_path) == os.path.abspath(path) and suffix in path:
        print(f"[skip] {path}: refusing to overwrite an existing tidy sidecar", file=sys.stderr)
        return 1
    if os.path.exists(out_path) and not force and not dry_run:
        print(f"[skip] {out_path} exists (use --force)", file=sys.stderr)
        return 1

    dumped = {"srt": format_srt, "vtt": format_vtt, "txt": format_text}[to_fmt](cues)
    if dry_run:
        print(f"[dry-run] would write {out_path}")
        for line in report:
            print(f"          - {line}")
        return 0
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(dumped)
    print(f"[ok] {path} ({fmt}) -> {out_path} ({to_fmt})")
    for line in report:
        print(f"     - {line}")
    return 0


def expand_globs(patterns: list[str]) -> list[str]:
    files: list[str] = []
    for pattern in patterns:
        expanded = glob.glob(pattern) or [pattern]
        for path in sorted(expanded):
            if os.path.isfile(path) and path not in files:
                files.append(path)
    return files


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="caption-tidier",
        description=(
            "Clean caption files (SRT / WebVTT / plain text): fix timing "
            "overlaps and frame-rate drift, strip speaker-name noise, "
            "normalize punctuation, re-flow split lines, and convert "
            "between formats. Writes sidecar files only; originals are "
            "never touched."
        ),
        epilog=(
            "Examples:\n"
            "  caption-tidier talk.srt\n"
            "  caption-tidier --to vtt *.srt\n"
            "  caption-tidier --from-fps 24 --to-fps 29.97 movie.srt\n"
            "  caption-tidier --llm notes.vtt\n"
        ),
    )
    parser.add_argument("inputs", nargs="+", metavar="FILE|GLOB", help="caption files to clean")
    parser.add_argument(
        "--to",
        dest="to_fmt",
        choices=FORMATS,
        default=None,
        help="output format (default: same as input)",
    )
    parser.add_argument("--suffix", default="tidy", help="sidecar suffix (default: tidy)")
    parser.add_argument("--force", action="store_true", help="overwrite existing sidecar files")
    parser.add_argument("--dry-run", action="store_true", help="report changes without writing")
    parser.add_argument("--fps", type=float, help="snap all timestamps to this frame rate")
    parser.add_argument(
        "--from-fps",
        dest="from_fps",
        type=float,
        help="source frame rate for a frame-rate conversion",
    )
    parser.add_argument(
        "--to-fps",
        dest="to_fps",
        type=float,
        help="target frame rate for a frame-rate conversion (needs --from-fps)",
    )
    parser.add_argument(
        "--keep-speakers", action="store_true", help="do not strip speaker names / noise markers"
    )
    parser.add_argument("--max-chars", dest="max_chars", type=int, default=42, help="re-flow line width")
    parser.add_argument("--max-lines", dest="max_lines", type=int, default=2, help="re-flow lines per cue")
    parser.add_argument(
        "--no-auto-time",
        dest="auto_time",
        action="store_false",
        help="do not invent timings when converting plain text to srt/vtt",
    )
    parser.add_argument(
        "--chars-per-second",
        dest="chars_per_second",
        type=float,
        default=14.0,
        help="reading speed used by auto-timing (default: 14)",
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help="review garbled ASR lines with a local OpenAI-compatible server (Ollama works, no keys)",
    )
    parser.add_argument(
        "--llm-endpoint",
        dest="llm_endpoint",
        default=llm_mod.DEFAULT_ENDPOINT,
        help=f"chat-completions base URL (default: {llm_mod.DEFAULT_ENDPOINT})",
    )
    parser.add_argument(
        "--llm-model",
        dest="llm_model",
        default=llm_mod.DEFAULT_MODEL,
        help=f"model name at the endpoint (default: {llm_mod.DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--llm-yes",
        dest="llm_yes",
        action="store_true",
        help="auto-approve every LLM suggestion (batch mode; review results!)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if (args.from_fps is None) != (args.to_fps is None):
        print("error: --from-fps and --to-fps are used together", file=sys.stderr)
        return 2
    if args.to_fps and args.fps:
        print("error: use --fps or --from-fps/--to-fps, not both", file=sys.stderr)
        return 2

    llm_config = (
        llm_mod.LLMConfig(
            endpoint=args.llm_endpoint,
            model=args.llm_model,
            yes=args.llm_yes,
        )
        if args.llm
        else None
    )

    files = expand_globs(args.inputs)
    if not files:
        print("error: no input files found", file=sys.stderr)
        return 2
    if args.llm and len(files) > 1 and not args.llm_yes:
        print(
            "note: interactive LLM review; you can pre-approve with --llm-yes",
            file=sys.stderr,
        )

    errors = 0
    for path in files:
        ext_fmt = os.path.splitext(path)[1][1:].lower()
        target = args.to_fmt or (ext_fmt if ext_fmt in FORMATS else None)
        if target is None:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
                head = fh.read(4096)
            target = detect_format(head, path)
        result = clean_file(
            path,
            target,
            fps=args.fps,
            source_fps=args.from_fps,
            target_fps=args.to_fps,
            strip_names=not args.keep_speakers,
            max_chars=args.max_chars,
            max_lines=args.max_lines,
            auto_time=args.auto_time,
            chars_per_second=args.chars_per_second,
            dry_run=args.dry_run,
            force=args.force,
            suffix=args.suffix,
            llm=llm_config,
        )
        if result:
            errors += result
    return 0 if errors == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
