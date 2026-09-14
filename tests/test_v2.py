import json
import sqlite3

import pytest
from download import _pick_subtitle
from openai_analysis import _build_user_content
from vea.database import Database
from vea.extractors import FFmpegSceneDetector, color, editing
from vea.models import Event, feature, provenance
from vea.performance import metrics, relative
from vea.pipeline import analyze, validate_source, video_identity
from vea.synchronization import beat_sync


def test_missing_zero_and_validation():
    assert feature(0)["status"] == "ok"
    assert feature()["value"] is None
    for v in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            feature(v)
    with pytest.raises(ValueError):
        feature(0, status="unknown")
    with pytest.raises(ValueError):
        Event("v", "r", 0, 1001, "shot", "x", {}).validate(1000)
    with pytest.raises(ValueError):
        Event("v", "r", 0.0, 100, "shot", "x", {}).validate(1000)


def test_relative_predecessors():
    target = {
        "video_id": "target",
        "channel_id": "c",
        "published_at": "2026-08-20",
        "views": 200,
    }
    candidates = [
        dict(
            video_id=str(i),
            channel_id="c",
            published_at=f"2026-08-{i:02}",
            views=100,
            is_short=False,
        )
        for i in range(1, 20)
    ]
    candidates += [
        dict(
            video_id="future",
            channel_id="c",
            published_at="2026-09-01",
            views=1,
            is_short=False,
        )
    ]
    r = relative(target, candidates)
    assert r["value"] == 2 and r["comparison_video_count"] == 10
    assert r["provenance"]["parameters"]["comparison_video_ids"] == list(
        map(str, range(19, 9, -1))
    )
    candidates[-2]["views"] = None
    r = relative(target, candidates)
    assert r["comparison_video_count"] == 9  # don't substitute an older video
    assert relative(target, [])["value"] is None
    assert (
        relative(
            target,
            [
                dict(
                    video_id="zero",
                    channel_id="c",
                    published_at="2026-08-19",
                    views=0,
                    is_short=False,
                )
            ],
        )["value"]
        is None
    )


def test_counts_and_sync():
    p = metrics(200, 20, 10, "2026-09-01T00:00:00Z", "2026-09-03T00:00:00Z")
    assert p["views_per_day"]["value"] == 100
    assert metrics(0, 0, None, None, "2026-09-03")["likes_per_view"]["value"] is None
    s = beat_sync([1000, 2000, 9000], [1050, 2050, 3000], [(0, 4000)], 150)
    assert s["beat_aligned_cut_ratio"]["value"] == 1
    assert s["beat_cut_coverage"]["value"] == pytest.approx(2 / 3)
    assert beat_sync([], [], [])["beat_cut_coverage"]["status"] == "not_applicable"
    assert beat_sync([100], [], None)["beat_cut_coverage"]["status"] == "unknown"


def test_thumbnail_contract():
    payload = _build_user_content(
        metadata={},
        transcript="hi",
        frame_paths=[],
        thumbnail_path=None,
        thumbnail_url="https://example.com/thumb.jpg",
        output_language="ja",
    )
    assert payload[-1]["image_url"].endswith("thumb.jpg")
    payload = _build_user_content(
        metadata={},
        transcript=None,
        frame_paths=[],
        thumbnail_path=None,
        output_language="ja",
    )
    assert "No visual" in json.loads(payload[0]["text"])["visual_input_note"]


def test_transcript_only_and_language(tmp_path):
    p = tmp_path / "text.txt"
    p.write_text("hello")
    with pytest.raises(ValueError):
        validate_source(str(p))
    for lang in ("ja", "en", "de"):
        (tmp_path / f"video.{lang}.vtt").write_text("")
    assert _pick_subtitle(tmp_path, "de.*,en.*").name == "video.de.vtt"


def test_shots_colors(static_clip):
    shots = FFmpegSceneDetector().detect(str(static_clip), 3000)
    assert len(shots) == 1 and shots[0]["end_ms"] == 3000
    e, _ = editing(shots, 3000)
    assert e["cuts_per_min"]["value"] == 0
    f, c = color(str(static_clip), shots)
    assert f["palette_consistency"]["value"] == 1
    assert c["shots"][0]["dominant_colors"][0]["rgb"][2] > 200


def test_pipeline_storage_export_delete(static_clip, tmp_path, monkeypatch):
    # Supplied transcript prevents Whisper; deliberate API key absence is explicit offline mode.
    def forbidden(*a, **kw):
        raise AssertionError("Whisper must not run")

    monkeypatch.setattr("vea.pipeline._load_whisper_transcript", forbidden)
    path = tmp_path / "v2.db"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE analyses(id INTEGER PRIMARY KEY)")
        c.execute("INSERT INTO analyses VALUES(1)")
    r = analyze(
        str(static_clip),
        transcript="日本語 supplied",
        offline=True,
        db_path=path,
        max_frames=2,
        work_dir=tmp_path / "work",
    )
    assert r["transcript"]["source"] == "user-supplied"
    assert r["features"]["shot_count"]["value"] == 1
    assert r["features"]["music_ratio"]["status"] == "not_applicable"
    assert r["features"]["raw_views"]["status"] == "not_applicable"
    assert r["timeline"] and r["status"] == "partial"
    r2 = analyze(
        str(static_clip),
        transcript="new",
        offline=True,
        db_path=path,
        max_frames=2,
        mode="fast",
        work_dir=tmp_path / "work",
    )
    assert r["video_id"] == r2["video_id"] and r["run_id"] != r2["run_id"]
    db = Database(path)
    assert db.conn.execute("SELECT count(*) FROM videos").fetchone()[0] == 1
    assert db.conn.execute("SELECT count(*) FROM analysis_runs").fetchone()[0] == 2
    assert db.conn.execute("SELECT count(*) FROM analyses").fetchone()[0] == 1
    assert db.export(tmp_path / "export.json") == 1
    assert db.export(tmp_path / "export.csv", "csv") == 1
    assert "not_analyzed" in (tmp_path / "export.csv").read_text(encoding="utf-8-sig")
    assert db.delete(r["video_id"]) == 1
    assert db.conn.execute("SELECT count(*) FROM timeline_events").fetchone()[0] == 0
    assert db.conn.execute("SELECT count(*) FROM analyses").fetchone()[0] == 1
    db.close()


def test_partial_failure(static_clip, tmp_path, monkeypatch):
    def broken(*a, **kw):
        raise RuntimeError("model failure")

    monkeypatch.setattr("vea.pipeline.color", broken)
    r = analyze(
        str(static_clip),
        offline=True,
        transcript="supplied",
        work_dir=tmp_path,
        max_frames=2,
    )
    assert r["features"]["brightness"]["status"] == "analysis_failed"
    assert r["features"]["shot_count"]["value"] == 1
    assert r["errors"][0]["module"] == "color"


def test_youtube_identity(static_clip):
    a = video_identity("https://youtu.be/abcdefghijk?feature=x", static_clip)
    b = video_identity("https://www.youtube.com/watch?v=abcdefghijk", static_clip)
    assert a == b == "yt:abcdefghijk"


def test_snapshot_append_and_export_latest(static_clip, tmp_path):
    r = analyze(
        str(static_clip),
        transcript="text",
        offline=True,
        mode="fast",
        max_frames=1,
        db_path=tmp_path / "db",
    )
    db = Database(tmp_path / "db")
    for views, collected in [
        (10, "2026-09-01T00:00:00+00:00"),
        (20, "2026-09-02T00:00:00+00:00"),
    ]:
        s = dict(
            views=views,
            likes=0,
            comments=None,
            collected_at=collected,
            source="test",
            metrics=metrics(views, 0, None, "2026-08-01", collected),
        )
        with db.conn:
            db.snapshot(r["video_id"], s)
    assert (
        db.conn.execute("SELECT count(*) FROM performance_snapshots").fetchone()[0] == 2
    )
    db.export(tmp_path / "out.json")
    out = json.loads((tmp_path / "out.json").read_text())
    assert out[0]["features"]["raw_views"]["value"] == 20
    assert db.candidates("2026-09-01T12:00:00+00:00")[0]["views"] == 10
    db.close()


def test_api_failure_partial_run(static_clip, tmp_path, monkeypatch):
    monkeypatch.setattr("vea.pipeline.load_openai_api_key", lambda: "fake")

    def fail(**kw):
        raise SystemExit("API failure")

    monkeypatch.setattr("vea.pipeline.analyze_with_openai", fail)
    r = analyze(
        str(static_clip), transcript="user", max_frames=1, db_path=tmp_path / "db"
    )
    assert r["modules"]["script"]["status"] == "analysis_failed"
    assert r["features"]["shot_count"]["value"] == 1
    assert r["status"] == "partial"


def test_supplied_transcript_skips_url_captions(static_clip, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "vea.pipeline.download",
        lambda *a, **kw: dict(video_path=str(static_clip), info={}, subtitle_path=None),
    )

    def forbidden(*a, **kw):
        raise AssertionError("Caption fetch called")

    monkeypatch.setattr("vea.pipeline.fetch_captions", forbidden)
    monkeypatch.setattr("vea.pipeline.load_openai_api_key", lambda: None)
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    r = analyze(
        "https://youtu.be/abcdefghijk",
        transcript="user priority",
        max_frames=1,
        work_dir=tmp_path,
    )
    assert r["transcript"]["text"] == "user priority"
    assert not r["errors"]
    assert r["features"]["relative_channel_performance"]["status"] == "unknown"


def test_api_predecessor_catalog(monkeypatch):
    from vea.youtube_api import collect

    def item(id, day):
        return {
            "id": id,
            "snippet": {"channelId": "c", "publishedAt": f"2026-08-{day:02}T00:00:00Z"},
            "statistics": {"viewCount": "100"},
            "contentDetails": {"duration": "PT10M"},
        }

    def fake(resource, params, key):
        if resource == "channels":
            return {
                "items": [{"contentDetails": {"relatedPlaylists": {"uploads": "p"}}}]
            }
        if resource == "playlistItems":
            return {
                "items": [{"contentDetails": {"videoId": str(i)}} for i in range(1, 20)]
            }
        if params["id"] == "target":
            return {"items": [item("target", 20)]}
        return {"items": [item(str(i), i) for i in range(1, 20)]}

    monkeypatch.setattr("vea.youtube_api.request", fake)
    target, candidates, complete = collect("yt:target", key="fake")
    assert complete and relative(target, candidates, True)["value"] == 1


def test_semantic_evidence_normalization(static_clip, tmp_path, monkeypatch):
    monkeypatch.setattr("vea.pipeline.load_openai_api_key", lambda: "fake")
    result = {
        "structure": {"chapters": []},
        "argumentation": {"main_claims": []},
        "research": {
            "hook_end_ms": 1000,
            "chapters": [
                {"start_ms": 0, "end_ms": 1000, "label": "intro", "evidence": []}
            ],
            "visual_samples": [
                {
                    "frame_index": 0,
                    "material_type": "animation",
                    "roll_type": "unknown",
                    "confidence": 0.5,
                    "evidence": [],
                }
            ],
            "vseo": {
                "thumbnail_score": {"score": 8, "confidence": 0.8, "evidence": []}
            },
        },
    }
    monkeypatch.setattr("vea.pipeline.analyze_with_openai", lambda **kw: result)
    r = analyze(
        str(static_clip), transcript="test", max_frames=1, db_path=tmp_path / "db"
    )
    assert r["features"]["hook_duration"]["value"] == 1
    assert r["features"]["thumbnail_score"]["value"] is None  # actual thumbnail absent
    assert any(e["event_type"] == "chapter" for e in r["timeline"])
    assert any(e["event_type"] == "visual" for e in r["timeline"])
    assert (
        r["features"]["animation_ratio"]["value"] is None
    )  # sample cannot measure duration


def test_visual_ratios_require_full_coverage():
    from vea.extractors import visual_ratios

    p = provenance("test", "test")
    e = [
        dict(
            start_ms=0,
            end_ms=1000,
            label="photo",
            attributes={"roll_type": "B-roll"},
            provenance=p,
        ),
        dict(
            start_ms=1000,
            end_ms=3000,
            label="talking_head",
            attributes={"roll_type": "A-roll"},
            provenance=p,
        ),
    ]
    r = visual_ratios(e, 3000)
    assert r["photo_ratio"]["value"] == pytest.approx(1 / 3)
    assert r["broll_ratio"]["kind"] == "model_derived"
    assert visual_ratios(e[:1], 3000) == {}
    e[1]["start_ms"] = 900
    assert visual_ratios(e, 3000) == {}


def test_deep_optional_backends_and_user_priority(static_clip, tmp_path, monkeypatch):
    p = provenance("mock_beats", "fixture")
    monkeypatch.setattr(
        "vea.pipeline.LibrosaBeats.detect", lambda *a: (120, [500, 1000], p)
    )

    def forbidden(*a):
        raise AssertionError("User transcript must take priority")

    monkeypatch.setattr("vea.deep.transcribe_aligned", forbidden)
    r = analyze(
        str(static_clip),
        mode="deep",
        transcript="priority",
        offline=True,
        max_frames=1,
        work_dir=tmp_path,
        deep_config={"whisperx_model": "unused"},
    )
    assert r["transcript"]["source"] == "user-supplied"
    assert r["features"]["music_ratio"]["status"] == "not_applicable"


def test_audio_silence_metrics(tmp_path):
    import subprocess

    from vea.extractors import FFmpegAudioClassifier

    path = tmp_path / "silence.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=mono",
            "-t",
            "2",
            str(path),
        ],
        check=True,
    )
    f, events = FFmpegAudioClassifier().classify(path, 2000)
    assert f["silence_ratio"]["value"] == pytest.approx(1)
    assert events[0]["start_ms"] == 0 and events[0]["end_ms"] == 2000


def test_chapter_music_sync_merges_contiguous_windows():
    from vea.synchronization import chapter_music_sync

    f = chapter_music_sync([0, 10000, 20000], [(0, 10000), (10000, 15000)], 100)
    assert f["value"] == 0  # 10s is a classifier-window boundary, not a music change


def test_long_literal_transcript():
    from transcript_input import load_transcript_input

    text = "This is a long literal transcript. " * 1000
    assert load_transcript_input(text) == text.strip()
