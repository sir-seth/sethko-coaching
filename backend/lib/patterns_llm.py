"""
T-30 — LLM copy generation for qualifying pattern findings.

One Anthropic call per qualifying finding, capped at MAX_LLM_CALLS_PER_RUN.
Uses tool-calling so headline_payload + body parsing never fails.

New dep: none (anthropic already in requirements).
"""

import json
import logging
from pathlib import Path

import anthropic

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
MAX_LLM_CALLS_PER_RUN = 4

_PROMPT_PATH = Path(__file__).parent / "prompts" / "patterns.md"

_TOOL_DEF = {
    "name": "generate_pattern_card",
    "description": "Output the pattern card headline and body for a discovered correlation.",
    "input_schema": {
        "type": "object",
        "properties": {
            "headline_payload": {
                "type": "object",
                "properties": {
                    "before": {"type": "string"},
                    "italic": {"type": "string"},
                    "after":  {"type": "string"},
                },
                "required": ["before", "italic", "after"],
            },
            "body": {"type": "string"},
        },
        "required": ["headline_payload", "body"],
    },
}

_STUB_PAYLOAD = {"before": "", "italic": "", "after": ""}


def generate_copy_for_findings(findings: list[dict], user_mode: str) -> list[dict]:
    """
    Enrich qualifying findings with LLM-generated headline_payload and body.
    Non-qualifying findings and those beyond the cap get stub values.
    Returns a new list in the same order as the input.
    """
    system = _PROMPT_PATH.read_text(encoding="utf-8")
    client = anthropic.Anthropic()
    calls = 0
    result = []

    for f in findings:
        if not f.get("qualifies") or calls >= MAX_LLM_CALLS_PER_RUN:
            result.append({**f, "headline_payload": _STUB_PAYLOAD, "body": ""})
            continue

        user_msg = json.dumps({
            "predictor_label": f["human_predictor"],
            "response_label":  f["human_response"],
            "lag_days":        f["lag_days"],
            "r":               round(f["r"], 3),
            "n":               f["n"],
            "effect":          round(f["effect"], 1),
            "effect_unit":     f["effect_unit"],
            "user_mode":       user_mode or "gentle",
        })

        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=300,
                temperature=0.3,
                system=system,
                tools=[_TOOL_DEF],
                tool_choice={"type": "tool", "name": "generate_pattern_card"},
                messages=[{"role": "user", "content": user_msg}],
            )
            tool_use = next((b for b in resp.content if b.type == "tool_use"), None)
            if tool_use:
                inp = tool_use.input
                result.append({
                    **f,
                    "headline_payload": inp["headline_payload"],
                    "body": inp.get("body", ""),
                })
                calls += 1
                continue
        except Exception as exc:
            log.warning("patterns_llm: LLM call failed for %s→%s: %s", f["predictor"], f["response"], exc)

        result.append({**f, "headline_payload": _STUB_PAYLOAD, "body": ""})

    return result
