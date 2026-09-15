from __future__ import annotations

import argparse
import json
from pathlib import Path

from storage import DEFAULT_DB

from .database import Database
from .pipeline import analyze


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Video Essay Analyzer v2 — video source required"
    )
    ap.add_argument("source", nargs="?")
    ap.add_argument("--mode", choices=["fast", "standard", "deep"], default="standard")
    ap.add_argument("--transcript")
    ap.add_argument("--title", help="Original public title for a local video or excerpt")
    ap.add_argument("--excerpt", action="store_true", help="Input is a clip, not the complete video")
    ap.add_argument("--sub-langs", default="ja.*,en.*")
    ap.add_argument("--model", default="gpt-5.6")
    ap.add_argument("--language", default="ja")
    ap.add_argument("--max-frames", type=int, default=40)
    ap.add_argument("--resolution", type=int, default=512)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--work-dir")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--scene-threshold", type=float, default=0.3)
    ap.add_argument("--beat-threshold-ms", type=int, default=150)
    ap.add_argument(
        "--deep-config", help="JSON file describing locally installed Deep models"
    )
    ap.add_argument("--clip-model")
    ap.add_argument("--audio-checkpoint")
    ap.add_argument("--include-shorts", action="store_true", default=True)
    ap.add_argument("--exclude-shorts", action="store_false", dest="include_shorts")
    ap.add_argument("--shorts-status", choices=["short", "long"])
    actions = ap.add_mutually_exclusive_group()
    actions.add_argument("--batch", help="UTF-8 file with one source per line")
    actions.add_argument("--export", choices=["json", "csv"])
    actions.add_argument("--list", action="store_true")
    actions.add_argument("--snapshot", metavar="VIDEO_ID")
    actions.add_argument("--delete", metavar="VIDEO_ID")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args(argv)
    if args.snapshot:
        from .operations import refresh_snapshot

        print(
            json.dumps(
                refresh_snapshot(args.db, args.snapshot, args.include_shorts),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.export or args.list or args.delete:
        db = Database(args.db)
        try:
            if args.export:
                if not args.out:
                    ap.error("--export requires --out")
                print(
                    json.dumps(
                        {"exported": db.export(args.out, args.export), "path": args.out}
                    )
                )
                return 0
            if args.list:
                print(
                    json.dumps(
                        [dict(r) for r in db.conn.execute("SELECT * FROM videos")],
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 0
            if not args.yes:
                ap.error(
                    "--delete requires --yes (removes all v2 runs/snapshots for this video; media files remain)"
                )
            print(json.dumps({"deleted": db.delete(args.delete)}))
            return 0
        finally:
            db.close()
    if args.source and args.batch:
        ap.error("Choose source or --batch")
    if not args.source and not args.batch:
        ap.error("Supply a video source or --batch")
    if args.batch and (args.transcript or args.out):
        ap.error(
            "Batch uses per-run output; --transcript and --out are single-video only"
        )
    sources = (
        [args.source]
        if args.source
        else [
            s.strip()
            for s in Path(args.batch).read_text(encoding="utf-8").splitlines()
            if s.strip()
        ]
    )
    if not sources:
        ap.error("Batch is empty")
    failed = 0
    for source in sources:
        try:
            opts = {
                k: getattr(args, k)
                for k in (
                    "mode",
                    "transcript",
                    "title",
                    "excerpt",
                    "sub_langs",
                    "model",
                    "language",
                    "max_frames",
                    "resolution",
                    "work_dir",
                    "offline",
                    "scene_threshold",
                    "clip_model",
                    "audio_checkpoint",
                    "beat_threshold_ms",
                    "include_shorts",
                )
            }
            opts["deep_config"] = (
                json.loads(Path(args.deep_config).read_text(encoding="utf-8"))
                if args.deep_config
                else None
            )
            opts["shorts_status"] = (
                None if not args.shorts_status else args.shorts_status == "short"
            )
            r = analyze(source, db_path=None if args.no_save else args.db, **opts)
            out = Path(args.out) if args.out else Path(r["work_dir"]) / "analysis.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps(r, ensure_ascii=False, indent=2, allow_nan=False),
                encoding="utf-8",
            )
            print(
                json.dumps(
                    {
                        "video_id": r["video_id"],
                        "run_id": r["run_id"],
                        "status": r["status"],
                        "json_output": str(out),
                        "warnings": r["warnings"],
                        "errors": r["errors"],
                    },
                    ensure_ascii=False,
                )
            )
        except (Exception, SystemExit) as exc:
            failed += 1
            print(
                json.dumps(
                    {"source": source, "status": "failed", "error": str(exc)},
                    ensure_ascii=False,
                )
            )
    return 1 if failed else 0
