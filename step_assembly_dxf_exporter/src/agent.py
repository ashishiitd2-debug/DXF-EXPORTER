"""
Thin LLM layer on top of the deterministic pipeline. Does NOT do geometry --
it reviews the pipeline's manifest (classification decisions, confidence,
needs_review flags) and:

  1. Writes a plain-language summary of what was exported.
  2. Flags parts the deterministic classifier was unsure about and explains
     *why* in terms a non-CAD-expert user can act on.
  3. Optionally, if a `user_hint` is provided (e.g. "everything in this
     assembly is laser-cut sheet metal"), re-biases low-confidence calls
     rather than re-running geometry -- it never overrides a HIGH-confidence
     geometric classification, since the geometry kernel is the source of
     truth for actual shape; the LLM's job is judgment under ambiguity and
     communication, not geometry.

This keeps the split clean: pipeline.py is deterministic and testable in
isolation; agent.py is the only place an LLM call happens, and it's optional
-- run_pipeline() works standalone with no API key required.
"""

import json
import os
from dataclasses import dataclass

from anthropic import Anthropic

from .pipeline import run_pipeline
from .convert_projection import ViewDirection


@dataclass
class AgentResult:
    zip_path: str
    manifest: list
    summary: str
    flagged_parts: list


_SYSTEM_PROMPT = """You are reviewing the output of a deterministic STEP-to-DXF \
conversion pipeline for a laser/sheet-metal cutting workflow. You will be given \
a JSON manifest describing each unique part found in the assembly, how it was \
classified (flat profile vs. 3D projection), the confidence of that \
classification, and whether it's flagged for review.

Your job:
1. Write a short (3-6 sentence) plain-language summary of what was exported, \
suitable for a non-CAD-expert user: how many unique parts, how many total \
instances, how many need review and why.
2. For each part with needs_review=true or confidence != "high", explain in \
one sentence what's likely going on and what the user should check before \
trusting that DXF for cutting.

Respond ONLY as JSON: {"summary": "...", "flagged": [{"part_name": "...", "note": "..."}]}
No markdown fences, no other text.
"""


def run_agent_pipeline(step_path: str, out_dir: str, user_hint: str | None = None,
                        view: ViewDirection = ViewDirection.FRONT,
                        model: str = "claude-sonnet-4-6") -> AgentResult:
    pipeline_result = run_pipeline(step_path, out_dir, view=view)
    manifest = pipeline_result["manifest"]

    client = Anthropic()  # picks up ANTHROPIC_API_KEY from env
    user_content = json.dumps({"manifest": manifest, "user_hint": user_hint or ""}, indent=2)

    response = client.messages.create(
        model=model,
        max_tokens=1500,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )

    text = "".join(block.text for block in response.content if block.type == "text")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = {"summary": text, "flagged": []}

    return AgentResult(
        zip_path=pipeline_result["zip_path"],
        manifest=manifest,
        summary=parsed.get("summary", ""),
        flagged_parts=parsed.get("flagged", []),
    )
