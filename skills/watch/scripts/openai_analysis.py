"""OpenAI Responses API client for Video Essay Analyzer."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from analysis_schema import ANALYSIS_INSTRUCTIONS, VIDEO_ESSAY_SCHEMA


RESPONSES_ENDPOINT = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-5.6"


def load_openai_api_key() -> str | None:
    direct = os.environ.get("OPENAI_API_KEY")
    if direct and direct.strip():
        return direct.strip()
    for path in (
        Path.home() / ".config" / "watch" / ".env",
        Path.cwd() / ".env",
    ):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, _, value = raw.partition("=")
            if key.strip() == "OPENAI_API_KEY":
                value = value.strip().strip('"').strip("'")
                if value:
                    return value
    return None


def _image_data_url(path: str | Path) -> str:
    p = Path(path)
    mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
    data = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def _build_user_content(
    *,
    metadata: dict[str, Any],
    transcript: str | None,
    frame_paths: list[str],
    thumbnail_path: str | None,
    thumbnail_url: str | None = None,
    output_language: str,
) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    text_payload = {
        "task": "Analyze this video as a video essay.",
        "output_language": output_language,
        "metadata": metadata,
        "transcript": transcript or "",
        "visual_input_note": (
            "Thumbnail plus sampled frames are attached."
            if (thumbnail_path or thumbnail_url) and frame_paths
            else "Only a thumbnail is attached."
            if (thumbnail_path or thumbnail_url) and not frame_paths
            else "Sampled frames are attached; thumbnail unavailable."
            if frame_paths
            else "No visual images are attached."
        ),
    }
    content.append(
        {
            "type": "input_text",
            "text": json.dumps(text_payload, ensure_ascii=False),
        }
    )
    if thumbnail_path:
        content.append(
            {
                "type": "input_image",
                "image_url": _image_data_url(thumbnail_path),
                "detail": "high",
            }
        )
    if thumbnail_url and not thumbnail_path:
        content.append(
            {"type": "input_image", "image_url": thumbnail_url, "detail": "high"}
        )
    for frame_path in frame_paths:
        content.append(
            {
                "type": "input_image",
                "image_url": _image_data_url(frame_path),
                "detail": "low",
            }
        )
    return content


def analyze_with_openai(
    *,
    metadata: dict[str, Any],
    transcript: str | None,
    frame_paths: list[str],
    thumbnail_path: str | None = None,
    thumbnail_url: str | None = None,
    output_language: str = "ja",
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
    timeout: int = 180,
    schema: dict | None = None,
    instructions: str | None = None,
) -> dict[str, Any]:
    key = api_key or load_openai_api_key()
    if not key:
        raise SystemExit(
            "OPENAI_API_KEY is required for analysis. Set it in the environment "
            "or ~/.config/watch/.env."
        )

    payload = {
        "model": model,
        "instructions": instructions or ANALYSIS_INSTRUCTIONS,
        "input": [
            {
                "role": "user",
                "content": _build_user_content(
                    metadata=metadata,
                    transcript=transcript,
                    frame_paths=frame_paths,
                    thumbnail_path=thumbnail_path,
                    thumbnail_url=thumbnail_url,
                    output_language=output_language,
                ),
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "video_essay_analysis",
                "strict": True,
                "schema": schema or VIDEO_ESSAY_SCHEMA,
            }
        },
    }

    req = urllib.request.Request(
        RESPONSES_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"OpenAI analysis failed ({exc.code}): {body[:1000]}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"OpenAI analysis request failed: {exc}")

    output_text = data.get("output_text")
    if not output_text:
        chunks: list[str] = []
        for item in data.get("output") or []:
            for part in item.get("content") or []:
                if part.get("type") == "output_text" and part.get("text"):
                    chunks.append(part["text"])
        output_text = "\n".join(chunks)
    if not output_text:
        raise SystemExit("OpenAI returned no output_text.")

    try:
        return json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"OpenAI returned invalid JSON: {exc}: {output_text[:500]}")
