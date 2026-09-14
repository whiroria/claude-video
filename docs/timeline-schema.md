# Unified Timeline

Event fields: event_id, video_id, run_id, start_ms, end_ms, event_type, label, confidence, source, model, model_version, analyzed_at, pipeline_version, attributes, provenance. Times are nonnegative integer milliseconds with start <= end <= video duration; interval semantics are [start,end), with point events allowed for beats. No guessed timestamps for untimed transcripts. Provenance: method, source, model, model_version, parameters, confidence, analyzed_at, pipeline_version, extractor_version.

Shot events partition the duration. Representative frame events refer to exact sampled instants and paths, not entire-shot visual classification. Model-derived events retain evidence and method separately from observed measurements.
