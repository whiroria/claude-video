"""Structured output schema and prompt for Video Essay Analyzer."""
from __future__ import annotations

SCHEMA_VERSION = "1.0"

VIDEO_ESSAY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "executive_summary": {"type": "string"},
        "content_profile": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "genre": {"type": "string"},
                "target_audience": {"type": "string"},
                "central_question": {"type": "string"},
                "thesis_or_core_message": {"type": "string"},
                "value_proposition": {"type": "string"},
            },
            "required": [
                "genre",
                "target_audience",
                "central_question",
                "thesis_or_core_message",
                "value_proposition",
            ],
        },
        "packaging": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "title_analysis": {"type": "string"},
                "thumbnail_analysis": {"type": "string"},
                "title_thumbnail_fit": {"type": "string"},
                "click_drivers": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "risks": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "title_analysis",
                "thumbnail_analysis",
                "title_thumbnail_fit",
                "click_drivers",
                "risks",
            ],
        },
        "hook": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "first_30_seconds": {"type": "string"},
                "hook_type": {"type": "string"},
                "curiosity_gap": {"type": "string"},
                "stakes": {"type": "string"},
                "clarity": {"type": "string"},
                "score_10": {"type": "integer", "minimum": 1, "maximum": 10},
                "evidence": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "first_30_seconds",
                "hook_type",
                "curiosity_gap",
                "stakes",
                "clarity",
                "score_10",
                "evidence",
            ],
        },
        "structure": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "overall_structure": {"type": "string"},
                "chapters": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "label": {"type": "string"},
                            "approx_time": {"type": "string"},
                            "purpose": {"type": "string"},
                        },
                        "required": ["label", "approx_time", "purpose"],
                    },
                },
                "progression": {"type": "string"},
                "transitions": {"type": "string"},
                "pacing": {"type": "string"},
            },
            "required": [
                "overall_structure",
                "chapters",
                "progression",
                "transitions",
                "pacing",
            ],
        },
        "argumentation": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "main_claims": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "evidence_use": {"type": "string"},
                "reasoning_quality": {"type": "string"},
                "counterarguments_or_limits": {"type": "string"},
            },
            "required": [
                "main_claims",
                "evidence_use",
                "reasoning_quality",
                "counterarguments_or_limits",
            ],
        },
        "storytelling": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "narrative_model": {"type": "string"},
                "tension_and_open_loops": {"type": "string"},
                "characters_or_agents": {"type": "string"},
                "emotional_arc": {"type": "string"},
                "payoff": {"type": "string"},
            },
            "required": [
                "narrative_model",
                "tension_and_open_loops",
                "characters_or_agents",
                "emotional_arc",
                "payoff",
            ],
        },
        "visuals_and_editing": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "visual_grammar": {"type": "string"},
                "asset_types": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "editing_rhythm": {"type": "string"},
                "on_screen_text": {"type": "string"},
                "b_roll_and_graphics": {"type": "string"},
                "visual_evidence": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "visual_grammar",
                "asset_types",
                "editing_rhythm",
                "on_screen_text",
                "b_roll_and_graphics",
                "visual_evidence",
            ],
        },
        "retention": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "strengths": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "dropoff_risks": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "pattern_interrupts": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "payoff_timing": {"type": "string"},
            },
            "required": [
                "strengths",
                "dropoff_risks",
                "pattern_interrupts",
                "payoff_timing",
            ],
        },
        "performance_hypotheses": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "audience_match": {"type": "string"},
                "likely_ctr_drivers": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "likely_watch_time_drivers": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "metadata_signals": {"type": "string"},
                "caveats": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "audience_match",
                "likely_ctr_drivers",
                "likely_watch_time_drivers",
                "metadata_signals",
                "caveats",
            ],
        },
        "reusable_patterns": {
            "type": "array",
            "items": {"type": "string"},
        },
        "improvements": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "priority": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                    "recommendation": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["priority", "recommendation", "rationale"],
            },
        },
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "timestamp": {"type": "string"},
                    "observation": {"type": "string"},
                    "supports": {"type": "string"},
                },
                "required": ["timestamp", "observation", "supports"],
            },
        },
        "confidence": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "overall": {"type": "number", "minimum": 0, "maximum": 1},
                "limitations": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["overall", "limitations"],
        },
    },
    "required": [
        "executive_summary",
        "content_profile",
        "packaging",
        "hook",
        "structure",
        "argumentation",
        "storytelling",
        "visuals_and_editing",
        "retention",
        "performance_hypotheses",
        "reusable_patterns",
        "improvements",
        "evidence",
        "confidence",
    ],
}

ANALYSIS_INSTRUCTIONS = """You are a rigorous Video Essay Analyst.
Analyze only what is supported by the supplied metadata, transcript, thumbnail, and sampled frames.
Distinguish observation from inference. Do not invent retention analytics, CTR, audience demographics,
chapters, or performance causes that are not directly available. When discussing likely performance,
label it as a hypothesis and state limitations. Use timestamps whenever the evidence permits.
For thumbnail fields, explicitly say that the thumbnail is unavailable when no thumbnail image is supplied.
For visual/editing fields, explicitly state limitations when frame coverage is sparse.
Return the analysis in the requested output language and conform exactly to the JSON schema."""
