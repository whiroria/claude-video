from pathlib import Path
import pytest
from vea import pipeline


def test_anchors_survive_duplicate_scene_frames(monkeypatch, tmp_path):
    seen = []
    def extract(path, work, points, **kwargs):
        seen.extend(points)
        return [dict(index=i, timestamp_seconds=t, path=str(tmp_path / f'a{i}.jpg'))
                for i, t in enumerate(points)], {}
    monkeypatch.setattr(pipeline, 'extract_at_timestamps', extract)
    monkeypatch.setattr(pipeline, '_extract_frames', lambda *args, **kwargs: [
        dict(index=0, timestamp_seconds=130.2, path=str(tmp_path / 'duplicate.jpg')),
        dict(index=1, timestamp_seconds=0, path=str(tmp_path / 'opening.jpg')),
    ])
    frames, meta = pipeline.sample_representative_frames('video', tmp_path, 1302, 9, 320)
    assert len(seen) == 5
    assert all(0 < t < 1302 for t in seen)
    assert meta['anchor_count'] == 5
    assert meta['max_unsampled_gap_seconds'] <= 260.41
    assert len(frames) <= 9
    assert all('duplicate.jpg' not in f['path'] for f in frames)
    assert [f['index'] for f in frames] == list(range(len(frames)))


def test_failed_anchors_are_explicit(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, 'extract_at_timestamps', lambda *a, **k: ([], {}))
    monkeypatch.setattr(pipeline, '_extract_frames', lambda *a, **k: [])
    frames, meta = pipeline.sample_representative_frames('video', tmp_path, 100, 1, 320)
    assert not frames
    assert meta['anchor_failed'] == 1
    assert meta['strategy'] == 'scene_only'
    assert meta['max_unsampled_gap_seconds'] == 100


def test_zero_frame_budget_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        pipeline.sample_representative_frames('video', tmp_path, 100, 0, 320)
