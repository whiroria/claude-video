from __future__ import annotations

import colorsys
import math
import re
import statistics
import subprocess
from collections import Counter

from .models import feature, provenance


def run(args, binary=False):
    p = subprocess.run(args, capture_output=True, timeout=1800)
    if p.returncode:
        raise RuntimeError(p.stderr.decode(errors="replace")[-1500:])
    return p.stdout if binary else p.stderr.decode(errors="replace")


class FFmpegSceneDetector:
    def __init__(self, threshold=0.3):
        if not 0 < threshold < 1:
            raise ValueError("Scene threshold must be between 0 and 1")
        self.threshold = threshold

    def detect(self, path, duration_ms):
        log = run(
            [
                "ffmpeg",
                "-hide_banner",
                "-i",
                str(path),
                "-an",
                "-vf",
                f"select='gt(scene,{self.threshold})',showinfo",
                "-f",
                "null",
                "-",
            ]
        )
        cuts = sorted(
            {
                round(float(t) * 1000)
                for t in re.findall(r"pts_time:([\d.]+)", log)
                if 0 < float(t) * 1000 < duration_ms
            }
        )
        boundaries = [0, *cuts, duration_ms]
        p = provenance(
            "ffmpeg_scene_threshold", str(path), {"threshold": self.threshold}
        )
        return [
            dict(
                start_ms=a,
                end_ms=b,
                label="shot",
                attributes={"transition_type": "cut" if a else "start"},
                provenance=p,
            )
            for a, b in zip(boundaries, boundaries[1:])
        ]


def editing(shots, duration_ms):
    lengths = [(s["end_ms"] - s["start_ms"]) / 1000 for s in shots]
    p = shots[0]["provenance"]
    values = {
        "shot_count": len(shots),
        "avg_shot_length": statistics.mean(lengths),
        "median_shot_length": statistics.median(lengths),
        "cuts_per_min": (len(shots) - 1) * 60000 / duration_ms,
        "shot_length_variance": statistics.pvariance(lengths),
    }
    features = {k: feature(v, prov=p) for k, v in values.items()}
    pacing = [
        {
            "start_ms": a,
            "end_ms": min(a + 30000, duration_ms),
            "cuts_per_min": sum(
                a <= s["start_ms"] < min(a + 30000, duration_ms) for s in shots[1:]
            )
            * 60000
            / min(30000, duration_ms - a),
        }
        for a in range(0, duration_ms, 30000)
    ]
    return features, pacing


def color(path, shots):
    results = []
    linear = lambda x: x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4
    p = provenance(
        "shot_midpoint_rgb_hsv",
        str(path),
        {
            "size": [64, 64],
            "quantization": 32,
            "color_space": "linear_srgb_for_distance",
        },
    )
    for shot in shots:
        timestamp = (shot["start_ms"] + shot["end_ms"]) / 2000
        raw = run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                str(timestamp),
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-vf",
                "scale=64:64",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-",
            ],
            True,
        )
        if len(raw) != 64 * 64 * 3:
            raise RuntimeError("Incomplete color sample")
        pixels = list(zip(raw[0::3], raw[1::3], raw[2::3]))
        hsv = [colorsys.rgb_to_hsv(*(v / 255 for v in px)) for px in pixels]
        sat = statistics.mean(v[1] for v in hsv)
        bright = statistics.mean(v[2] for v in hsv)
        x = sum(s * math.cos(2 * math.pi * h) for h, s, v in hsv)
        y = sum(s * math.sin(2 * math.pi * h) for h, s, v in hsv)
        hue = (
            (math.atan2(y, x) / (2 * math.pi) % 1) * 360
            if math.hypot(x, y) > 1e-9
            else None
        )
        bins = Counter(
            tuple(min(255, (v // 32) * 32 + 16) for v in px) for px in pixels
        )
        palette = [
            {"rgb": list(rgb), "ratio": n / len(pixels)}
            for rgb, n in bins.most_common(5)
        ]
        results.append(
            {
                "start_ms": shot["start_ms"],
                "end_ms": shot["end_ms"],
                "sample_ms": round(timestamp * 1000),
                "mean_hue": hue,
                "mean_saturation": sat,
                "mean_brightness": bright,
                "dominant_colors": palette,
                "dominant_color_ratio": palette[0]["ratio"],
                "warm_cool_score": statistics.mean(
                    s * math.cos(2 * math.pi * h - math.pi / 6) for h, s, v in hsv
                ),
                "linear_rgb": [
                    statistics.mean(linear(px[i] / 255) for px in pixels)
                    for i in range(3)
                ],
            }
        )
    changes = [
        math.dist(a["linear_rgb"], b["linear_rgb"]) / math.sqrt(3)
        for a, b in zip(results, results[1:])
    ]
    total = sum(r["end_ms"] - r["start_ms"] for r in results)
    weighted = lambda key: (
        sum(r[key] * (r["end_ms"] - r["start_ms"]) for r in results) / total
    )
    f = {
        "dominant_saturation": feature(weighted("mean_saturation"), prov=p),
        "brightness": feature(weighted("mean_brightness"), prov=p),
        "warm_cool_score": feature(weighted("warm_cool_score"), prov=p),
        "color_change_rate": feature(sum(changes) / (total / 60000), prov=p),
        "palette_consistency": feature(
            1 - statistics.mean(changes) if changes else 1, prov=p
        ),
    }
    return f, {"shots": results, "shot_to_shot_color_change": changes, "provenance": p}


class FFmpegAudioClassifier:
    """Only measures silence and loudness; does not infer speech/music from energy."""

    def classify(self, path, duration_ms):
        log = run(
            [
                "ffmpeg",
                "-hide_banner",
                "-i",
                str(path),
                "-vn",
                "-af",
                "silencedetect=noise=-35dB:d=0.3,ebur128",
                "-f",
                "null",
                "-",
            ]
        )
        intervals = []
        start = None
        for kind, value in re.findall(r"silence_(start|end):\s*([\d.]+)", log):
            t = min(duration_ms, round(float(value) * 1000))
            if kind == "start":
                start = t
            elif start is not None:
                intervals.append((start, t))
                start = None
        if start is not None:
            intervals.append((start, duration_ms))
        p = provenance(
            "ffmpeg_silencedetect_ebur128",
            str(path),
            {"threshold_db": -35, "min_silence_s": 0.3},
        )
        match = re.findall(r"I:\s*(-?[\d.]+|[-+]?inf)\s*LUFS", log)
        loudness = float(match[-1]) if match else None
        if loudness is not None and not math.isfinite(loudness):
            loudness = None
        lra = re.findall(r"LRA:\s*([\d.]+)\s*LU", log)
        f = {
            "silence_ratio": feature(
                sum(b - a for a, b in intervals) / duration_ms, prov=p
            ),
            "loudness": feature(loudness, unit="LUFS", prov=p),
            "loudness_variation": feature(
                float(lra[-1]) if lra else None, unit="LRA_LU", prov=p
            ),
        }
        events = [
            dict(start_ms=a, end_ms=b, label="silence", attributes={}, provenance=p)
            for a, b in intervals
        ]
        return f, events


def shot_frames(path, shots, out_dir):
    from pathlib import Path

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for i, shot in enumerate(shots):
        t = (shot["start_ms"] + shot["end_ms"]) / 2000
        output = out_dir / f"shot-{i:06}.jpg"
        run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-ss",
                str(t),
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-vf",
                "scale=512:-2",
                str(output),
            ]
        )
        frames.append(
            {
                "path": str(output),
                "timestamp_seconds": t,
                "shot_start_ms": shot["start_ms"],
                "shot_end_ms": shot["end_ms"],
            }
        )
    return frames


def visual_ratios(events, duration_ms):
    """Duration-weighted midpoint classifications; require full, non-overlapping coverage."""
    intervals = sorted(events, key=lambda e: e["start_ms"])
    if (
        not intervals
        or intervals[0]["start_ms"] != 0
        or intervals[-1]["end_ms"] != duration_ms
    ):
        return {}
    if any(a["end_ms"] != b["start_ms"] for a, b in zip(intervals, intervals[1:])):
        return {}
    p = provenance(
        "duration_weighted_shot_midpoint_estimates",
        "visual_events",
        {
            "event_provenances": [e["provenance"] for e in events],
            "assumption": "Midpoint label represents entire shot; this is a model-derived estimate.",
        },
    )
    totals = {}
    unknown = False
    roll_known = True
    broll = 0
    for e in events:
        a = e.get("attributes", {})
        label = a.get("material_type", e["label"])
        duration = e["end_ms"] - e["start_ms"]
        totals[label] = totals.get(label, 0) + duration
        unknown = unknown or label == "unknown"
        role = a.get("roll_type", "unknown")
        roll_known = roll_known and role in ("A-roll", "B-roll")
        if role == "B-roll":
            broll += duration
    result = {}
    if not unknown:
        for k in (
            "talking_head",
            "photo",
            "illustration",
            "map",
            "chart",
            "animation",
            "text_card",
        ):
            result[k + "_ratio"] = feature(
                totals.get(k, 0) / duration_ms, kind="model_derived", prov=p
            )
    if roll_known:
        result["broll_ratio"] = feature(
            broll / duration_ms, kind="model_derived", prov=p
        )
    return result
