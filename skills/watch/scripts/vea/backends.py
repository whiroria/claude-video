"""Optional backends; dependencies and weights are never downloaded implicitly."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .models import provenance

MATERIALS = [
    "talking_head",
    "archival_footage",
    "stock_footage",
    "screen_recording",
    "photo",
    "illustration",
    "map",
    "chart",
    "animation",
    "text_card",
    "other",
    "unknown",
]


class ShotDetector(Protocol):
    def detect(self, path: str, duration_ms: int) -> list[dict]: ...


class VisualClassifier(Protocol):
    def classify(self, frames: list[dict]) -> list[dict]: ...


class AudioEventClassifier(Protocol):
    def classify(self, path: str, duration_ms: int): ...


class Transcriber(Protocol):
    def transcribe(self, path: str) -> dict: ...


class CLIPClassifier:
    def __init__(self, model_path, device="cpu"):
        self.model_path = model_path
        self.device = device

    def classify(self, frames):
        import torch
        import transformers
        from PIL import Image
        from transformers import CLIPModel, CLIPProcessor

        model = (
            CLIPModel.from_pretrained(self.model_path, local_files_only=True)
            .to(self.device)
            .eval()
        )
        proc = CLIPProcessor.from_pretrained(self.model_path, local_files_only=True)
        result = []
        p = provenance(
            "clip_zero_shot_sample",
            self.model_path,
            {"labels": MATERIALS, "device": self.device},
            model=self.model_path,
            model_version=getattr(model.config, "_commit_hash", None),
        )
        p["parameters"]["transformers_version"] = transformers.__version__
        for f in frames:
            with Image.open(f["path"]) as im:
                inputs = proc(
                    text=[
                        "a video frame showing " + x.replace("_", " ")
                        for x in MATERIALS
                    ],
                    images=im.convert("RGB"),
                    return_tensors="pt",
                    padding=True,
                ).to(self.device)
                with torch.no_grad():
                    scores = (
                        model(**inputs)
                        .logits_per_image.softmax(dim=1)[0]
                        .cpu()
                        .tolist()
                    )
            i = max(range(len(scores)), key=scores.__getitem__)
            result.append(
                dict(
                    start_ms=f.get(
                        "shot_start_ms", round(f["timestamp_seconds"] * 1000)
                    ),
                    end_ms=f.get("shot_end_ms", round(f["timestamp_seconds"] * 1000)),
                    label=MATERIALS[i],
                    attributes={
                        "material_type": MATERIALS[i],
                        "roll_type": "unknown",
                        "frame_reference": f["path"],
                        "relative_softmax_score": scores[i],
                        "scope": "shot_midpoint_estimate"
                        if "shot_start_ms" in f
                        else "sample_only",
                    },
                    provenance=p,
                )
            )
        return result


class WhisperXTranscriber:
    def __init__(self, model_path, device="cpu", language=None):
        self.model_path = model_path
        self.device = device
        self.language = language

    def transcribe(self, path):
        import whisperx

        # Caller must provision models locally. Alignment downloads are not performed here.
        if not Path(self.model_path).exists():
            raise ValueError("WhisperX requires a local model path")
        model = whisperx.load_model(
            self.model_path,
            self.device,
            compute_type="int8" if self.device == "cpu" else "float16",
            language=self.language,
            local_files_only=True,
        )
        audio = whisperx.load_audio(path)
        result = model.transcribe(audio, batch_size=4)
        return result


class LibrosaBeats:
    def detect(self, path):
        import librosa
        import numpy as np

        y, sr = librosa.load(path, sr=22050)
        tempo, positions = librosa.beat.beat_track(y=y, sr=sr, units="time")
        return (
            float(np.asarray(tempo).reshape(-1)[0]),
            [round(float(t) * 1000) for t in positions],
            provenance(
                "librosa_beat_track",
                str(path),
                {"sr": sr},
                model="librosa",
                model_version=librosa.__version__,
            ),
        )


class PANNsClassifier:
    def __init__(self, checkpoint, threshold=0.5):
        self.checkpoint = checkpoint
        self.threshold = threshold

    def classify(self, path, duration_ms):
        import librosa
        import numpy as np
        from panns_inference import AudioTagging, labels

        from .models import feature

        if not Path(self.checkpoint).is_file():
            raise ValueError("Provide a local PANNs checkpoint")
        tagger = AudioTagging(checkpoint_path=self.checkpoint, device="cpu")
        y, sr = librosa.load(path, sr=32000)
        indices = {
            name: labels.index(label)
            for name, label in [("speech", "Speech"), ("music", "Music")]
        }
        # AudioSet contains individual sound classes, not a universal calibrated SFX label.
        p = provenance(
            "panns_window_tagging",
            self.checkpoint,
            {"window_ms": 10000, "threshold": self.threshold},
            model="Cnn14",
            model_version="checkpoint:"
            + __import__("hashlib")
            .sha256(Path(self.checkpoint).read_bytes())
            .hexdigest(),
        )
        events = []
        totals = {"speech": 0, "music": 0}
        for start in range(0, duration_ms, 10000):
            end = min(start + 10000, duration_ms)
            chunk = y[round(start * sr / 1000) : round(end * sr / 1000)]
            if not len(chunk):
                continue
            chunk = np.pad(chunk, (0, max(0, sr - len(chunk))))
            scores, _ = tagger.inference(chunk[None, :])
            for label, index in indices.items():
                if float(scores[0, index]) >= self.threshold:
                    totals[label] += end - start
                    events.append(
                        dict(
                            start_ms=start,
                            end_ms=end,
                            label=label,
                            attributes={"score": float(scores[0, index])},
                            provenance=p,
                        )
                    )
        return {
            k + "_ratio": feature(v / duration_ms, prov=p, kind="model_derived")
            for k, v in totals.items()
        }, events
