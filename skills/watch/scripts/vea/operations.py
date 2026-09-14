"""Snapshot refresh without downloading/reanalyzing media."""

import json
import os
import subprocess

from .database import Database
from .models import feature, now, provenance
from .performance import metrics, relative


def refresh_snapshot(db_path, video_id, include_shorts=True):
    db = Database(db_path)
    try:
        row = db.conn.execute(
            "SELECT * FROM videos WHERE video_id=?", (video_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Video ID is not in this database")
        target = dict(row)
        if not video_id.startswith("yt:"):
            raise ValueError("Snapshot refresh currently requires a YouTube video")
        complete = False
        candidates = []
        if os.environ.get("YOUTUBE_API_KEY"):
            from .youtube_api import collect

            observed, candidates, complete = collect(video_id, include_shorts)
        else:
            cmd = [
                "yt-dlp",
                "--skip-download",
                "--no-playlist",
                "--dump-single-json",
                "--",
                target["source_url"],
            ]
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if p.returncode:
                raise RuntimeError("yt-dlp metadata refresh failed")
            raw = json.loads(p.stdout)
            observed = {
                **target,
                "views": raw.get("view_count"),
                "likes": raw.get("like_count"),
                "comments": raw.get("comment_count"),
                "collected_at": now(),
                "source": "yt-dlp",
            }
        values = metrics(
            observed.get("views"),
            observed.get("likes"),
            observed.get("comments"),
            observed.get("published_at"),
            observed["collected_at"],
        )
        values["relative_channel_performance"] = (
            relative(observed, candidates, include_shorts)
            if complete
            else feature(
                prov=provenance("previous_10_median_views", "unavailable_catalog")
            )
        )
        snapshot = {**observed, "metrics": values}
        with db.conn:
            db.snapshot(video_id, snapshot)
        return snapshot
    finally:
        db.close()
