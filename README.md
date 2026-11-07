# caption-tidier

**Clean up caption files without ever touching the originals.**

`caption-tidier` reads SRT, WebVTT, and plain-text caption files and writes a
cleaned copy as a *sidecar* file: `talk.srt` becomes `talk.tidy.srt`. Your
original is never modified, so you can experiment freely — batch a whole
folder of downloads at once and always have the source to fall back on.

What it fixes:

- **Timing overlaps** — cues that start before the previous cue ends are
  clamped so the track plays cleanly.
- **Frame-rate drift** — snap timestamps to a frame grid (`--fps 25`) or
  rescale a track that was made for a wrong frame rate
  (`--from-fps 24 --to-fps 29.97`). Uneven spacing is flagged for you.
- **Speaker-name noise** — `MARY-JANE:`, `>>`, `[laughter]`,
  `(elephant trumpets)` and similar transcription clutter are stripped.
- **Punctuation** — stray spaces before punctuation, missing spaces after
  it, `!!!?!` pile-ups, `...` ellipses, smart quotes, and cue-initial
  capitalization all get normalized.
- **Split lines re-flowed** — captions that were broken across the screen
  at odd places are re-wrapped to a tidy width (default 42 chars, 2 lines).
- **Format conversion** — anything in, any format out: `--to srt|vtt|txt`.
- **Optional local LLM review** — garbled speech-recognition words
  (`"thaaat a looot"`, `"obvxcfgs"`) are flagged and sent to a local
  OpenAI-compatible endpoint (Ollama included, no API keys, no cloud). You
  approve each fix line by line before it's applied.

It is pure Python (3.10+, standard library only), runs entirely offline,
and is batch-safe: feed it globs like `*.srt` and it cleans every file,
skipping any whose sidecar already exists (`--force` to override).

## Install

```bash
pip install .            # from this folder; creates the `caption-tidier` command
# or just run it in place:
python -m caption_tidier --help
```

## Usage

```bash
caption-tidier talk.srt                        # -> talk.tidy.srt
caption-tidier --to vtt talk.srt               # convert SRT -> talk.tidy.vtt
caption-tidier "*.vtt"                         # batch a whole folder
caption-tidier --dry-run talk.srt              # see the report, write nothing
caption-tidier --fps 25 movie.srt              # snap timestamps to 25 fps
caption-tidier --from-fps 24 --to-fps 29.97 m.srt   # frame-rate conversion
caption-tidier --to srt notes.txt              # plain text -> timed SRT
caption-tidier --keep-speakers show.srt        # keep "NAME:" prefixes
caption-tidier --max-chars 38 --max-lines 2 chat.srt  # re-flow widths
```

Converting plain text to SRT/VTT invents timings at a typist-friendly pace
(`--chars-per-second 14`, `--no-auto-time` to disable).

## LLM review (optional, local, no keys)

Point it at any OpenAI-compatible chat server. Ollama works out of the box:

```bash
ollama serve                                        # usually already running
ollama pull llama3.2:1b
caption-tidier --llm podcast-transcript.vtt
# with a different server:
caption-tidier --llm --llm-endpoint http://localhost:1234/v1 --llm-model Qwen2.5-7B-Instruct file.srt
# batch mode (no per-line approval — check the sidecar afterwards!):
caption-tidier --llm --llm-yes file.srt
```

Only lines that *look* garbled (repeated letters, long consonant runs, junk
tokens) are sent for review. You see the before/after and choose
`y`/`n`/`q` per line. Nothing leaves your machine, and nothing is rewritten
without approval.

## Safety model

- Inputs are opened read-only; every write goes to a new sidecar path
  (`<name>.<suffix>.<format>`, suffix defaults to `tidy`).
- An existing sidecar is never overwritten unless `--force`.
- `--dry-run` prints exactly what would happen, changing nothing.

## Development

```bash
python -m pytest tests/ -q        # round-trip SRT/VTT tests + cleaning passes
pip install . && caption-tidier --version
```

## Files in this repo

| Path | Role |
| --- | --- |
| `caption_tidier/` | the package: `formats.py` (parse/write), `tidy.py` (cleaning passes), `llm.py` (local review), `cli.py` |
| `tests/` | pytest suite, SRT/VTT round-trip coverage |

## Credits & license

Made by **Asdale Dynamics, LLC** — part of a growing set of beginner-friendly,
local-first tools. Learn more at [dylanasdale.github.io](https://dylanasdale.github.io)
and in *The Massive AI Manual* by Dylan Asdale.

Distributed under the [MIT License](LICENSE) — `Copyright (c) 2026 Asdale Dynamics, LLC`.
