"""Tests for Video Essay Analyzer helper modules."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "skills" / "watch" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from analysis_schema import SCHEMA_VERSION, VIDEO_ESSAY_SCHEMA  # noqa: E402
from storage import ensure_db, save_analysis  # noqa: E402
from transcript_input import load_transcript_input  # noqa: E402


def test_transcript_input_accepts_literal_text():
    assert load_transcript_input("hello world") == "hello world"


def test_transcript_input_accepts_utf8_file(tmp_path):
    p = tmp_path / "transcript.txt"
    p.write_text("日本語の文字起こし", encoding="utf-8")
    assert load_transcript_input(str(p)) == "日本語の文字起こし"


def test_schema_is_strict_top_level_object():
    assert VIDEO_ESSAY_SCHEMA["type"] == "object"
    assert VIDEO_ESSAY_SCHEMA["additionalProperties"] is False
    assert "executive_summary" in VIDEO_ESSAY_SCHEMA["required"]


def test_sqlite_storage_round_trip(tmp_path):
    db = tmp_path / "analysis.db"
    ensure_db(db)
    row_id = save_analysis(
        source_type="local_video",
        source="video.mp4",
        title="Test",
        uploader=None,
        duration_seconds=12.5,
        transcript_source="user-supplied",
        transcript_text="hello",
        metadata={"title": "Test"},
        analysis={"executive_summary": "ok"},
        model="test-model",
        output_language="ja",
        schema_version=SCHEMA_VERSION,
        db_path=db,
    )
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT source, metadata_json, analysis_json FROM analyses WHERE id = ?",
            (row_id,),
        ).fetchone()
    assert row[0] == "video.mp4"
    assert json.loads(row[1])["title"] == "Test"
    assert json.loads(row[2])["executive_summary"] == "ok"
