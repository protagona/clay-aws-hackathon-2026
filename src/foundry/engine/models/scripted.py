"""A scripted 'model': replays a JSON script of decisions so runs are deterministic without Bedrock.

Script format: a JSON array of turns, each {"tool": name, "input": {...}} or {"text": "..."}, optionally with
"usage": {"in": n, "out": m}. For complete() (judge/summarizer use) a turn may also be {"raise": "message"}.
"""

from __future__ import annotations

import json
import math
import threading
from pathlib import Path
from typing import Any

from strands.models.model import Model

from foundry.engine.models.base import Completion, CompletionRequest, ProviderError


class ScriptExhausted(Exception):
    pass


def _load(path: Path) -> list[dict]:
    try:
        turns = json.loads(path.read_text())
    except FileNotFoundError:
        raise FileNotFoundError(f"Scripted model script not found: {path}") from None
    if not isinstance(turns, list):
        raise ValueError(f"Scripted model script {path} must be a JSON array of turns")
    return turns


def _estimate(text: str) -> int:
    return math.ceil(len(text) / 4)


class ScriptedModel(Model):
    def __init__(self, model_id: str, script: Path):
        self.model_id = model_id
        self.turns = _load(script)
        self.script = script
        self._i = 0
        self._lock = threading.Lock()
        self.config: dict[str, Any] = {"model_id": model_id}

    def update_config(self, **model_config: Any) -> None:
        self.config.update(model_config)

    def get_config(self) -> dict[str, Any]:
        return self.config

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):  # pragma: no cover
        raise NotImplementedError("scripted model does not support structured output")
        yield  # makes this an async generator

    def _next(self) -> dict:
        with self._lock:
            if self._i >= len(self.turns):
                raise ScriptExhausted(f"script {self.script.name} has no turn {self._i + 1} (it has {len(self.turns)})")
            turn = self.turns[self._i]
            self._i += 1
            return turn

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        self.last_messages = messages  # lets tests see exactly what the agent sent
        turn = self._next()
        usage = turn.get("usage") or {}
        tokens_in = usage.get("in", _estimate((system_prompt or "") + json.dumps(messages, default=str)))
        tokens_out = usage.get("out", _estimate(json.dumps(turn)))
        yield {"messageStart": {"role": "assistant"}}
        if "tool" in turn:
            n = self._i
            yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": f"scripted-{n}", "name": turn["tool"]}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(turn.get("input", {}))}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockDelta": {"delta": {"text": turn.get("text", "")}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
        cache_read = usage.get("cache_read", 0)
        yield {"metadata": {"usage": {"inputTokens": tokens_in, "outputTokens": tokens_out,
                                      "cacheReadInputTokens": cache_read,
                                      "totalTokens": tokens_in + cache_read + tokens_out},
                            "metrics": {"latencyMs": 0}}}


class ScriptedProvider:
    provider = "scripted"

    def __init__(self, model_id: str, script: Path):
        self.model_id = model_id
        self.script = script
        self._model: ScriptedModel | None = None

    def strands_model(self) -> ScriptedModel:
        if self._model is None:
            self._model = ScriptedModel(self.model_id, self.script)
        return self._model

    def complete(self, req: CompletionRequest) -> Completion:
        try:
            turn = self.strands_model()._next()
        except ScriptExhausted as exc:
            raise ProviderError(str(exc)) from None
        if "raise" in turn:
            raise ProviderError(turn["raise"])
        text = turn.get("text", "")
        return Completion(text, _estimate(req.system + req.user), _estimate(text), self.model_id)
