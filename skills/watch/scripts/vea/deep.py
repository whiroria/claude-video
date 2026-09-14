"""Opt-in heavy backends using operator-provisioned packages and model files."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import subprocess
import sys
from pathlib import Path

from .models import provenance


def package_version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def transcribe_aligned(path, config):
    import whisperx

    device = config.get("device", "cpu")
    model_path = Path(config["whisperx_model"])
    align_path = Path(config["alignment_model"])
    if not model_path.exists() or not align_path.exists():
        raise ValueError("WhisperX requires local ASR and alignment models")
    model = whisperx.load_model(
        str(model_path),
        device,
        compute_type="int8" if device == "cpu" else "float16",
        language=config.get("language"),
        local_files_only=True,
    )
    audio = whisperx.load_audio(str(path))
    result = model.transcribe(audio, batch_size=config.get("batch_size", 4))
    align_model, meta = whisperx.load_align_model(
        language_code=result["language"], device=device, model_name=str(align_path)
    )
    aligned = whisperx.align(
        result["segments"],
        align_model,
        meta,
        audio,
        device,
        return_char_alignments=False,
    )
    p = provenance(
        "whisperx_forced_alignment",
        str(path),
        {
            "asr_model": str(model_path),
            "alignment_model": str(align_path),
            "device": device,
        },
        model=str(model_path),
        model_version=config.get("whisperx_revision"),
    )
    return aligned, p


class TransNetDetector:
    def __init__(self, config):
        self.config = config

    def detect(self, path, duration_ms):
        module_path = Path(self.config["transnet_module"])
        weights = Path(self.config["transnet_weights"])
        if not module_path.is_file() or not weights.exists():
            raise ValueError("Provide upstream transnetv2.py and local weights")
        spec = importlib.util.spec_from_file_location(
            "vea_transnet_backend", module_path
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        detector = module.TransNetV2(model_dir=str(weights))
        frames, predictions, _ = detector.predict_video(str(path))
        threshold = self.config.get("transnet_threshold", 0.5)
        scenes = detector.predictions_to_scenes(predictions, threshold=threshold)
        count = len(frames)
        if not count:
            raise ValueError("TransNet decoded no frames")
        boundaries = sorted(
            {
                0,
                duration_ms,
                *[
                    round(int(s[0]) * duration_ms / count)
                    for s in scenes
                    if int(s[0]) > 0
                ],
            }
        )
        p = provenance(
            "transnetv2_scene_starts",
            str(path),
            {"threshold": threshold, "weights": str(weights), "frame_count": count},
            model="TransNetV2",
            model_version=self.config.get("transnet_revision"),
        )
        return [
            dict(
                start_ms=a,
                end_ms=b,
                label="shot",
                attributes={"transition_type": "unknown"},
                provenance=p,
            )
            for a, b in zip(boundaries, boundaries[1:])
        ]


def separate(path, work, config):
    repo = Path(config["demucs_repo"])
    if not repo.is_dir():
        raise ValueError("Demucs requires a local model repository")
    name = config.get("demucs_model", "htdemucs")
    out = Path(work) / "separated"
    args = [
        sys.executable,
        "-m",
        "demucs",
        "--repo",
        str(repo),
        "-n",
        name,
        "--two-stems",
        "vocals",
        "-o",
        str(out),
        str(path),
    ]
    completed = subprocess.run(args, capture_output=True, text=True, timeout=3600)
    if completed.returncode:
        raise RuntimeError("Demucs separation failed: " + completed.stderr[-500:])
    residual = out / name / Path(path).stem / "no_vocals.wav"
    if not residual.exists():
        raise ValueError("Demucs did not produce residual audio")
    return str(residual), provenance(
        "demucs_two_stems",
        str(path),
        {
            "model_repo": str(repo),
            "output": str(residual),
            "note": "Residual may contain music and SFX; it is not proof of a music interval.",
        },
        model=name,
        model_version=config.get("demucs_revision"),
    )
