from vea.audio_timeline import merge_windows


def test_overlap_keeps_speech_and_music_independent():
    w=[dict(start_ms=0,end_ms=960,speech=.9,music=.8),
       dict(start_ms=480,end_ms=1440,speech=.8,music=.1),
       dict(start_ms=1920,end_ms=2000,speech=.1,music=.8)]
    speech=merge_windows(w,'speech',.3)
    music=merge_windows(w,'music',.3)
    assert [(e['start_ms'],e['end_ms']) for e in speech]==[(0,1440)]
    assert [(e['start_ms'],e['end_ms']) for e in music]==[(0,960),(1920,2000)]
    assert all(e['end_ms']<=2000 for e in speech+music)


def test_below_threshold_is_not_a_silence_event():
    w=[dict(start_ms=0,end_ms=960,speech=.1,music=.1)]
    assert merge_windows(w,'speech',.3)==[]
    assert merge_windows(w,'music',.3)==[]
