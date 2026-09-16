"""Behavioral regression tests; neural accuracy is validated on real footage."""
import json
import subprocess

import pytest

np = pytest.importorskip('numpy')
cv2 = pytest.importorskip('cv2')
from vea.rhythm import (model_windows, transition_candidates, difference,
                        classify, regional_candidates, frames, build_events)


@pytest.mark.parametrize('count', [1, 49, 50, 51, 74, 75, 99, 100, 101, 150])
def test_temporal_windows_cover_each_frame_once_including_short_tail(count):
    source = [np.full((1, 1, 3), i, dtype=np.uint8) for i in range(count)]
    decoded = []
    windows = list(model_windows(iter(source)))
    for window, valid in windows:
        assert window.shape == (100, 1, 1, 3)
        decoded.extend(window[25:25+valid, 0, 0, 0].tolist())
    assert decoded == list(range(count))
    assert windows[0][0][0, 0, 0, 0] == 0
    assert windows[-1][0][-1, 0, 0, 0] == count-1


def test_contiguous_transition_response_is_not_many_cuts():
    p = np.zeros((120, 2))
    p[30:42, 0] = .9
    p[22:49, 1] = .8
    events = transition_candidates(p)
    assert len(events) == 1
    assert events[0]['transition'] == 'gradual'
    assert events[0]['start_ms'] < 1000 < events[0]['end_ms']


def test_two_rapid_disjoint_cuts_are_not_joined_by_minimum_gap():
    p = np.zeros((60, 2))
    p[20, 0] = .95
    p[26, 0] = .97
    assert len(transition_candidates(p)) == 2


def textured_frame():
    noise = np.random.default_rng(481).integers(0, 256, (108, 192, 3), dtype=np.uint8)
    return cv2.GaussianBlur(noise, (3, 3), 0)


def test_global_pan_is_registered_and_not_classified_as_a_cut():
    a = textured_frame()
    b = cv2.warpAffine(a, np.array([[1., 0., 5.], [0., 1., 2.]]), (192, 108))
    m = difference(a, b)
    assert m['changed_area'] < .04
    assert m['motion_px'] > 3
    assert classify(m, {'source': 'regional_difference', 'transition': 'unknown'}) == 'motion'


def test_photo_insertion_preserves_background_and_has_local_difference():
    a = textured_frame()
    b = a.copy()
    b[25:70, 55:110] = [255, 10, 20]
    m = difference(a, b, preview=True)
    assert .05 < m['changed_area'] < .25
    assert classify(m, {'source': 'transnetv2', 'transition': 'cut'}) == 'partial'
    assert m['_preview'].startswith('data:image/jpeg;base64,')


def test_partial_event_is_not_lost_inside_long_moving_run():
    rows=[]
    for i in range(60):
        value=.09 + .5 * max(0, 1-abs(i-12)/5) + .7 * max(0, 1-abs(i-45)/5)
        rows.append(dict(time_ms=i*100,changed_area=value,residual_difference=30,
                         motion_px=0, registration_inliers=0))
    events=regional_candidates(rows)
    assert any(abs(e['peak_ms']-1200)<200 for e in events)
    assert any(abs(e['peak_ms']-4500)<200 for e in events)


def test_unicode_video_stream_decodes_at_declared_rate(tmp_path):
    path=tmp_path/'字幕つき.mkv'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=192x108:rate=30',
                    '-t','1','-c:v','ffv1',str(path)],check=True)
    decoded=list(frames(path,48,27,30,.8,0,1))
    assert len(decoded)==30
    assert all(f.shape==(27,48,3) for f in decoded)
