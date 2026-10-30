"""Deterministic stand-ins for the judge and summarizer so evaluation runs with no model access.

Responses are real JSON/text that go through the same parser and validator as a real model's output.
"""

from __future__ import annotations

import json
import re

from foundry.models.base import Completion, CompletionRequest

STEP_LINE = re.compile(r"^#\d+ ", re.MULTILINE)


class OfflineProvider:
    provider = "offline"

    def __init__(self, model_id: str):
        self.model_id = model_id

    def strands_model(self):
        raise ValueError("provider 'offline' only serves the judge and summarizer; use 'scripted' or 'bedrock' for [model]")

    def complete(self, req: CompletionRequest) -> Completion:
        if req.purpose == "judge":
            if req.rubric is None:
                raise ValueError("offline judge needs the rubric on the request")
            text = json.dumps({"criteria": [
                {"id": c.id, "score": c.max - 1, "rationale": "Offline judge: fixed score for pipeline testing."}
                for c in req.rubric.criteria]})
        else:
            n = len(STEP_LINE.findall(req.user))
            text = (f"Offline summarizer: the agent's trace has {n} steps. "
                    "This narrative is a fixed placeholder used when no summarizer model is configured.")
        return Completion(text, len(req.system + req.user) // 4, len(text) // 4, self.model_id)
