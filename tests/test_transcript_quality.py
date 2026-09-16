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


def test_pipeline_after_whisper_language_recording(monkeypatch, tmp_path):
    import subprocess
    from vea import pipeline
    video = tmp_path / "speech.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=blue:s=64x64:r=2:d=1", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1", "-c:v", "libx264", "-c:a", "aac",
                    "-shortest", str(video)], check=True)
    monkeypatch.setenv("WATCH_TRANSCRIPT_LANGUAGE", "ja")
    monkeypatch.setattr(pipeline, "_load_whisper_transcript",
                        lambda *a: ("[00:00] 日本語の字幕です。", "whisper (openai)"))
    monkeypatch.setattr(pipeline, "load_openai_api_key", lambda: None)
    result = pipeline.analyze(str(video), max_frames=1, resolution=64, work_dir=tmp_path)
    assert result["metadata"]["whisper_language_requested"] == "ja"
    assert result["transcript"]["text"] == "[00:00] 日本語の字幕です。"
