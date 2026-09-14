"""SQLite persistence for Video Essay Analyzer."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_DB = Path.home() / ".config" / "watch" / "video_essay_analyzer.db"


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source TEXT NOT NULL,
    title TEXT,
    uploader TEXT,
    duration_seconds REAL,
    transcript_source TEXT,
    transcript_text TEXT,
    metadata_json TEXT NOT NULL,
    analysis_json TEXT NOT NULL,
    model TEXT NOT NULL,
    output_language TEXT NOT NULL,
    schema_version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analyses_created_at ON analyses(created_at);
CREATE INDEX IF NOT EXISTS idx_analyses_source ON analyses(source);
"""


def ensure_db(path: str | Path | None = None) -> Path:
    db_path = Path(path).expanduser().resolve() if path else DEFAULT_DB
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
    return db_path


def save_analysis(
    *,
    source_type: str,
    source: str,
    title: str | None,
    uploader: str | None,
    duration_seconds: float | None,
    transcript_source: str | None,
    transcript_text: str | None,
    metadata: dict[str, Any],
    analysis: dict[str, Any],
    model: str,
    output_language: str,
    schema_version: str,
    db_path: str | Path | None = None,
) -> int:
    path = ensure_db(db_path)
    created_at = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(path) as conn:
        cur = conn.execute(
            """
            INSERT INTO analyses (
                created_at, source_type, source, title, uploader, duration_seconds,
                transcript_source, transcript_text, metadata_json, analysis_json,
                model, output_language, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created_at,
                source_type,
                source,
                title,
                uploader,
                duration_seconds,
                transcript_source,
                transcript_text,
                json.dumps(metadata, ensure_ascii=False),
                json.dumps(analysis, ensure_ascii=False),
                model,
                output_language,
                schema_version,
            ),
        )
        return int(cur.lastrowid)
