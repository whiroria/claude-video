"""Local, opt-in temporal editing review. Never treats candidates as ground truth.

TransNet V2 proposes transitions; registered regional differences distinguish
partial changes and coherent motion. No network/API calls are made here.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from uuid import uuid4

from .models import now

UPSTREAM_REVISION = "85cef72af9a916bdfd7cc94a670c9cdfbf12d1ed"
LABELS = {
    "cut": "背景切替の候補",
    "gradual": "徐々に切り替わる候補",
    "partial": "画面の一部が変わる候補",
    "motion": "パン・ズーム等の動きの候補",
    "uncertain": "判別保留",
}


def probe(path):
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height,duration:format=duration",
                        "-of", "json", str(path)], capture_output=True, timeout=60)
    if p.returncode:
        raise ValueError("動画情報を読み取れません。")
    data = json.loads(p.stdout)
    streams = data.get("streams", [])
    if not streams:
        raise ValueError("動画の映像が見つかりません。")
    duration = float(streams[0].get("duration") or data["format"]["duration"])
    if duration <= 0:
        raise ValueError("動画の長さを確認できません。")
    return duration, streams[0]


def frames(path, width, height, fps, top_fraction, start, seconds):
    """Bounded memory, explicitly map video 0 (ignore attached cover artwork)."""
    import numpy as np
    vf = f"setpts=PTS-STARTPTS,fps={fps},"
    if top_fraction < 1:
        vf += f"crop=iw:trunc(ih*{top_fraction}/2)*2:0:0,"
    vf += f"scale={width}:{height}"
    with tempfile.TemporaryFile() as errors:
        cmd = ["ffmpeg", "-v", "error", "-ss", str(start), "-i", str(path),
               "-t", str(seconds), "-map", "0:v:0", "-an", "-sn", "-vf", vf,
               "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errors,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        size = width * height * 3
        try:
            while True:
                raw = p.stdout.read(size)
                if not raw:
                    break
                if len(raw) != size:
                    raise RuntimeError("映像のフレームを最後まで読み取れませんでした。")
                yield np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3).copy()
            if p.wait(timeout=60):
                errors.seek(0)
                raise RuntimeError(errors.read().decode("utf-8", "replace")[-1500:])
        finally:
            p.stdout.close()
            if p.poll() is None:
                p.kill()
                p.wait()


def model_windows(iterator):
    """Official 100-frame window / 50 stride / 25 context, including EOF."""
    import numpy as np
    iterator = iter(iterator)
    initial = []
    for _ in range(75):
        frame = next(iterator, None)
        if frame is None:
            break
        initial.append(frame)
    if not initial:
        return
    buffer = [initial[0]] * 25 + initial
    actual = len(initial)
    offset = 0
    while actual > offset:
        window = buffer + [buffer[-1]] * (100 - len(buffer))
        valid = min(50, actual - offset)
        yield np.stack(window), valid
        offset += 50
        buffer = window[50:]
        new = []
        for _ in range(50):
            frame = next(iterator, None)
            if frame is None:
                break
            new.append(frame)
        actual += len(new)
        buffer += new


def predict_transitions(path, weights, top_fraction, start, seconds, progress):
    import numpy as np
    import torch
    from .vendor.transnetv2_pytorch import TransNetV2
    torch.set_num_threads(min(4, torch.get_num_threads()))
    model = TransNetV2()
    # NPZ does not unpickle executable objects. Accept only model tensor names.
    with np.load(weights, allow_pickle=False) as data:
        expected = model.state_dict()
        if set(data.files) - {'__license__', '__source__'} != set(expected):
            raise ValueError("TransNet V2 のモデルファイルが対応していません。")
        model.load_state_dict({k: torch.from_numpy(data[k].copy()) for k in expected})
    model.eval()
    predictions = []
    iterator = frames(path, 48, 27, 30, top_fraction, start, seconds)
    try:
        with torch.inference_mode():
            for i, (window, valid) in enumerate(model_windows(iterator)):
                single, extra = model(torch.from_numpy(window[None]))
                a = torch.sigmoid(single)[0, 25:25 + valid, 0].numpy()
                b = torch.sigmoid(extra["many_hot"])[0, 25:25 + valid, 0].numpy()
                predictions.extend(zip(a.tolist(), b.tolist()))
                if i % 20 == 0:
                    progress(f"連続映像の切替を検出中：{min(seconds, len(predictions)/30):.0f} / {seconds:.0f} 秒")
    finally:
        iterator.close()
    if not predictions:
        raise ValueError("解析する映像がありません。")
    return np.asarray(predictions)


def ranges(mask):
    start = None
    for i, value in enumerate(mask):
        if value and start is None:
            start = i
        if not value and start is not None:
            yield start, i - 1
            start = None
    if start is not None:
        yield start, len(mask) - 1


def transition_candidates(predictions, start=0, fps=30):
    """Group adjacent evidence, retain disjoint cuts even when temporally close."""
    import numpy as np
    out = []
    for a, b in ranges(predictions[:, 0] >= .5):
        peak = a + int(np.argmax(predictions[a:b + 1, 0]))
        left, right = a, b
        # Auxiliary head indicates a support window, not verified dissolve edges.
        while left > 0 and predictions[left - 1, 1] >= .35 and peak - left < fps * 2:
            left -= 1
        while right + 1 < len(predictions) and predictions[right + 1, 1] >= .35 and right - peak < fps * 2:
            right += 1
        item = dict(start_ms=round((start + left / fps) * 1000),
                    end_ms=round((start + (right + 1) / fps) * 1000),
                    peak_ms=round((start + peak / fps) * 1000),
                    model_score=round(float(predictions[peak, 0]), 4),
                    transition="gradual" if right - left >= fps * .18 else "cut",
                    source="transnetv2")
        if out and item["start_ms"] < out[-1]["end_ms"]:
            # Only overlapping support regions, not an arbitrary minimum gap.
            out[-1]["end_ms"] = max(out[-1]["end_ms"], item["end_ms"])
            out[-1]["transition"] = "gradual"
            if item["model_score"] > out[-1]["model_score"]:
                out[-1]["peak_ms"] = item["peak_ms"]
                out[-1]["model_score"] = item["model_score"]
        else:
            out.append(item)
    return out


def difference(before, after, preview=False):
    """Estimate coherent translation/rotation/zoom, then inspect residual regions."""
    import cv2
    import numpy as np
    cv2.setNumThreads(1)
    a = cv2.cvtColor(before, cv2.COLOR_RGB2GRAY)
    b = cv2.cvtColor(after, cv2.COLOR_RGB2GRAY)
    valid = np.ones(a.shape, dtype=np.uint8)
    aligned = before
    motion = 0.0
    inlier_ratio = 0.0
    points = cv2.goodFeaturesToTrack(a, 120, .015, 5)
    if points is not None and len(points) >= 8:
        tracked, status, _ = cv2.calcOpticalFlowPyrLK(a, b, points, None,
                                                    winSize=(21, 21), maxLevel=2)
        keep = status.ravel().astype(bool)
        if keep.sum() >= 8:
            matrix, inliers = cv2.estimateAffinePartial2D(points[keep], tracked[keep],
                                                        method=cv2.RANSAC, ransacReprojThreshold=1.5)
            if matrix is not None:
                inlier_ratio = float(inliers.mean())
                scale = float(np.hypot(matrix[0, 0], matrix[0, 1]))
                if inlier_ratio >= .65 and .8 <= scale <= 1.25:
                    h, w = a.shape
                    aligned = cv2.warpAffine(before, matrix, (w, h))
                    valid = cv2.warpAffine(valid, matrix, (w, h), flags=cv2.INTER_NEAREST)
                    motion = float(np.median(np.linalg.norm(tracked[keep] - points[keep], axis=2)))
    blurred_a = cv2.GaussianBlur(aligned, (3, 3), 0).astype(np.float32)
    blurred_b = cv2.GaussianBlur(after, (3, 3), 0).astype(np.float32)
    residual = np.mean(np.abs(blurred_a - blurred_b), axis=2)
    mask = (residual > 22) & (valid > 0)
    area = float(mask.sum() / max(1, valid.sum()))
    ys, xs = np.where(mask)
    box = None if not len(xs) else [float(xs.min() / a.shape[1]), float(ys.min() / a.shape[0]),
                                   float((xs.max()+1) / a.shape[1]), float((ys.max()+1) / a.shape[0])]
    # Fraction of tiles with changes. A few changed pixels in every tile is not a cut.
    tiles = [float(cell.mean()) for row in np.array_split(mask, 6, axis=0)
             for cell in np.array_split(row, 8, axis=1)]
    spread = sum(v > .15 for v in tiles) / len(tiles)
    raw = float(np.mean(np.abs(before.astype(np.float32) - after.astype(np.float32))))
    result = dict(changed_area=round(area, 4), changed_tiles=round(spread, 4),
                motion_px=round(motion, 3), registration_inliers=round(inlier_ratio, 3),
                raw_difference=round(raw, 3), residual_difference=round(float(residual[valid > 0].mean()), 3),
                box=box)
    if preview:
        heat = after.copy()
        heat[mask] = (.35 * heat[mask] + .65 * np.array([255, 65, 55])).astype(np.uint8)
        ok, jpg = cv2.imencode('.jpg', cv2.cvtColor(heat, cv2.COLOR_RGB2BGR))
        result['_preview'] = 'data:image/jpeg;base64,' + base64.b64encode(jpg).decode('ascii') if ok else None
    return result


def regional_scan(path, top_fraction, start, seconds, progress):
    from collections import deque
    rows, history = [], deque(maxlen=6)
    for i, frame in enumerate(frames(path, 192, 108, 10, top_fraction, start, seconds)):
        if len(history) == 6:
            d = difference(history[0], frame)
            d["time_ms"] = round((start + (i - 3) / 10) * 1000)
            rows.append(d)
        history.append(frame)
        if i % 600 == 0:
            progress(f"部分変化と動きを照合中：{i/10:.0f} / {seconds:.0f} 秒")
    return rows


def regional_candidates(rows):
    """Temporal support intervals; persistent movement remains one candidate."""
    active = [r["changed_area"] >= .035 and r["residual_difference"] >= 3 for r in rows]
    # Fill only a single 100ms hole. Do not join genuinely separate rapid cuts.
    for i in range(1, len(active)-1):
        if active[i-1] and active[i+1]:
            active[i] = True
    result = []
    for a, b in ranges(active):
        if b - a < 1:
            continue
        peaks = [max(range(a, b+1), key=lambda i: rows[i]["changed_area"])]
        if b-a > 20:
            # Persistent background movement must not swallow an inserted photo
            # or a later transition in the same above-threshold run.
            local = []
            for i in range(a, b+1):
                value = rows[i]['changed_area']
                near = rows[max(a, i-3):min(b+1, i+4)]
                lo = min(r['changed_area'] for r in rows[max(0, i-10):i+1])
                hi = min(r['changed_area'] for r in rows[i:min(len(rows), i+11)])
                if value >= max(r['changed_area'] for r in near) and value-max(lo, hi) >= .06:
                    local.append(i)
            peaks = []
            for i in sorted(local, key=lambda i: rows[i]['changed_area'], reverse=True):
                if all(abs(i-j) >= 6 for j in peaks):
                    peaks.append(i)
            if not peaks:
                peaks = [max(range(a, b+1), key=lambda i: rows[i]['changed_area'])]
        for peak in sorted(peaks):
            left, right = a, b
            if b-a > 20:
                cutoff = max(.035, rows[peak]['changed_area'] * .5)
                left = right = peak
                while left > a and peak-left < 10 and rows[left-1]['changed_area'] >= cutoff:
                    left -= 1
                while right < b and right-peak < 10 and rows[right+1]['changed_area'] >= cutoff:
                    right += 1
            result.append(dict(start_ms=max(0, rows[left]["time_ms"] - 300),
                               end_ms=rows[right]["time_ms"] + 300,
                               peak_ms=rows[peak]["time_ms"], source="regional_difference",
                               transition="unknown", model_score=None))
    moving = [r["motion_px"] >= .5 and r["registration_inliers"] >= .7
              and r["changed_area"] < .035 for r in rows]
    for a, b in ranges(moving):
        if b-a >= 3:
            result.append(dict(start_ms=rows[a]["time_ms"], end_ms=rows[b]["time_ms"],
                               peak_ms=rows[(a+b)//2]["time_ms"], source="registered_motion",
                               transition="motion", model_score=None))
    return result


def classify(measurement, candidate):
    area, spread = measurement["changed_area"], measurement["changed_tiles"]
    if candidate["source"] == "registered_motion":
        return "motion"
    if (measurement["motion_px"] >= .5 and measurement["registration_inliers"] >= .7
            and area < .1 and measurement['residual_difference'] < measurement['raw_difference'] * .35):
        return "motion"
    if .015 <= area < .45 and spread < .62:
        return "partial"
    if area >= .3 and spread >= .55:
        return candidate["transition"] if candidate["transition"] in ("cut", "gradual") else "uncertain"
    return "uncertain"


def snapshot(path, timestamp, width=384):
    import cv2
    import numpy as np
    p = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(max(0, timestamp)),
                        "-i", str(path), "-map", "0:v:0", "-frames:v", "1",
                        "-vf", f"scale={width}:-2", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                       capture_output=True, timeout=60)
    if p.returncode or not p.stdout:
        raise RuntimeError("照合画像を抽出できませんでした。")
    image = cv2.imdecode(np.frombuffer(p.stdout, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError("照合画像を読み取れませんでした。")
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB), "data:image/jpeg;base64," + base64.b64encode(p.stdout).decode("ascii")


def build_events(path, candidates, rows, top_fraction, start, seconds, progress=lambda _: None):
    import cv2
    candidates = [dict(e) for e in candidates]
    neural = list(candidates)
    for regional in regional_candidates(rows):
        # A regional cluster may cover several neural events during moving footage.
        # Never collapse those events into a single long pseudo-shot.
        matches = [e for e in neural if e["start_ms"] - 300 <= regional["peak_ms"] <= e["end_ms"] + 300]
        if len(matches) == 1 and regional['source'] == 'regional_difference':
            # A one-frame neural peak may be inside a long blur/dissolve. Use
            # the wider regional evidence for clear before/after images only;
            # it is not a verified transition duration or an extra edit count.
            matches[0]['context_start_ms'] = regional['start_ms']
            matches[0]['context_end_ms'] = regional['end_ms']
        if not matches:
            candidates.append(regional)
    candidates.sort(key=lambda e: e["peak_ms"])
    events = []
    for i, e in enumerate(candidates):
        left = max(start, min(e["start_ms"], e.get('context_start_ms', e['start_ms']))/1000 - .15)
        right = min(start+seconds-.05, max(e["end_ms"], e.get('context_end_ms', e['end_ms']))/1000 + .15)
        before, before_url = snapshot(path, left)
        after, after_url = snapshot(path, right)
        cut = lambda frame: cv2.resize(frame[:round(frame.shape[0]*top_fraction)], (192, 108))
        measurement = difference(cut(before), cut(after), preview=True)
        difference_image = measurement.pop('_preview', None)
        label = classify(measurement, e)
        # Do not label a multi-second cluster in live footage as a single edit.
        if e["source"] == "regional_difference" and e["end_ms"]-e["start_ms"] > 2500:
            label = "motion" if measurement["motion_px"] >= .5 else "uncertain"
        e.update(id=f"edit-{i+1:04}", label=label, measurement=measurement,
                 review_status="unreviewed", before_ms=round(left*1000), after_ms=round(right*1000),
                 before_image=before_url, after_image=after_url, difference_image=difference_image)
        e["start_ms"] = max(round(start*1000), e["start_ms"])
        e["end_ms"] = min(round((start+seconds)*1000), e["end_ms"])
        events.append(e)
        if i % 20 == 0:
            progress(f"前後の照合画像を作成中：{i+1} / {len(candidates)} 箇所")
    return events


def review(path, weights, top_fraction=.8, start=0, seconds=None, progress=lambda _: None):
    if not 0.25 <= top_fraction <= 1:
        raise ValueError("対象領域は25〜100%で指定してください。")
    duration, media = probe(path)
    seconds = min(seconds if seconds is not None else duration-start, duration-start)
    if start < 0 or seconds <= 0:
        raise ValueError("解析区間が動画の範囲外です。")
    predictions = predict_transitions(path, weights, top_fraction, start, seconds, progress)
    candidates = transition_candidates(predictions, start)
    rows = regional_scan(path, top_fraction, start, seconds, progress)
    events = build_events(path, candidates, rows, top_fraction, start, seconds, progress)
    return dict(status="ok", version=1, analyzed_at=now(),
                start_ms=round(start*1000), end_ms=round((start+seconds)*1000),
                source=str(Path(path).resolve()), source_size_bytes=Path(path).stat().st_size,
                parameters=dict(neural_fps=30, regional_fps=10, top_fraction=top_fraction,
                                analyzed_frames=len(predictions), regional_comparisons=len(rows),
                                model="TransNet V2", upstream_revision=UPSTREAM_REVISION,
                                weights_sha256=hashlib.sha256(Path(weights).read_bytes()).hexdigest(),
                                single_threshold=.5, support_threshold=.35, registered_lag_ms=600),
                events=events,
                limitations=["候補の検出です。全編集点の検出や分類の正確さを保証しません。",
                             "徐々に切り替わる候補にはディゾルブ・ワイプ等が含まれ、種類は未確定です。",
                             "開始・終了は検出の支持区間です。実際の編集操作の開始・終了とは限りません。",
                             "下部を除外した場合、その領域の字幕や画像の変化は評価しません。",
                             "部分変化は写真・図解・文字・物体の動きを含み、意味の分類には人の照合が必要です。",
                             "30fpsへ変換して全時間帯を解析し、部分変化は10fpsで照合します。短い変化は見逃す場合があります。"])


def main():
    parser = argparse.ArgumentParser(description="Local editing review; no API calls")
    parser.add_argument("video")
    parser.add_argument("--weights", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--top-fraction", type=float, default=.8)
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--seconds", type=float)
    args = parser.parse_args()
    output = Path(args.out)
    data = review(args.video, args.weights, args.top_fraction, args.start, args.seconds,
                  lambda text: print(text, flush=True))
    duration, media = probe(args.video)
    result = dict(run_id=str(uuid4()), metadata=dict(title=Path(args.video).stem,
                  duration_ms=round(duration*1000)), modules={"editing_review": {"status": "ok"}},
                  editing_review=data, status="partial", timeline=[], features={},
                  warnings=["編集リズムのみを解析。台本・音声のAI分析は実行していません。"], errors=[])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    from desktop import make_report
    make_report(output, output.with_suffix(".html"))
    print(json.dumps({"output": str(output), "events": len(data["events"])}), flush=True)


if __name__ == "__main__":
    main()
