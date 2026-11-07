# status: caption-tidier

Built and verified 2026-11-07 · `caption_tidier` v0.1.0 · Python 3.13, stdlib only

## What was built

A zero-dependency Python CLI that cleans caption files **without ever
modifying the inputs**. Every output is a *sidecar* file (`talk.srt` →
`talk.tidy.srt`), so batching a folder full of transcriptions is safe to
repeat and experiment with.

Features implemented:

- **Parsing & conversion** — SRT, WebVTT, and plain text in; SRT, WebVTT,
  plain text out (`--to srt|vtt|txt`). Format auto-detected by content
  with extension fallback. `format detection`, `SRT/VTT round-trip`
  covered by tests.
- **Timing repairs**
  - Overlap clamping so no cue starts inside the previous one.
  - `--fps N` snap-to-frame-grid (removes timestamp jitter).
  - `--from-fps X --to-fps Y` frame-rate conversion (timestamps × Y/X).
  - Uneven cue spacing (drift symptom) is reported with the jitter figure.
  - Plain text → timed SRT/VTT auto-timing at `--chars-per-second`.
- **Text repairs**
  - Speaker-name strip: `NAME:`, `>>`, `[laughter]`, `(stage directions)`,
    `♪ music ♪`, stray leading dashes.
  - Punctuation normalization: no space before `, . ! ? ; :`, one space
    after, deduplicate `?!` pile-ups, `...` ellipses, smart quotes → ASCII,
    cue-initial capitalization.
  - Re-flow of split lines: re-wrap cues into ≤ `--max-lines` lines of
    ≤ `--max-chars` chars (defaults 42/2, the common caption widths).
- **Optional local LLM review** — `--llm` flags garbled speech-recognition
  lines (repeated letters, long consonant runs, junk tokens) and asks a
  local OpenAI-compatible endpoint for a repair. Ollama works out of the
  box; **no API keys, no cloud at any point**. Each suggested fix is shown
  as a before/after diff and applied only on approval (`y/n/q`, or
  `--llm-yes` to pre-approve for batch runs on trusted input).
- **Batch safety** — glob inputs (`*.srt`), existing sidecars skipped
  unless `--force`, `--dry-run` for a no-write report, refusal to ever
  write to an input path.

Package layout: `caption_tidier/formats.py` (parse/format),
`caption_tidier/tidy.py` (cleaning passes), `caption_tidier/llm.py`
(local review), `caption_tidier/cli.py` (argparse + batch logic).

## How to run it

```bash
pip install .                 # or use the .venv/ already present
caption-tidier talk.srt                       # -> talk.tidy.srt
caption-tidier --dry-run --to vtt *.srt
caption-tidier --from-fps 24 --to-fps 29.97 movie.srt
caption-tidier --llm podcast.vtt              # interactive, local-only
python -m caption_tidier --help               # works without install too
```

## Verification results

- `python -m pytest tests/ -q` — **23 passed**, including round-trip
  SRT/VTT parsing (parse → format → reparse is byte-identical and
  timestamp/text-identical), a full pass over the cleaning transforms,
  sidecar/glob naming, and mock-LLM review flows.
- `pyflakes` — clean. `py_compile` all modules — clean.
- End-to-end (real runs):
  - Messy SRT with overlaps, speaker noise, mid-line caps, `♪♪` lines →
    `awful.tidy.srt`: 2 overlaps fixed, 3 cues de-noised, punctuation in
    3 cues, 2 cues re-flowed. Original untouched, verified.
  - Batch `[skip]` on existing sidecar without `--force`; `--force`
    regenerates.
  - `--dry-run` writes nothing and prints the change report.
  - TXT → SRT auto-timed at 14 chars/sec; VTT → SRT and
    `--from-fps 24 --to-fps 25` rescale verified (2,000 ms becomes
    2,083 ms, as expected).
  - LLM review against a mock OpenAI-compatible server: suspect cues
    detected, diff shown, only approved lines patched; unreachable
    endpoint fails gracefully ("[!] LLM endpoint unreachable…", file
    still cleaned, exit 0).
  - Real Ollama was also reachable in this environment; one request
    round-tripped via curl. The bundled `llama3.2:1b` default is not
    installed here, and the available 4B model took >90 s per hot reply,
    so live-model output quality was not fully trialed (see gaps).

## How to run the tests

```bash
python -m pytest tests/ -q
```

## Known gaps

- **Drift repair is snap/conversion only.** There is no automatic
  "stretch already-drifted timestamps back to the frame grid" — you get a
  warning plus the two manual knobs (`--fps`, `--from/to-fps`).
- **VTT cue identifiers and comment text are not preserved** (they are
  intentionally dropped as noise; NOTE blocks are removed).
- **Speaker strip is heuristic.** Proper-noun-only colons like a
  sentence-cased `Okay:` could, in rare text, be mismatched; `--keep-speakers`
  disables the pass where in doubt.
- **No HTML/SSA tag preservation** (`<i>`, `<v Name>`, `{\an8}`): VTT cue
  *settings* survive, but inline markup is treated as body text. Common
  SRT formatting tags are not shifted or deleted, but they count toward
  the re-flow width.
- **Garble heuristics are deliberately simple** (regex-based). The local
  LLM pass is the intended deep fix; heuristics only *select* candidates.
- **Interactive review needs y/n/q per line**; for hands-off use rely on
  `--llm-yes` and review the sidecar, or run `--dry-run` first.
- **Local models can be slow**: timeout is generous (240 s) and failures
  are graceful, but very large cues on low-end machines may take a while.

All files contain no secrets, API keys, or personal data.
