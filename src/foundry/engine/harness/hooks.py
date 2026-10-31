"""Strands hook callbacks that turn model and tool calls into trace steps and enforce the run limits."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from jsonschema import validators
from strands.hooks import (
    AfterModelCallEvent,
    AfterToolCallEvent,
    BeforeModelCallEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)

from foundry.data.runs import utc_now
from foundry.engine.tracing.base import TracerSet

PREVIEW_CHARS = 1000
ERROR_CHARS = 500


@dataclass
class Limits:
    max_model_calls: int
    max_input_tokens: int
    tool_result_max_chars: int


@dataclass
class _Pending:
    started_at: str
    t0: float
    invalid: bool = False


@dataclass
class TraceHooks(HookProvider):
    tracers: TracerSet
    limits: Limits
    model_id: str
    prices: dict[str, Any]
    tool_server: dict[str, str]
    tool_schema: dict[str, dict]
    error_detection: dict[str, str]

    model_calls: int = 0
    input_tokens: int = 0
    last_tokens_in: int = 0
    limit_hit: str | None = None
    timed_out: bool = False
    _cancelled_call: bool = False
    _model_started: tuple[str, float] | None = None
    _tools: dict[str, _Pending] = field(default_factory=dict)
    _errored: dict[tuple, int] = field(default_factory=dict)
    parent: int | None = None  # the running agent's agent_step in pipeline mode (increment 7)

    def start_agent(self, step_no: int) -> None:
        """Steps from here on belong to agent_step `step_no`; retries are tracked per agent (P4)."""
        self.parent = step_no
        self._errored.clear()

    def _record(self, **fields: Any):
        if self.parent is not None:
            fields["parent"] = self.parent
        return self.tracers.record(**fields)

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeModelCallEvent, self.before_model)
        registry.add_callback(AfterModelCallEvent, self.after_model)
        registry.add_callback(BeforeToolCallEvent, self.before_tool)
        registry.add_callback(AfterToolCallEvent, self.after_tool)

    # --- model calls -------------------------------------------------------------------------------

    def before_model(self, event: BeforeModelCallEvent) -> None:
        if self.timed_out:  # nothing runs after a timeout, even an agent started as the run was cancelled (P3)
            event.cancel = "run timed out"
            self._cancelled_call = True
            return
        projected = event.projected_input_tokens
        if projected is None:
            projected = self.last_tokens_in
        reason = None
        if self.model_calls + 1 > self.limits.max_model_calls:
            reason = f"max_model_calls ({self.limits.max_model_calls}) reached"
        elif self.input_tokens + projected > self.limits.max_input_tokens:
            reason = f"max_input_tokens ({self.limits.max_input_tokens}) reached"
        if reason:
            event.cancel = reason
            self.limit_hit = reason
            self._cancelled_call = True
            self._record(type="note", name="limit", text=reason)
            return
        self.model_calls += 1
        self._model_started = (utc_now(), time.monotonic())

    def after_model(self, event: AfterModelCallEvent) -> None:
        if self._cancelled_call:  # Strands fires After for a cancelled call; it is not a model call
            self._cancelled_call = False
            return
        started_at, t0 = self._model_started or (utc_now(), time.monotonic())
        duration_ms = round((time.monotonic() - t0) * 1000, 1)
        if event.exception is not None or event.stop_response is None:
            exc = event.exception
            self._record(type="llm_call", name="model", started_at=started_at, duration_ms=duration_ms,
                                status="error", error=f"{type(exc).__name__}: {exc}"[:ERROR_CHARS])
            return
        message = event.stop_response.message
        usage = (message.get("metadata") or {}).get("usage") or {}
        tokens_in, tokens_out, cached = total_input_tokens(usage), usage.get("outputTokens"), cached_tokens(usage)
        if tokens_in:
            self.input_tokens += tokens_in
            self.last_tokens_in = tokens_in
        text = " ".join(b["text"] for b in message.get("content", []) if "text" in b)
        tool_uses = [b["toolUse"]["name"] for b in message.get("content", []) if "toolUse" in b]
        if not text and tool_uses:
            text = "(calls " + ", ".join(tool_uses) + ")"
        extra = {"tokens_in_cached": cached} if cached else {}
        self._record(type="llm_call", name="model", started_at=started_at, duration_ms=duration_ms,
                            tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=self.cost(tokens_in, tokens_out, cached),
                            text=text[:PREVIEW_CHARS], **extra)

    def cost(self, tokens_in: int | None, tokens_out: int | None, cached: int = 0) -> float | None:
        price = self.prices.get(self.model_id)
        if price is None or tokens_in is None or tokens_out is None:
            return None
        return round(price.cost(tokens_in, tokens_out, cached), 6)

    # --- tool calls --------------------------------------------------------------------------------

    def before_tool(self, event: BeforeToolCallEvent) -> None:
        use = event.tool_use
        if self.timed_out:
            event.cancel_tool = "run timed out"
            return
        pending = _Pending(utc_now(), time.monotonic())
        if event.selected_tool is None:
            pending.invalid = True
        else:
            schema = self.tool_schema.get(use["name"])
            if schema:
                validator = validators.validator_for(schema)(schema)
                err = next(iter(validator.iter_errors(use.get("input") or {})), None)
                if err is not None:
                    pending.invalid = True
                    event.cancel_tool = f"Invalid arguments for {use['name']}: {err.message}"
        self._tools[use["toolUseId"]] = pending

    def after_tool(self, event: AfterToolCallEvent) -> None:
        use = event.tool_use
        name, args = use["name"], use.get("input") or {}
        pending = self._tools.pop(use["toolUseId"], None) or _Pending(utc_now(), time.monotonic())
        server = self.tool_server.get(name)
        result = event.result or {}
        text = "".join(c.get("text", "") for c in result.get("content", []) if isinstance(c, dict))

        error = None
        if self.timed_out and (event.cancel_message or event.exception is not None or result.get("status") == "error"):
            # On cancel, Strands 1.62 interrupts the MCP call and reports it as an exception
            # ("Tool execution cancelled locally; ...") rather than via cancel_message.
            error = "cancelled: run timed out"
        elif event.exception is not None:
            error = f"{event.exception}"
        elif result.get("status") == "error":
            error = text or "tool returned an error"
        elif server and self.error_detection.get(server) == "json_status" and _json_status_error(text):
            error = text

        duration = getattr(event, "duration", None)
        duration_ms = round(duration * 1000, 1) if duration is not None else round(
            (time.monotonic() - pending.t0) * 1000, 1)
        key = (server, name, json.dumps(args, sort_keys=True, default=str))
        fields: dict[str, Any] = dict(
            type="tool_call", name=name, server=server, args=args, started_at=pending.started_at,
            duration_ms=duration_ms, status="error" if error else "ok", result_preview=text[:PREVIEW_CHARS] or None,
        )
        if error:
            fields["error"] = error[:ERROR_CHARS]
        if pending.invalid:
            fields["invalid"] = True
        if key in self._errored:
            fields["retry_of"] = self._errored[key]
        step = self._record(**fields)
        if step is not None and error:
            self._errored[key] = step.step

        cap = self.limits.tool_result_max_chars
        if len(text) > cap:  # keep the agent's context (and cost) bounded
            # Rebuild the result rather than patching it: MCP results also carry structuredContent.
            event.result = {"toolUseId": result.get("toolUseId", use["toolUseId"]), "status": result.get("status", "success"),
                            "content": [{"text": text[:cap] + f"\n[truncated {len(text) - cap} chars]"}]}


def total_input_tokens(usage: dict) -> int | None:
    """All input tokens, cached or not. Some models (e.g. Kimi K3 on Bedrock) cache automatically, and then
    inputTokens counts only the uncached part, with the rest in cacheRead/WriteInputTokens (observed 2026-10-30)."""
    if usage.get("inputTokens") is None:
        return None
    return usage["inputTokens"] + cached_tokens(usage)


def cached_tokens(usage: dict) -> int:
    return (usage.get("cacheReadInputTokens") or 0) + (usage.get("cacheWriteInputTokens") or 0)


def _json_status_error(text: str) -> bool:
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(obj, dict) and obj.get("status") == "error"
