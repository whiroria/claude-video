import json
import pytest
import whisper
from vea.semantic import SCHEMA

@pytest.mark.parametrize("language,expected", [("ja", "ja"), ("en", "en"), ("auto", None)])
def test_request_language(monkeypatch, tmp_path, language, expected):
    monkeypatch.setenv("WATCH_TRANSCRIPT_LANGUAGE", language)
    captured = {}
    def multipart(fields, path):
        captured.update(fields)
        return b"audio", "boundary"
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{"segments": []}'
    monkeypatch.setattr(whisper, "_build_multipart", multipart)
    monkeypatch.setattr(whisper, "urlopen", lambda *a, **k: Response())
    whisper._post_whisper(whisper.OPENAI_ENDPOINT, "fake", "whisper-1", tmp_path / "audio.mp3")
    assert captured.get("language") == expected
    assert whisper.OPENAI_ENDPOINT.endswith("/transcriptions")


def test_invalid_language_stops_before_upload(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_TRANSCRIPT_LANGUAGE", "日本語")
    with pytest.raises(ValueError):
        whisper._post_whisper(whisper.OPENAI_ENDPOINT, "fake", "whisper-1", tmp_path / "missing.mp3")


def test_quality_schema_requires_original_and_nullable_candidate():
    assert "transcript_quality" in SCHEMA["required"]
    issue = SCHEMA["properties"]["transcript_quality"]["properties"]["issues"]["items"]
    assert "source_excerpt" in issue["required"]
    assert "null" in issue["properties"]["suggested_reading"]["type"]
