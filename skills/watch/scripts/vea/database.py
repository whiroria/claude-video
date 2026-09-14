from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from .models import now

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS channels(channel_id TEXT PRIMARY KEY, channel_name TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS videos(video_id TEXT PRIMARY KEY, channel_id TEXT REFERENCES channels(channel_id), source_type TEXT NOT NULL,
 source_url TEXT, title TEXT, description TEXT, published_at TEXT, duration_ms INTEGER NOT NULL CHECK(duration_ms>0),
 thumbnail TEXT, is_short INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS analysis_runs(run_id TEXT PRIMARY KEY, video_id TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
 mode TEXT NOT NULL, started_at TEXT, completed_at TEXT, status TEXT, errors_json TEXT NOT NULL, warnings_json TEXT NOT NULL, result_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS performance_snapshots(snapshot_id TEXT PRIMARY KEY, video_id TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
 views INTEGER, likes INTEGER, comments INTEGER, views_per_day REAL, collected_at TEXT NOT NULL, source TEXT NOT NULL, metrics_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS performance_time ON performance_snapshots(video_id,collected_at);
CREATE TABLE IF NOT EXISTS timeline_events(event_id TEXT PRIMARY KEY, video_id TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
 run_id TEXT NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE, start_ms INTEGER NOT NULL CHECK(start_ms>=0),
 end_ms INTEGER NOT NULL CHECK(end_ms>=start_ms), event_type TEXT NOT NULL,label TEXT,confidence REAL,source TEXT,model TEXT,model_version TEXT,
 analyzed_at TEXT,pipeline_version TEXT,attributes_json TEXT NOT NULL,provenance_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS timeline_lookup ON timeline_events(run_id,start_ms,event_type);
CREATE TABLE IF NOT EXISTS transcripts(transcript_id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
 source TEXT,text TEXT,provenance_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS video_features(run_id TEXT NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
 feature_name TEXT NOT NULL,value REAL,status TEXT NOT NULL,unit TEXT,kind TEXT,provenance_json TEXT NOT NULL,PRIMARY KEY(run_id,feature_name));
CREATE TABLE IF NOT EXISTS model_analyses(analysis_id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
 category TEXT NOT NULL,result_json TEXT NOT NULL,provenance_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS vseo_snapshots(snapshot_id TEXT PRIMARY KEY,video_id TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
 query TEXT,region TEXT,language TEXT,rank INTEGER,collected_at TEXT,result_json TEXT NOT NULL,provenance_json TEXT NOT NULL);
"""
EVENT_TABLES = {
    "word": "words",
    "shot": "shots",
    "visual": "visual_segments",
    "audio": "audio_events",
    "beat": "beats",
    "chapter": "chapters",
}


def dumps(v):
    return json.dumps(v, ensure_ascii=False, allow_nan=False)


class Database:
    def __init__(self, path):
        path = Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(SCHEMA)
        for table in EVENT_TABLES.values():
            self.conn.execute(
                f"CREATE TABLE IF NOT EXISTS {table}(event_id TEXT PRIMARY KEY REFERENCES timeline_events(event_id) ON DELETE CASCADE)"
            )
        self.conn.execute(
            "INSERT OR IGNORE INTO schema_migrations VALUES (2,?)", (now(),)
        )
        self.conn.commit()

    def close(self):
        self.conn.close()

    def save(self, r):
        m = r["metadata"]
        v = r["video_id"]
        t = now()
        with self.conn:
            if m.get("channel_id"):
                self.conn.execute(
                    "INSERT INTO channels VALUES(?,?,?,?) ON CONFLICT(channel_id) DO UPDATE SET channel_name=excluded.channel_name,updated_at=excluded.updated_at",
                    (m["channel_id"], m.get("uploader"), t, t),
                )
            self.conn.execute(
                """INSERT INTO videos VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(video_id) DO UPDATE SET
            channel_id=COALESCE(excluded.channel_id,videos.channel_id),title=excluded.title,description=excluded.description,
            published_at=COALESCE(excluded.published_at,videos.published_at),duration_ms=excluded.duration_ms,
            thumbnail=excluded.thumbnail,is_short=COALESCE(excluded.is_short,videos.is_short),updated_at=excluded.updated_at""",
                (
                    v,
                    m.get("channel_id"),
                    m["source_type"],
                    m["source"],
                    m.get("title"),
                    m.get("description"),
                    m.get("published_at"),
                    m["duration_ms"],
                    m.get("thumbnail_url"),
                    m.get("is_short"),
                    t,
                    t,
                ),
            )
            self.conn.execute(
                "INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    r["run_id"],
                    v,
                    r["mode"],
                    r["started_at"],
                    r["completed_at"],
                    r["status"],
                    dumps(r["errors"]),
                    dumps(r["warnings"]),
                    dumps(r),
                ),
            )
            for e in r["timeline"]:
                self.conn.execute(
                    "INSERT INTO timeline_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        e["event_id"],
                        v,
                        r["run_id"],
                        e["start_ms"],
                        e["end_ms"],
                        e["event_type"],
                        e["label"],
                        e["confidence"],
                        e["source"],
                        e["model"],
                        e["model_version"],
                        e["analyzed_at"],
                        e["pipeline_version"],
                        dumps(e["attributes"]),
                        dumps(e["provenance"]),
                    ),
                )
                if e["event_type"] in EVENT_TABLES:
                    self.conn.execute(
                        f"INSERT INTO {EVENT_TABLES[e['event_type']]} VALUES(?)",
                        (e["event_id"],),
                    )
            if r.get("transcript"):
                tr = r["transcript"]
                self.conn.execute(
                    "INSERT INTO transcripts VALUES(?,?,?,?,?)",
                    (
                        str(uuid4()),
                        r["run_id"],
                        tr["source"],
                        tr["text"],
                        dumps(tr["provenance"]),
                    ),
                )
            for k, f in r["features"].items():
                self.conn.execute(
                    "INSERT INTO video_features VALUES(?,?,?,?,?,?,?)",
                    (
                        r["run_id"],
                        k,
                        f["value"],
                        f["status"],
                        f.get("unit"),
                        f.get("kind"),
                        dumps(f["provenance"]),
                    ),
                )
            for category, a in r.get("model_analyses", {}).items():
                self.conn.execute(
                    "INSERT INTO model_analyses VALUES(?,?,?,?,?)",
                    (
                        str(uuid4()),
                        r["run_id"],
                        category,
                        dumps(a["result"]),
                        dumps(a["provenance"]),
                    ),
                )
            if r.get("performance_snapshot"):
                self.snapshot(v, r["performance_snapshot"])

    def snapshot(self, video_id, s):
        self.conn.execute(
            "INSERT INTO performance_snapshots VALUES(?,?,?,?,?,?,?,?,?)",
            (
                str(uuid4()),
                video_id,
                s.get("views"),
                s.get("likes"),
                s.get("comments"),
                s["metrics"]["views_per_day"]["value"],
                s["collected_at"],
                s["source"],
                dumps(s["metrics"]),
            ),
        )

    def candidates(self, collected_at):
        return [
            dict(row)
            for row in self.conn.execute(
                """SELECT v.*,p.views,p.collected_at FROM videos v JOIN performance_snapshots p ON
        p.snapshot_id=(SELECT p2.snapshot_id FROM performance_snapshots p2 WHERE p2.video_id=v.video_id AND p2.collected_at<=?
        ORDER BY p2.collected_at DESC,p2.rowid DESC LIMIT 1)""",
                (collected_at,),
            )
        ]

    def delete(self, video_id):
        with self.conn:
            return self.conn.execute(
                "DELETE FROM videos WHERE video_id=?", (video_id,)
            ).rowcount

    def export(self, path, fmt="json"):
        rows = self.conn.execute("""SELECT r.result_json FROM analysis_runs r WHERE r.run_id=(SELECT r2.run_id FROM analysis_runs r2
        WHERE r2.video_id=r.video_id ORDER BY r2.started_at DESC,r2.rowid DESC LIMIT 1) ORDER BY r.video_id""").fetchall()
        data = [json.loads(r[0]) for r in rows]
        for result in data:
            latest = self.conn.execute(
                "SELECT * FROM performance_snapshots WHERE video_id=? ORDER BY collected_at DESC,rowid DESC LIMIT 1",
                (result["video_id"],),
            ).fetchone()
            if latest:
                latest = dict(latest)
                latest["metrics"] = json.loads(latest.pop("metrics_json"))
                result["performance_snapshot"] = latest
                result["features"].update(latest["metrics"])
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "json":
            path.write_text(dumps(data), encoding="utf-8")
        else:
            names = sorted({k for r in data for k in r["features"]})
            fields = ["video_id", "run_id", "mode", "status"] + [
                k + s for k in names for s in ("", "__status", "__kind", "__provenance")
            ]
            with path.open("w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields)
                w.writeheader()
                for r in data:
                    row = {k: r[k] for k in fields[:4]}
                    for k in names:
                        x = r["features"].get(k, {})
                        row.update(
                            {
                                k: x.get("value"),
                                k + "__status": x.get("status", "not_analyzed"),
                                k + "__kind": x.get("kind"),
                                k + "__provenance": dumps(x.get("provenance", {})),
                            }
                        )
                    w.writerow(row)
        return len(data)
