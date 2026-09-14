from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from download import VIDEO_EXTS, download, fetch_captions, is_url
from frames import get_metadata
from openai_analysis import analyze_with_openai, load_openai_api_key
from transcribe import format_transcript, parse_vtt
from transcript_input import load_transcript_input

from analyze import _extract_frames, _load_whisper_transcript

from . import VERSION
from .backends import CLIPClassifier, LibrosaBeats
from .database import Database
from .extractors import (
    FFmpegAudioClassifier,
    FFmpegSceneDetector,
    color,
    editing,
    shot_frames,
    visual_ratios,
)
from .models import Event, feature, now, provenance
from .performance import date, metrics, relative
from .semantic import INSTRUCTIONS, SCHEMA
from .synchronization import beat_sync, chapter_music_sync

FEATURE_GROUPS = {
    "script": ["hook_duration", "chapter_count", "claim_count"],
    "editing": [
        "shot_count",
        "avg_shot_length",
        "median_shot_length",
        "cuts_per_min",
        "shot_length_variance",
    ],
    "visual": [
        "broll_ratio",
        "talking_head_ratio",
        "photo_ratio",
        "illustration_ratio",
        "map_ratio",
        "chart_ratio",
        "animation_ratio",
        "text_card_ratio",
    ],
    "color": [
        "dominant_saturation",
        "brightness",
        "color_change_rate",
        "palette_consistency",
        "warm_cool_score",
    ],
    "audio": [
        "BPM",
        "music_ratio",
        "speech_ratio",
        "sfx_ratio",
        "silence_ratio",
        "loudness",
        "loudness_variation",
    ],
    "synchronization": [
        "beat_aligned_cut_ratio",
        "beat_cut_coverage",
        "broll_narration_score",
        "chapter_music_change_rate",
        "visual_change_per_sentence",
        "broll_switch_frequency",
    ],
    "vseo": [
        "keyword_score",
        "title_score",
        "thumbnail_score",
        "content_keyword_alignment",
        "competition_score",
    ],
    "performance": [
        "raw_views",
        "views_per_day",
        "likes_per_view",
        "comments_per_view",
        "relative_channel_performance",
    ],
}


def video_identity(source, path):
    if is_url(source):
        u = urlparse(source)
        host = (u.hostname or "").lower()
        if host in ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"):
            key = (
                u.path.strip("/").split("/")[0]
                if host == "youtu.be"
                else u.path.split("/")[2]
                if u.path.startswith(("/shorts/", "/embed/", "/live/"))
                else parse_qs(u.query).get("v", [""])[0]
            )
            import re

            if re.fullmatch(r"[A-Za-z0-9_-]{11}", key):
                return "yt:" + key
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return ("url:" if is_url(source) else "local:") + h.hexdigest()


def validate_source(source):
    if not source.strip():
        raise ValueError("A video source is required")
    if not is_url(source):
        p = Path(source).expanduser()
        if not p.is_file():
            raise ValueError("Local video does not exist")
        if p.suffix.lower() not in VIDEO_EXTS:
            raise ValueError(
                "Transcript-only input is not allowed; supply a video file or URL"
            )


def analyze(
    source,
    *,
    mode="standard",
    transcript=None,
    sub_langs="ja.*,en.*",
    model="gpt-5.6",
    language="ja",
    max_frames=40,
    resolution=512,
    db_path=None,
    work_dir=None,
    offline=False,
    scene_threshold=0.3,
    clip_model=None,
    audio_checkpoint=None,
    beat_threshold_ms=150,
    include_shorts=True,
    shorts_status=None,
    deep_config=None,
):
    validate_source(source)
    deep_config = deep_config or {}
    if mode not in ("fast", "standard", "deep"):
        raise ValueError("Unknown mode")
    if max_frames < 1 or resolution < 1:
        raise ValueError("Frame count and resolution must be positive")
    if offline and is_url(source):
        raise ValueError("--offline requires a local video")
    started = now()
    run_id = str(uuid4())
    work = (
        Path(work_dir).expanduser().resolve() / run_id
        if work_dir
        else Path(tempfile.mkdtemp(prefix="vea-"))
    )
    work.mkdir(parents=True, exist_ok=True)
    errors = []
    warnings = []
    modules = {}
    timeline = []
    features = {}
    model_analyses = {}
    supplied = load_transcript_input(transcript)
    text = supplied
    tsrc = "user-supplied" if supplied else None
    segments = []
    if (
        supplied
        and transcript
        and len(transcript) < 4096
        and Path(transcript).suffix.lower() == ".vtt"
        and Path(transcript).is_file()
    ):
        segments = parse_vtt(transcript)
        text = format_transcript(segments) if segments else supplied
    caption = {"info": {}, "subtitle_path": None}
    if is_url(source) and not supplied:
        try:
            caption = fetch_captions(source, work / "captions", sub_langs=sub_langs)
        except (Exception, SystemExit) as exc:
            warnings.append("Caption fetch: " + str(exc))
    dl = download(source, work / "download", sub_langs=sub_langs)
    path = dl["video_path"]
    info = dl.get("info") or caption.get("info") or {}
    media = get_metadata(path)
    duration_ms = round(float(media.get("duration_seconds") or 0) * 1000)
    if duration_ms <= 0 or not media.get("width"):
        raise ValueError("Source must contain a video stream with positive duration")
    vid = video_identity(source, path)
    collected = now()
    published = date(info.get("upload_date"))
    if info.get("timestamp"):
        published = datetime.fromtimestamp(info["timestamp"], timezone.utc)
    is_short = (
        shorts_status
        if shorts_status is not None
        else (True if "/shorts/" in source and vid.startswith("yt:") else None)
    )
    metadata = {
        **info,
        "source": source,
        "source_type": "youtube"
        if vid.startswith("yt:")
        else "url"
        if is_url(source)
        else "local_video",
        "title": info.get("title") or Path(path).name,
        "video_id": vid,
        "duration_ms": duration_ms,
        "duration_seconds": duration_ms / 1000,
        "published_at": published.isoformat() if published else None,
        "collected_at": collected,
        "is_short": is_short,
        "thumbnail_url": info.get("thumbnail_url") if is_url(source) else None,
        "media": media,
    }

    def mark(group, status):
        modules[group] = {"status": status}
        for name in FEATURE_GROUPS.get(group, []):
            features[name] = feature(
                status=status,
                prov=provenance("unmeasured", group),
                kind="model_derived"
                if group in ("script", "visual", "vseo")
                else "observed",
            )

    for g in FEATURE_GROUPS:
        mark(g, "not_analyzed" if mode == "fast" else "not_available")
    for g in ("script", "vseo"):
        mark(g, "not_available")
    mark("performance", "not_applicable" if not is_url(source) else "unknown")
    features["video_duration"] = feature(
        duration_ms / 1000, unit="seconds", prov=provenance("ffprobe", path)
    )

    def add(kind, items):
        events = []
        for item in items:
            e = Event(
                vid,
                run_id,
                item["start_ms"],
                item["end_ms"],
                kind,
                item.get("label", kind),
                item["provenance"],
                item.get("attributes", {}),
            )
            events.append(e.validate(duration_ms).to_dict())
        timeline.extend(events)

    def module(group, func):
        try:
            value = func()
            modules[group] = {"status": "ok"}
            return value
        except (Exception, SystemExit) as exc:
            mark(group, "analysis_failed")
            errors.append({"module": group, "message": str(exc)[:1000]})
            return None

    if not supplied and mode == "deep" and deep_config.get("whisperx_model"):
        try:
            from .deep import transcribe_aligned

            aligned, word_prov = transcribe_aligned(path, deep_config)
            segments = aligned["segments"]
            text = format_transcript(segments)
            tsrc = "whisperx"
            word_events = []
            for word in aligned.get("word_segments", []):
                if "start" in word and "end" in word:
                    word_events.append(
                        dict(
                            start_ms=round(word["start"] * 1000),
                            end_ms=round(word["end"] * 1000),
                            label=word["word"],
                            attributes={
                                "speaker": word.get("speaker"),
                                "confidence": word.get("score"),
                            },
                            provenance=word_prov,
                        )
                    )
            add("word", word_events)
            modules["word_alignment"] = {"status": "ok"}
        except (Exception, SystemExit) as exc:
            errors.append({"module": "word_alignment", "message": str(exc)[:1000]})
            modules["word_alignment"] = {"status": "analysis_failed"}
    if not supplied and not text:
        subtitle = caption.get("subtitle_path") or dl.get("subtitle_path")
        if subtitle:
            try:
                segments = parse_vtt(subtitle)
                text = format_transcript(segments) if segments else None
                tsrc = "captions" if text else None
            except Exception as exc:
                warnings.append("Caption parse: " + str(exc))
        if not text and media.get("has_audio") and not offline:
            try:
                text, tsrc = _load_whisper_transcript(path, work)
            except (Exception, SystemExit) as exc:
                errors.append({"module": "transcript", "message": str(exc)[:1000]})
    metadata["transcript_source"] = tsrc
    tp = (
        word_prov
        if tsrc == "whisperx"
        else provenance(
            "supplied_transcript" if supplied else "transcription",
            tsrc or "none",
            {"sub_langs": sub_langs},
        )
    )
    if not text:
        warnings.append("Transcript unavailable; script interpretation is limited.")
    if segments:
        try:
            add(
                "transcript",
                [
                    dict(
                        start_ms=round(s["start"] * 1000),
                        end_ms=round(s["end"] * 1000),
                        label=s["text"],
                        provenance=tp,
                    )
                    for s in segments
                ],
            )
        except ValueError as exc:
            warnings.append("Invalid transcript timing excluded: " + str(exc))
    frames = (
        module(
            "frames",
            lambda: _extract_frames(
                path,
                work,
                duration_ms / 1000,
                max_frames=max_frames,
                resolution=resolution,
            ),
        )
        or []
    )
    fp = provenance(
        "representative_frame_sampling",
        path,
        {"max_frames": max_frames, "resolution": resolution},
    )
    add(
        "frame",
        [
            dict(
                start_ms=min(duration_ms, round(f["timestamp_seconds"] * 1000)),
                end_ms=min(duration_ms, round(f["timestamp_seconds"] * 1000)),
                label="sampled_frame",
                attributes={"path": str(f["path"])},
                provenance=fp,
            )
            for f in frames
        ],
    )
    metadata["frame_timestamps"] = [f["timestamp_seconds"] for f in frames]
    shots = []
    color_result = None
    if mode != "fast":
        detector = FFmpegSceneDetector(scene_threshold)
        if mode == "deep" and deep_config.get("transnet_module"):
            from .deep import TransNetDetector

            detector = TransNetDetector(deep_config)
        shots = module("editing", lambda: detector.detect(path, duration_ms)) or []
        if shots:
            add("shot", shots)
            ef, pacing = editing(shots, duration_ms)
            features.update(ef)
            modules["editing"]["pacing_curve"] = pacing
            c = module("color", lambda: color(path, shots))
            if c:
                features.update(c[0])
                color_result = c[1]
        if media.get("has_audio"):
            audio = module(
                "audio", lambda: FFmpegAudioClassifier().classify(path, duration_ms)
            )
            if audio:
                features.update(audio[0])
                add("audio", audio[1])
                modules["audio"]["status"] = "partial"
            if audio_checkpoint:
                from .backends import PANNsClassifier

                classified = module(
                    "audio_classification",
                    lambda: PANNsClassifier(audio_checkpoint).classify(
                        path, duration_ms
                    ),
                )
                if classified:
                    features.update(classified[0])
                    add("audio", classified[1])
                    modules["audio"]["status"] = "ok"
        else:
            mark("audio", "not_applicable")
        if clip_model:
            visual = module(
                "visual",
                lambda: CLIPClassifier(clip_model).classify(
                    shot_frames(path, shots, work / "shot-frames") if shots else frames
                ),
            )
            if visual:
                add("visual", visual)
                features.update(visual_ratios(visual, duration_ms))
                modules["visual"]["status"] = "partial"
                warnings.append(
                    "CLIP material ratios use shot-midpoint estimates; A/B-roll requires semantic context."
                )
        else:
            warnings.append("Visual material backend not configured (--clip-model).")
    if frames and not offline and load_openai_api_key():
        a = module(
            "script",
            lambda: analyze_with_openai(
                metadata=metadata,
                transcript=text,
                frame_paths=[str(f["path"]) for f in frames],
                thumbnail_url=metadata.get("thumbnail_url"),
                output_language=language,
                model=model,
                schema=SCHEMA,
                instructions=INSTRUCTIONS,
            ),
        )
        if a:
            p = provenance(
                "responses_structured_video_essay",
                source,
                {"language": language, "rubric_version": "2.0", "api_call_count": 1},
                model=model,
                model_version=model,
            )
            model_analyses["video_essay"] = {"result": a, "provenance": p}
            for name, value in [
                ("chapter_count", len(a.get("structure", {}).get("chapters", []))),
                ("claim_count", len(a.get("argumentation", {}).get("main_claims", []))),
            ]:
                features[name] = feature(value, kind="model_derived", prov=p)
            research = a.get("research", {})
            hook = research.get("hook_end_ms")
            if hook is not None and type(hook) is int and 0 <= hook <= duration_ms:
                features["hook_duration"] = feature(
                    hook / 1000, unit="seconds", kind="model_derived", prov=p
                )
            for ch in research.get("chapters", []):
                try:
                    add(
                        "chapter",
                        [
                            dict(
                                start_ms=ch["start_ms"],
                                end_ms=ch["end_ms"],
                                label=ch["label"],
                                attributes={"evidence": ch["evidence"]},
                                provenance=p,
                            )
                        ],
                    )
                except (KeyError, ValueError):
                    warnings.append("Invalid model chapter timing excluded.")
            for v in research.get("visual_samples", []):
                index = v.get("frame_index")
                if type(index) is not int or not 0 <= index < len(frames):
                    warnings.append("Invalid model frame reference excluded.")
                    continue
                t = round(frames[index]["timestamp_seconds"] * 1000)
                vp = {**p, "confidence": v.get("confidence")}
                try:
                    add(
                        "visual",
                        [
                            dict(
                                start_ms=t,
                                end_ms=t,
                                label=v["material_type"],
                                attributes={
                                    **v,
                                    "frame_reference": str(frames[index]["path"]),
                                    "scope": "sample_only",
                                },
                                provenance=vp,
                            )
                        ],
                    )
                except (KeyError, ValueError):
                    warnings.append("Invalid visual model event excluded.")
            if research.get("visual_samples"):
                modules["visual"] = {
                    "status": "partial",
                    "note": "Sample labels; duration ratios unmeasured.",
                }
            for name, score in research.get("vseo", {}).items():
                value = score.get("score")
                if name not in FEATURE_GROUPS["vseo"]:
                    continue
                if value is not None and (
                    not isinstance(value, (int, float)) or not 0 <= value <= 10
                ):
                    warnings.append("Out-of-range VSEO score excluded.")
                    continue
                if name == "thumbnail_score" and not metadata.get("thumbnail_url"):
                    value = None
                sp = {
                    **p,
                    "confidence": score.get("confidence"),
                    "parameters": {
                        **p["parameters"],
                        "evidence": score.get("evidence", []),
                    },
                }
                features[name] = feature(value, kind="model_derived", prov=sp)
            modules["vseo"] = {
                "status": "partial",
                "note": "Rubric-derived title/thumbnail/content scores; SERP/competition unavailable.",
            }
    else:
        warnings.append(
            "Semantic API analysis skipped: offline, missing API key, or missing frames."
        )
    if mode == "deep":
        if media.get("has_audio"):
            beat_source = path
            if deep_config.get("demucs_repo"):
                from .deep import separate

                separated = module(
                    "separation", lambda: separate(path, work, deep_config)
                )
                if separated:
                    beat_source, sep_prov = separated
                    modules["separation"]["provenance"] = sep_prov
            detected = module("beats", lambda: LibrosaBeats().detect(beat_source))
            if detected:
                bpm, beats, p = detected
                features["BPM"] = feature(bpm, prov=p)
                add(
                    "beat",
                    [
                        dict(start_ms=t, end_ms=t, label="beat", provenance=p)
                        for t in beats
                        if 0 <= t <= duration_ms
                    ],
                )
                music = [
                    (e["start_ms"], e["end_ms"])
                    for e in timeline
                    if e["event_type"] == "audio" and e["label"] == "music"
                ]
                music = (
                    music
                    if modules.get("audio_classification", {}).get("status") == "ok"
                    else None
                )
                features.update(
                    beat_sync(
                        [s["start_ms"] for s in shots[1:]],
                        beats,
                        music,
                        beat_threshold_ms,
                    )
                )
        warnings.append(
            "Deep model backends require local packages/weights. Florence-2 and semantic B-roll/narration relevance are not implemented; advanced model paths are unverified until models are provisioned."
        )
    music = [
        (e["start_ms"], e["end_ms"])
        for e in timeline
        if e["event_type"] == "audio" and e["label"] == "music"
    ]
    if modules.get("audio_classification", {}).get("status") != "ok":
        music = None
    chapter_starts = [e["start_ms"] for e in timeline if e["event_type"] == "chapter"]
    if chapter_starts:
        features["chapter_music_change_rate"] = chapter_music_sync(
            chapter_starts, music
        )
    if any(features[k]["status"] == "ok" for k in FEATURE_GROUPS["synchronization"]):
        modules["synchronization"] = {"status": "partial"}
    snapshot = None
    db = Database(db_path) if db_path else None
    try:
        if is_url(source):
            pm = metrics(
                info.get("view_count"),
                info.get("like_count"),
                info.get("comment_count"),
                metadata["published_at"],
                collected,
            )
            candidates = []
            catalog_complete = False
            import os

            if (
                vid.startswith("yt:")
                and os.environ.get("YOUTUBE_API_KEY")
                and not offline
            ):
                try:
                    from .youtube_api import collect

                    observed, candidates, catalog_complete = collect(
                        vid, include_shorts
                    )
                    metadata.update(
                        {
                            k: observed[k]
                            for k in (
                                "published_at",
                                "title",
                                "description",
                                "thumbnail_url",
                            )
                        }
                    )
                    collected = observed["collected_at"]
                    info.update(
                        view_count=observed["views"],
                        like_count=observed["likes"],
                        comment_count=observed["comments"],
                    )
                    pm = metrics(
                        observed["views"],
                        observed["likes"],
                        observed["comments"],
                        observed["published_at"],
                        collected,
                    )
                except Exception as exc:
                    warnings.append(str(exc))
            if catalog_complete:
                pm["relative_channel_performance"] = relative(
                    {**metadata, "views": info.get("view_count")},
                    candidates,
                    include_shorts,
                )
            else:
                pm["relative_channel_performance"] = feature(
                    prov=provenance(
                        "previous_10_median_views",
                        "unavailable_catalog",
                        {"include_shorts": include_shorts},
                    )
                )
                warnings.append(
                    "Complete preceding-video catalog unavailable; channel-relative performance is unknown. Configure YOUTUBE_API_KEY; Shorts exclusion needs verified format metadata."
                )
            features.update(pm)
            snapshot = {
                "views": info.get("view_count"),
                "likes": info.get("like_count"),
                "comments": info.get("comment_count"),
                "collected_at": collected,
                "source": "youtube_data_api" if catalog_complete else "yt-dlp",
                "metrics": pm,
            }
            modules["performance"] = {
                "status": "partial"
                if any(f["status"] != "ok" for f in pm.values())
                else "ok"
            }
        result = {
            "schema_version": VERSION,
            "run_id": run_id,
            "video_id": vid,
            "mode": mode,
            "started_at": started,
            "completed_at": now(),
            "metadata": metadata,
            "transcript": {"text": text, "source": tsrc, "provenance": tp}
            if text
            else None,
            "timeline": sorted(timeline, key=lambda e: (e["start_ms"], e["end_ms"])),
            "features": features,
            "modules": modules,
            "model_analyses": model_analyses,
            "color": color_result,
            "performance_snapshot": snapshot,
            "errors": errors,
            "warnings": warnings,
            "work_dir": str(work),
            "status": "partial" if errors or warnings else "completed",
        }
        if db:
            db.save(result)
        (work / "analysis.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        return result
    finally:
        if db:
            db.close()
