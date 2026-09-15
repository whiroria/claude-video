"""Optional local YAMNet/ONNX speech and music timeline; no API requests."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def merge_windows(windows, label, threshold):
    events = []
    for w in windows:
        score = w[label]
        if score < threshold:
            continue
        if events and w['start_ms'] <= events[-1]['end_ms']:
            events[-1]['end_ms'] = max(events[-1]['end_ms'], w['end_ms'])
            events[-1]['max_score'] = max(events[-1]['max_score'], score)
        else:
            events.append(dict(start_ms=w['start_ms'], end_ms=w['end_ms'],
                               label=label, max_score=score))
    return events


def classify(video, model, labels, *, start=0.0, seconds=60.0, threshold=0.3):
    import numpy as np
    import onnxruntime as ort
    if start < 0 or seconds <= 0 or not 0 < threshold <= 1:
        raise ValueError('Require start >= 0, seconds > 0 and 0 < threshold <= 1')
    with open(labels, encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    indices = {label.lower(): int(next(r['index'] for r in rows if r['display_name'] == label))
               for label in ('Speech', 'Music')}
    pcm = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-ss', str(start),
        '-i', str(video), '-t', str(seconds), '-map', '0:a:0', '-ac', '1', '-ar', '16000',
        '-f', 'f32le', '-'], capture_output=True, check=True).stdout
    y = np.frombuffer(pcm, dtype='<f4').copy()
    if not y.size:
        raise ValueError('No decoded audio in requested interval')
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 2
    session = ort.InferenceSession(str(model), sess_options=opts, providers=['CPUExecutionProvider'])
    # 0.96 s context, 0.48 s hop. Consecutive blocks retain the last half-window.
    hop, size, block = 7680, 15360, 64
    windows = []
    for offset in range(0, len(y), block * hop):
        chunk = y[offset:offset + block * hop + size - hop]
        scores = session.run(['output_0'], {'waveform': chunk})[0]
        if scores.ndim != 2 or scores.shape[1] != len(rows):
            raise ValueError('Unexpected YAMNet output shape or class map')
        for i, score in enumerate(scores[:block]):
            begin = offset + i * hop
            if begin >= len(y):
                break
            windows.append(dict(start_ms=round(start*1000 + begin/16),
                end_ms=round(start*1000 + min(begin+size, len(y))/16),
                **{k: float(score[v]) for k, v in indices.items()}))
    return dict(start_ms=round(start*1000), end_ms=round(start*1000+len(y)/16),
        requested_seconds=seconds, threshold=threshold, windows=windows,
        events=[e for label in indices for e in merge_windows(windows, label, threshold)],
        provenance=dict(method='yamnet_onnx', model_sha256=hashlib.sha256(Path(model).read_bytes()).hexdigest(),
            class_map_sha256=hashlib.sha256(Path(labels).read_bytes()).hexdigest(),
            sample_rate=16000, window_seconds=0.96, hop_seconds=0.48,
            analyzed_at=datetime.now(timezone.utc).isoformat()),
        limitations=['Music includes foreground/film music; background function is unverified.',
            'Speech is not necessarily narrator speech. Scores are not calibrated probabilities.',
            'Below threshold means not detected, not silence or verified absence.',
            'Boundaries have about one second of context; overlapping voice/music is allowed.',
            'Only the stated interval was analyzed; original whole-video features are unchanged.'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('video');p.add_argument('--result', required=True)
    p.add_argument('--model',required=True);p.add_argument('--labels',required=True)
    p.add_argument('--out',required=True);p.add_argument('--start',type=float,default=0)
    p.add_argument('--seconds',type=float,default=60);p.add_argument('--threshold',type=float,default=.3)
    a=p.parse_args()
    if Path(a.out).resolve()==Path(a.result).resolve():
        p.error('Use a new output file to preserve the original analysis')
    result=json.loads(Path(a.result).read_text(encoding='utf-8-sig'))
    # Same file identity prevents attaching unrelated audio to an existing analysis.
    digest=hashlib.sha256()
    with open(a.video,'rb') as f:
        for b in iter(lambda:f.read(1024*1024), b''):digest.update(b)
    if result.get('video_id') != 'local:'+digest.hexdigest():
        p.error('Video must match the local video_id in the analysis result')
    result['media_timeline']=classify(a.video,a.model,a.labels,start=a.start,seconds=a.seconds,threshold=a.threshold)
    out=Path(a.out);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    t=result['media_timeline']
    print(json.dumps(dict(output=str(out),start_ms=t['start_ms'],end_ms=t['end_ms'],windows=len(t['windows']),events=len(t['events']))))

if __name__=='__main__':main()
