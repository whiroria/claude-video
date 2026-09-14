from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from . import VERSION


def now():
    return datetime.now(timezone.utc).isoformat()


class Status(str, Enum):
    OK = "ok"
    UNKNOWN = "unknown"
    UNAVAILABLE = "not_available"
    INAPPLICABLE = "not_applicable"
    FAILED = "analysis_failed"
    SKIPPED = "not_analyzed"
    NULL = "null"


def provenance(
    method, source, parameters=None, model=None, model_version=None, confidence=None
):
    return dict(
        method=method,
        source=source,
        model=model,
        model_version=model_version,
        parameters=parameters or {},
        confidence=confidence,
        analyzed_at=now(),
        pipeline_version=VERSION,
        extractor_version=VERSION,
    )


def feature(value=None, status=None, unit=None, kind="observed", prov=None):
    if value is not None and (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError("Feature value must be a finite number or None")
    status = status or (Status.OK.value if value is not None else Status.UNKNOWN.value)
    if status not in {s.value for s in Status} or (
        (status == "ok") != (value is not None)
    ):
        raise ValueError("Feature value/status mismatch")
    return dict(value=value, status=status, unit=unit, kind=kind, provenance=prov or {})


@dataclass
class Event:
    video_id: str
    run_id: str
    start_ms: int
    end_ms: int
    event_type: str
    label: str
    provenance: dict
    attributes: dict = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid4()))

    def validate(self, duration_ms):
        if type(self.start_ms) is not int or type(self.end_ms) is not int:
            raise ValueError("Timeline requires integer milliseconds")
        if not 0 <= self.start_ms <= self.end_ms <= duration_ms:
            raise ValueError("Event outside video bounds")
        c = self.provenance.get("confidence")
        if c is not None and not 0 <= c <= 1:
            raise ValueError("Invalid confidence")
        return self

    def to_dict(self):
        data = asdict(self)
        for key in (
            "source",
            "model",
            "model_version",
            "confidence",
            "analyzed_at",
            "pipeline_version",
        ):
            data[key] = self.provenance.get(key)
        return data
