"""Optional line-by-line LLM spellcheck via a local OpenAI-compatible server.

Designed for Ollama (http://localhost:11434/v1) and friends — no API
keys, no cloud. The tool never calls anything automatically; every
suggestion is shown and must be approved (or auto-approved with
``--llm-yes`` for batch runs on files you already trust).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_ENDPOINT = "http://localhost:11434/v1"
DEFAULT_MODEL = "llama3.2:1b"
TIMEOUT_SECONDS = 240  # local models can be slow on first load; failures are graceful

SYSTEM_PROMPT = (
    "You repair garbled speech-recognition captions. Given one caption line, "
    "return ONLY the corrected line with the same wording the speaker most "
    "likely said. Fix gibberish words and obvious mishearings. Do not add "
    "punctuation you are unsure about, do not translate, do not explain, do "
    'not wrap the answer in quotes. If the line is already fine, reply with '
    "exactly: OK"
)

_SUSPECT = [
    re.compile(r"\b\w*?(\w)\1\1\w*\b"),  # aaa / reeepeated letters
    re.compile(r"[bcdfghjklmnpqrstvwxz]{6,}"),  # long no-vowel runs
    re.compile(r"\b(?:uhh+|umm+|hmm+|mmm+|eee+|aaa+)\b", re.IGNORECASE),
    re.compile(r"[A-Za-z]+\d{2,}"),  # mixed "word42" junk
    re.compile(r"(?i)\bthatthat\b|\bthe the\b|\bandand\b"),
]


@dataclass
class LLMConfig:
    endpoint: str = DEFAULT_ENDPOINT
    model: str = DEFAULT_MODEL
    temperature: float = 0.0
    yes: bool = False  # auto-approve (batch mode)


def is_suspect(text: str) -> bool:
    """Heuristic: does this caption look like garbled speech-recognition output?"""
    return any(p.search(text) for p in _SUSPECT)


def request_fix(config: LLMConfig, text: str) -> str | None:
    """Ask the local model to repair one caption line. Returns None on error."""
    url = config.endpoint.rstrip("/") + "/chat/completions"
    payload = {
        "model": config.model,
        "temperature": config.temperature,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
        KeyError,
        OSError,
    ):
        return None
    try:
        return (body["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError, AttributeError):
        return None


def review_cues(cues, config: LLMConfig, out=print, input_fn=input) -> tuple[int, int, int]:
    """Interactive review. Returns (suspect, changed, skipped).

    Only cues that look garbled (or every cue in ``config.all``) are shown.
    Every applied change is user-approved unless ``config.yes`` is set.
    """
    from .tidy import describe_change

    suspects = [cue for cue in cues if cue.text and is_suspect(cue.text)]
    changed = 0
    skipped = 0
    for cue in suspects:
        for attempt in range(3):
            answer = request_fix(config, cue.text)
            if answer is None:
                out(
                    "  [!] LLM endpoint unreachable or invalid "
                    f"({config.endpoint}, model {config.model!r})."
                )
                return len(suspects), changed, len(suspects) - changed
            answer = answer.strip().strip('"')
            if not answer or answer.upper() == "OK" or answer == cue.text:
                skipped += 1
                break
            out(f"  cue {describe_change(cue)}")
            out(f"    - {cue.text}")
            out(f"    + {answer}")
            if config.yes or _approve(out, input_fn):
                cue.with_text(answer)
                changed += 1
                break
        else:
            skipped += 1
        _ = attempt
    return len(suspects), changed, skipped


def _approve(out=print, input_fn=input) -> bool:
    try:
        reply = input_fn("    apply? [y/n/q] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        out("")
        return False
    if reply.startswith("q"):
        raise SystemExit(0)
    return reply.startswith("y")
