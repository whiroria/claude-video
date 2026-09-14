from .models import feature, provenance


def beat_sync(cuts, beats, music_intervals, threshold_ms=150):
    if threshold_ms < 0:
        raise ValueError("threshold_ms must be nonnegative")
    p = provenance(
        "nearest_event_within_threshold", "timeline", {"threshold_ms": threshold_ms}
    )
    if music_intervals is None:
        return {
            k: feature(prov=p) for k in ("beat_aligned_cut_ratio", "beat_cut_coverage")
        }
    inside = lambda t: any(a <= t < b for a, b in music_intervals)
    cuts, beats = sorted(set(filter(inside, cuts))), sorted(set(filter(inside, beats)))
    from bisect import bisect_left

    def matches(t, seq):
        i = bisect_left(seq, t)
        return any(
            abs(t - seq[j]) <= threshold_ms for j in (i - 1, i) if 0 <= j < len(seq)
        )

    def fraction(a, b):
        return (
            feature(sum(matches(t, b) for t in a) / len(a), prov=p)
            if a
            else feature(status="not_applicable", prov=p)
        )

    return {
        "beat_aligned_cut_ratio": fraction(cuts, beats),
        "beat_cut_coverage": fraction(beats, cuts),
    }


def chapter_music_sync(chapters, music_intervals, threshold_ms=1000):
    """Fraction of internal chapter boundaries near a change in detected music presence."""
    p = provenance(
        "chapter_music_presence_changes", "timeline", {"threshold_ms": threshold_ms}
    )
    if music_intervals is None:
        return feature(prov=p)
    merged = []
    for a, b in sorted(music_intervals):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    boundaries = sorted({c for c in chapters if c > 0})
    changes = [t for interval in merged for t in interval if t > 0]
    if not boundaries:
        return feature(status="not_applicable", prov=p)
    return feature(
        sum(any(abs(c - t) <= threshold_ms for t in changes) for c in boundaries)
        / len(boundaries),
        prov=p,
        kind="model_derived",
    )
