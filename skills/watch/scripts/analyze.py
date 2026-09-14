#!/usr/bin/env python3
"""Analyze a YouTube URL or local video as a video essay.

A video source is always required. An optional user transcript can be supplied
as a file path or literal text. Transcript-only analysis is intentionally not
supported because visual evidence is part of the analyzer contract.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from analysis_schema import SCHEMA_VERSION
from download import download, fetch_captions, is_url
from frames import auto_fps, extract_scene_or_uniform, get_metadata
from openai_analysis import DEFAULT_MODEL, analyze_with_openai
from storage import DEFAULT_DB, save_analysis
from transcript_input import load_transcript_input
from transcribe import format_transcript, parse_vtt
from whisper import load_api_key, transcribe_video


def _load_native_transcript(subtitle_path: str | None) -> tuple[str | None, str | None]:
    if not subtitle_path:
        return None, None
    segments = parse_vtt(subtitle_path)
    if not segments:
        return None, None
    return format_transcript(segments), "captions"


def _load_whisper_transcript(video_path: str, work: Path) -> tuple[str | None, str | None]:
    backend, api_key = load_api_key()
    if not backend or not api_key:
        return None, None
    segments, used_backend = transcribe_video(
        video_path,
        work / "audio.mp3",
        backend=backend,
        api_key=api_key,
    )
    return format_transcript(segments), f"whisper ({used_backend})"


def _extract_frames(
    video_path: str,
    work: Path,
    duration: float,
    *,
    max_frames: int,
    resolution: int,
) -> list[dict]:
    fps, target = auto_fps(duration, max_frames=max_frames)
    frames, _ = extract_scene_or_uniform(
        video_path,
        work / "frames",
        fps=fps,
        target_frames=target,
        resolution=resolution,
        max_frames=max_frames,
        dedup=True,
    )
    return frames


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="analyze-video",
        description="Analyze a YouTube URL or local video with optional transcript.",
    )
    ap.add_argument(
        "source",
        help="YouTube/video URL or local video path. Transcript-only input is not allowed.",
    )
    ap.add_argument(
        "--transcript",
        default=None,
        help="Optional transcript file path or literal transcript text. Overrides captions/Whisper.",
    )
    ap.add_argument(
        "--sub-langs",
        default="ja.*,en.*",
        help="Subtitle languages for URL sources (default: ja.*,en.*).",
    )
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"OpenAI model (default: {DEFAULT_MODEL})")
    ap.add_argument("--language", default="ja", help="Analysis output language (default: ja)")
    ap.add_argument("--max-frames", type=int, default=40, help="Maximum sampled frames (default: 40)")
    ap.add_argument("--resolution", type=int, default=512, help="Frame width (default: 512)")
    ap.add_argument("--out", default=None, help="JSON output path")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path")
    ap.add_argument("--no-save", action="store_true", help="Do not save to SQLite")
    ap.add_argument("--work-dir", default=None, help="Keep intermediate files in this directory")
    args = ap.parse_args()

    if args.max_frames < 1:
        raise SystemExit("--max-frames must be greater than zero")

    source = args.source.strip()
    if not source:
        raise SystemExit("A YouTube URL or local video path is required.")

    work = (
        Path(args.work_dir).expanduser().resolve()
        if args.work_dir
        else Path(tempfile.mkdtemp(prefix="video-essay-analyzer-"))
    )
    work.mkdir(parents=True, exist_ok=True)

    supplied_transcript = load_transcript_input(args.transcript)
    transcript_text: str | None = supplied_transcript
    transcript_source: str | None = "user-supplied" if supplied_transcript else None

    url_source = is_url(source)
    caption_result: dict = {"subtitle_path": None, "info": {}}

    # For URLs, fetch lightweight metadata/captions first unless a transcript was supplied.
    if url_source and not supplied_transcript:
        caption_result = fetch_captions(source, work / "captions", sub_langs=args.sub_langs)
        transcript_text, transcript_source = _load_native_transcript(
            caption_result.get("subtitle_path")
        )

    # Always obtain/resolve the actual video: visual evidence is mandatory.
    dl = download(source, work / "download", sub_langs=args.sub_langs)
    video_path = dl["video_path"]
    info = dl.get("info") or caption_result.get("info") or {}

    # download() may have obtained captions even if the lightweight pass did not.
    if not transcript_text:
        transcript_text, transcript_source = _load_native_transcript(dl.get("subtitle_path"))

    media_meta = get_metadata(video_path)
    duration = float(media_meta.get("duration_seconds") or info.get("duration") or 0)

    # Local MP4 / caption-less URL: use configured Groq/OpenAI Whisper when possible.
    if not transcript_text and media_meta.get("has_audio"):
        transcript_text, transcript_source = _load_whisper_transcript(video_path, work)

    frames = _extract_frames(
        video_path,
        work,
        duration,
        max_frames=args.max_frames,
        resolution=args.resolution,
    )
    frame_paths = [str(frame["path"]) for frame in frames]

    source_type = "url" if url_source else "local_video"
    metadata = {
        "source_type": source_type,
        "source": source,
        "title": info.get("title") or Path(video_path).name,
        "uploader": info.get("uploader"),
        "channel_id": info.get("channel_id"),
        "duration_seconds": duration,
        "upload_date": info.get("upload_date"),
        "view_count": info.get("view_count"),
        "like_count": info.get("like_count"),
        "comment_count": info.get("comment_count"),
        "description": info.get("description"),
        "categories": info.get("categories"),
        "tags": info.get("tags"),
        "thumbnail_url": info.get("thumbnail_url"),
        "resolution": {
            "width": media_meta.get("width"),
            "height": media_meta.get("height"),
        },
        "codec": media_meta.get("codec"),
        "transcript_source": transcript_source,
        "frame_count": len(frame_paths),
        "frame_timestamps": [frame.get("timestamp_seconds") for frame in frames],
    }

    analysis = analyze_with_openai(
        metadata=metadata,
        transcript=transcript_text,
        frame_paths=frame_paths,
        thumbnail_url=info.get("thumbnail_url"),
        output_language=args.language,
        model=args.model,
    )

    result = {
        "schema_version": SCHEMA_VERSION,
        "metadata": metadata,
        "analysis": analysis,
    }

    output_path = (
        Path(args.out).expanduser().resolve()
        if args.out
        else work / "analysis.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    db_id = None
    if not args.no_save:
        db_id = save_analysis(
            source_type=source_type,
            source=source,
            title=metadata["title"],
            uploader=metadata["uploader"],
            duration_seconds=duration,
            transcript_source=transcript_source,
            transcript_text=transcript_text,
            metadata=metadata,
            analysis=analysis,
            model=args.model,
            output_language=args.language,
            schema_version=SCHEMA_VERSION,
            db_path=args.db,
        )

    print(json.dumps({
        "ok": True,
        "source": source,
        "transcript_source": transcript_source,
        "frames": len(frame_paths),
        "json_output": str(output_path),
        "sqlite_db": None if args.no_save else str(Path(args.db).expanduser()),
        "database_id": db_id,
        "work_dir": str(work),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
