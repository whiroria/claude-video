"""Helpers for optional user-supplied transcripts."""
from __future__ import annotations

from pathlib import Path


def load_transcript_input(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = Path(value).expanduser()
    if candidate.exists() and candidate.is_file():
        return candidate.read_text(encoding="utf-8", errors="replace").strip()
    return value.strip() or None
