"""Helpers for optional user-supplied transcripts."""

from __future__ import annotations

from pathlib import Path


def load_transcript_input(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = Path(value).expanduser()
    try:
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        # Literal transcripts may exceed filesystem filename limits.
        pass
    return value.strip() or None
