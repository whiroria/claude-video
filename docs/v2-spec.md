# Video Essay Analyzer v2

Research platform over the existing /watch ingestion stack. Required inputs are a video URL or a local video; user transcripts override captions and Whisper. Preserve the standalone skills/watch distribution and legacy analyses table. New CLI lives in scripts/vea, with a root convenience launcher.

## Audit
Base: main 2d3946d, merged PR #1 (feature/video-essay-analyzer). No unmerged v2 branch was present. Existing modules handle ingestion, representative frames, transcript parsing, Responses API and single-record SQLite persistence. Found thumbnail_url call/signature mismatch and undefined thumbnail_url in the payload builder; fix without changing /watch. No heavy backend is currently bundled.

## Acceptance and limits
Integer-ms unified events, append-only runs and performance snapshots, stable video identities, explicit missing statuses, reproducible observed features, separately marked model interpretations, JSON/CSV export and independent module failures. Standard is the default; fast skips quantitative extractors. Deep requests optional backends; unavailable backends must remain visibly unavailable, never be silently presented as complete. Do not infer causal effects or historical 24h/7d values from present-day counts.
