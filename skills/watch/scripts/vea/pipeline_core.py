from __future__ import annotations

import json
import os
from pathlib import Path

from .database import Database
from .extractors import editing
from .models import Event, provenance
from .pipeline import analyze as _base_analyze


def discover_transnet_weights(explicit=None):
    """Return the first usable TransNet V2 weights file, or None.

    The desktop UI may pass an explicit model through WATCH_TRANSNET_WEIGHTS.
    Direct CLI runs can keep a model in the skill, watch config, or Downloads.
    """
    skill = Path(__file__).resolve().parents[2]
    env = os.environ.get("WATCH_TRANSNET_WEIGHTS")
    candidates = [
        explicit,
        env,
        skill / "models" / "transnetv2-weights.npz",
        Path.home() / ".config" / "watch" / "models" / "transnetv2-weights.npz",
        Path.home() / "Downloads" / "transnetv2-weights.npz",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.is_file():
            return path.resolve()
    return None


def _local_video(result, source):
    source_path = Path(source).expanduser() if not str(source).startswith(("http://", "https://")) else None
    if source_path is not None and source_path.is_file():
        return source_path.resolve()
    for event in result.get("timeline", []):
        if event.get("event_type") != "frame":
            continue
        candidate = event.get("provenance", {}).get("source") or event.get("source")
        if candidate and Path(candidate).is_file():
            return Path(candidate).resolve()
    return None


def _core_provenance(review, video):
    params = dict(review.get("parameters") or {})
    return provenance(
        "transnetv2_background_transition_core",
        str(video),
        {
            **params,
            "scope": "background_layout_major_scene",
            "timeline_policy": "exclude_partial_overlay_subtitle_pan_zoom_only",
        },
        model="TransNet V2",
        model_version=str(params.get("upstream_revision") or "85cef72af9a916bdfd7cc94a670c9cdfbf12d1ed"),
    )


def _scene_shots(review, video, duration_ms):
    events = sorted(review.get("events") or [], key=lambda e: int(e.get("peak_ms", e.get("start_ms", 0))))
    by_peak = {}
    for event in events:
        peak = int(event.get("peak_ms", (int(event.get("start_ms", 0)) + int(event.get("end_ms", 0))) // 2))
        if not 0 < peak < duration_ms:
            continue
        previous = by_peak.get(peak)
        score = event.get("model_score")
        prev_score = previous.get("model_score") if previous else None
        if previous is None or (score is not None and (prev_score is None or score > prev_score)):
            by_peak[peak] = event
    boundaries = [0, *sorted(by_peak), duration_ms]
    p = _core_provenance(review, video)
    shots = []
    for index, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
        transition = None if index == 0 else by_peak.get(start)
        shots.append(
            {
                "start_ms": start,
                "end_ms": end,
                "label": "scene",
                "attributes": {
                    "scope": "background_layout_major_scene",
                    "transition_type": "start" if transition is None else transition.get("transition", transition.get("label", "uncertain")),
                    "model_score": None if transition is None else transition.get("model_score"),
                },
                "provenance": p,
            }
        )
    return shots


def _timeline_events(result, review, video, scenes):
    duration_ms = int(result["metadata"]["duration_ms"])
    run_id = result["run_id"]
    video_id = result["video_id"]
    p = _core_provenance(review, video)
    timeline = [e for e in result.get("timeline", []) if e.get("event_type") != "shot"]
    for scene in scenes:
        timeline.append(
            Event(
                video_id,
                run_id,
                int(scene["start_ms"]),
                int(scene["end_ms"]),
                "scene",
                "major_scene",
                scene["provenance"],
                scene.get("attributes", {}),
            ).validate(duration_ms).to_dict()
        )
    for item in review.get("events") or []:
        start = max(0, int(item.get("start_ms", item.get("peak_ms", 0))))
        end = min(duration_ms, int(item.get("end_ms", item.get("peak_ms", start))))
        if end < start:
            start, end = end, start
        score = item.get("model_score")
        ep = {**p, "confidence": score if isinstance(score, (int, float)) and 0 <= score <= 1 else None}
        measurement = item.get("measurement") or {}
        timeline.append(
            Event(
                video_id,
                run_id,
                start,
                end,
                "transition",
                str(item.get("transition") or item.get("label") or "uncertain"),
                ep,
                {
                    "transition_type": item.get("transition"),
                    "review_label": item.get("label"),
                    "model_score": score,
                    "peak_ms": item.get("peak_ms"),
                    "changed_area": measurement.get("changed_area"),
                    "changed_tiles": measurement.get("changed_tiles"),
                    "scope": "background_layout_major_scene",
                },
            ).validate(duration_ms).to_dict()
        )
    return sorted(timeline, key=lambda e: (e["start_ms"], e["end_ms"], e["event_type"]))


def enrich_with_transnet(result, video, weights, top_fraction=.8, progress=lambda _: None):
    """Replace low-level shot timeline/metrics with verified background transitions."""
    from .rhythm import review

    duration_ms = int(result["metadata"]["duration_ms"])
    reviewed = review(str(video), str(weights), top_fraction=top_fraction, progress=progress)
    scenes = _scene_shots(reviewed, video, duration_ms)
    result["editing_review"] = reviewed
    result["timeline"] = _timeline_events(result, reviewed, video, scenes)
    result.setdefault("modules", {})["editing_review"] = {
        "status": "ok",
        "detector": "TransNet V2 + regional verification",
        "scope": "background/layout/major-scene transitions",
    }
    if scenes:
        features, pacing = editing(scenes, duration_ms)
        result.setdefault("features", {}).update(features)
        result["modules"]["editing"] = {
            "status": "ok",
            "detector": "TransNet V2 + regional verification",
            "scope": "background/layout/major-scene transitions; not every editorial cut",
            "pacing_curve": pacing,
        }
    result.setdefault("metadata", {})["transition_detector"] = {
        "model": "TransNet V2",
        "weights": str(weights),
        "top_fraction": top_fraction,
        "scope": "background_layout_major_scene",
    }
    result["warnings"] = [
        warning
        for warning in result.get("warnings", [])
        if not str(warning).startswith("画面変化の未検出区間が最長")
    ]
    return result


def _rewrite_result(result):
    work = Path(result["work_dir"])
    work.mkdir(parents=True, exist_ok=True)
    (work / "analysis.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )


def analyze(
    source,
    *args,
    transnet_weights=None,
    transnet_top_fraction=None,
    transnet_enabled=True,
    **kwargs,
):
    """Primary analyzer used by the CLI.

    Base measurement remains available as a fallback, but standard/deep runs
    automatically promote TransNet V2 + regional verification to the canonical
    editing timeline whenever weights and runtime dependencies are available.
    """
    db_path = kwargs.pop("db_path", None)
    mode = kwargs.get("mode", "standard")
    result = _base_analyze(source, *args, db_path=None, **kwargs)
    if transnet_enabled and mode != "fast":
        weights = discover_transnet_weights(transnet_weights)
        if weights is None:
            result.setdefault("modules", {})["editing_review"] = {
                "status": "not_available",
                "detector": "TransNet V2",
            }
            result.setdefault("warnings", []).append(
                "TransNet V2 weights were not found; editing falls back to FFmpeg scene detection. "
                "Set WATCH_TRANSNET_WEIGHTS or place transnetv2-weights.npz in skills/watch/models, "
                "~/.config/watch/models, or Downloads."
            )
        else:
            top_fraction = transnet_top_fraction
            if top_fraction is None:
                top_fraction = float(os.environ.get("WATCH_TRANSNET_TOP_FRACTION", ".8"))
            try:
                video = _local_video(result, source)
                if video is None:
                    raise RuntimeError("Downloaded/local video path could not be resolved for TransNet V2")
                enrich_with_transnet(result, video, weights, top_fraction)
            except (Exception, SystemExit) as exc:
                result.setdefault("modules", {})["editing_review"] = {
                    "status": "analysis_failed",
                    "detector": "TransNet V2",
                }
                result.setdefault("errors", []).append(
                    {"module": "editing_review", "message": str(exc)[:1000]}
                )
                result.setdefault("warnings", []).append(
                    "TransNet V2 editing analysis failed; FFmpeg scene detection remains as fallback."
                )
    result["status"] = "partial" if result.get("errors") or result.get("warnings") else "completed"
    if db_path:
        db = Database(db_path)
        try:
            db.save(result)
        finally:
            db.close()
    _rewrite_result(result)
    return result
