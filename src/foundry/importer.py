"""Turn OpenTelemetry GenAI spans from any agent into run directories that `foundry evaluate` can score.

Input is OTLP: JSON (one ExportTraceServiceRequest, or JSON Lines of them, which is the Collector file exporter's
format) or protobuf (what `foundry receive` gets over HTTP). Each `invoke_agent` span is one run. Its GenAI
descendants become steps: `chat`-like spans -> llm_call, `execute_tool` -> tool_call, nested `invoke_agent` ->
agent_step. Everything else (event-loop cycles, MCP transport spans) is ignored.

Attribute styles handled, both observed from strands-agents 1.62.0 on 2026-10-30: the default (gen_ai.choice /
gen_ai.tool.message events, legacy prompt/completion token names) and gen_ai_latest_experimental
(gen_ai.input/output.messages, gen_ai.tool.call.arguments/result). See docs/eval-sandbox/increment-3.md.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from foundry.contract import CONTRACT_VERSION, HarnessRef, ModelRef, Output, RunRecord, Step
from foundry.runstore import RunDir

MAX_FILE_BYTES = 50 * 1024 * 1024
PREVIEW = 1000
TEXT = 1000
LLM_OPS = {"chat", "text_completion", "generate_content"}
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class ImportError_(Exception):  # noqa: N801 (avoid shadowing the builtin ImportError)
    """The input can't be imported; the message says why (CLI exit 2)."""


@dataclass
class Span:
    trace_id: str
    span_id: str
    parent_id: str
    name: str
    start_ns: int
    end_ns: int
    status_code: str
    status_message: str
    attrs: dict[str, Any]
    events: list[tuple[str, dict[str, Any]]]
    children: list[Span] = field(default_factory=list)

    @property
    def op(self) -> str:
        return str(self.attrs.get("gen_ai.operation.name") or self.name.split(" ")[0])

    def event_attr(self, key: str) -> Any:
        for _, ev in self.events:
            if key in ev:
                return ev[key]
        return None

    def event(self, name: str) -> dict[str, Any] | None:
        for n, ev in self.events:
            if n == name:
                return ev
        return None


# --- parsing --------------------------------------------------------------------------------------------

def _value(v: dict) -> Any:
    """OTLP AnyValue. In OTLP/JSON, intValue is a string (protobuf JSON mapping for int64)."""
    if "stringValue" in v:
        return v["stringValue"]
    if "intValue" in v:
        return int(v["intValue"])
    if "doubleValue" in v:
        return float(v["doubleValue"])
    if "boolValue" in v:
        return bool(v["boolValue"])
    if "arrayValue" in v:
        return [_value(x) for x in v["arrayValue"].get("values", [])]
    if "kvlistValue" in v:
        return {kv["key"]: _value(kv.get("value", {})) for kv in v["kvlistValue"].get("values", [])}
    return None


def _hex_id(value: Any) -> str:
    """OTLP/JSON spells trace/span ids in hex; protobuf->JSON converters (MessageToDict) emit base64. Accept both."""
    text = str(value or "")
    if re.fullmatch(r"[0-9a-fA-F]*", text):
        return text.lower()
    try:
        return base64.b64decode(text, validate=True).hex()
    except (binascii.Error, ValueError):
        return re.sub(r"[^A-Za-z0-9]", "", text)  # not an id we recognize: keep only letters and digits


def _attrs(items: list[dict]) -> dict[str, Any]:
    return {a["key"]: _value(a.get("value", {})) for a in items or [] if "key" in a}


def spans_from_request(req: dict) -> list[Span]:
    if not isinstance(req, dict) or "resourceSpans" not in req:
        raise ImportError_('not an OTLP trace export: expected an object with "resourceSpans"')
    out = []
    for rs in req.get("resourceSpans") or []:
        for ss in rs.get("scopeSpans") or rs.get("instrumentationLibrarySpans") or []:
            for s in ss.get("spans") or []:
                status = s.get("status") or {}
                out.append(Span(
                    trace_id=_hex_id(s.get("traceId")), span_id=_hex_id(s.get("spanId")),
                    parent_id=_hex_id(s.get("parentSpanId")), name=str(s.get("name", "")),
                    start_ns=int(s.get("startTimeUnixNano", 0)), end_ns=int(s.get("endTimeUnixNano", 0)),
                    status_code=str(status.get("code", "STATUS_CODE_UNSET")), status_message=str(status.get("message", "")),
                    attrs=_attrs(s.get("attributes")),
                    events=[(str(e.get("name", "")), _attrs(e.get("attributes"))) for e in s.get("events") or []],
                ))
    return out


def spans_from_protobuf(body: bytes) -> list[Span]:
    from google.protobuf.json_format import MessageToDict
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    try:
        req = ExportTraceServiceRequest.FromString(body)
    except Exception as exc:  # protobuf raises DecodeError and friends
        raise ImportError_(f"not an OTLP protobuf trace export: {type(exc).__name__}") from None
    return spans_from_request(MessageToDict(req))


def spans_from_file(path: Path) -> list[Span]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ImportError_(f"{path}: {exc.strerror}") from None
    if size > MAX_FILE_BYTES:
        raise ImportError_(f"{path}: {size:,} bytes is over the {MAX_FILE_BYTES:,}-byte limit")
    text = path.read_text(errors="replace")
    try:
        docs = [json.loads(text)]
    except json.JSONDecodeError:
        docs = []
        for n, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                docs.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ImportError_(f"{path}:{n}: not JSON or JSON Lines ({exc.msg})") from None
    return [s for d in docs for s in spans_from_request(d)]


# --- mapping --------------------------------------------------------------------------------------------

def _iso(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _json(text: Any) -> Any:
    if not isinstance(text, str):
        return text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _text_of(value: Any) -> tuple[str, list[str]]:
    """Text and tool-call names from any message shape seen in GenAI spans."""
    value = _json(value)
    texts, tools = [], []

    def walk(v):
        if isinstance(v, str):
            texts.append(v)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, dict):
            if v.get("type") == "tool_call" or "toolUse" in v:
                tools.append(str(v.get("name") or v.get("toolUse", {}).get("name") or "?"))
            elif v.get("type") == "text":
                texts.append(str(v.get("content", "")))
            elif "text" in v and isinstance(v["text"], str):
                texts.append(v["text"])
            elif "parts" in v:
                walk(v["parts"])
            elif "content" in v and v.get("role") != "tool":
                walk(v["content"])

    walk(value)
    return "".join(texts).strip(), tools


def _first_user_text(root: Span) -> str | None:
    msg = root.event("gen_ai.user.message")
    if msg is not None:
        return _text_of(msg.get("content"))[0] or None
    msgs = _json(root.event_attr("gen_ai.input.messages") or root.attrs.get("gen_ai.input.messages"))
    if isinstance(msgs, list):
        for m in msgs:
            if isinstance(m, dict) and m.get("role") == "user":
                return _text_of(m)[0] or None
    return None


def _output(span: Span) -> tuple[str, list[str]]:
    choice = span.event("gen_ai.choice")
    if choice is not None and "message" in choice:
        return _text_of(choice["message"])
    out = span.event_attr("gen_ai.output.messages") or span.attrs.get("gen_ai.output.messages")
    return _text_of(out) if out is not None else ("", [])


def _tokens(a: dict) -> tuple[int | None, int | None, int]:
    tin = a.get("gen_ai.usage.input_tokens", a.get("gen_ai.usage.prompt_tokens"))
    tout = a.get("gen_ai.usage.output_tokens", a.get("gen_ai.usage.completion_tokens"))
    cached = (a.get("gen_ai.usage.cache_read.input_tokens") or a.get("gen_ai.usage.cache_read_input_tokens") or 0) + \
             (a.get("gen_ai.usage.cache_creation.input_tokens") or a.get("gen_ai.usage.cache_write_input_tokens") or 0)
    if tin is not None and cached and "foundry.step" not in a:  # our tracer already includes cache in input
        tin += cached
    return (int(tin) if tin is not None else None), (int(tout) if tout is not None else None), int(cached)


def _error(span: Span) -> str | None:
    if span.status_code in ("STATUS_CODE_ERROR", "2", 2):
        msg = span.status_message
        exc = span.event("exception")
        if not msg and exc:
            msg = f"{exc.get('exception.type', 'Exception')}: {exc.get('exception.message', '')}"
        return (msg or "span status ERROR")[:500]
    if span.attrs.get("gen_ai.tool.status") == "error":
        return (_output(span)[0] or "tool reported error")[:500]
    return None


def _descendants(root: Span) -> list[Span]:
    out, stack = [], list(root.children)
    while stack:
        s = stack.pop()
        out.append(s)
        if s.op != "invoke_agent":  # a nested agent is one agent_step; its insides are not flattened in
            stack.extend(s.children)
    # Ties on start time happen with coarse timestamps: prefer a recorded step number, then whichever ended first.
    return sorted(out, key=lambda s: (s.start_ns, int(s.attrs.get("foundry.step") or 0), s.end_ns))


def steps_for(root: Span, prices: dict | None, model_id: str) -> list[dict]:
    steps: list[dict] = []
    errored: dict[tuple, int] = {}
    for s in _descendants(root):
        base = {"started_at": _iso(s.start_ns), "duration_ms": round((s.end_ns - s.start_ns) / 1e6, 1)}
        err = _error(s)
        if s.op in LLM_OPS:
            tin, tout, cached = _tokens(s.attrs)
            text, tools = _output(s)
            mid = str(s.attrs.get("gen_ai.response.model") or s.attrs.get("gen_ai.request.model") or model_id)
            price = (prices or {}).get(mid)
            cost = round(price.cost(tin, tout, cached), 6) if price and tin is not None and tout is not None else None
            step = {**base, "type": "llm_call", "name": "model", "tokens_in": tin, "tokens_out": tout,
                    "cost_usd": cost, "text": (text or (f"(calls {', '.join(tools)})" if tools else ""))[:TEXT]}
            if cached:
                step["tokens_in_cached"] = cached
        elif s.op == "execute_tool":
            name = str(s.attrs.get("gen_ai.tool.name") or s.name.removeprefix("execute_tool").strip() or "tool")
            args = _json(s.attrs.get("gen_ai.tool.call.arguments"))
            if args is None:
                tm = s.event("gen_ai.tool.message")
                args = _json(tm.get("content")) if tm else None
            result = s.attrs.get("gen_ai.tool.call.result")
            preview = _text_of(result)[0] if result is not None else _output(s)[0]
            step = {**base, "type": "tool_call", "name": name, "server": s.attrs.get("foundry.mcp_server"),
                    "args": args if isinstance(args, dict) else ({"value": args} if args is not None else None),
                    "result_preview": (preview or "")[:PREVIEW] or None}
            key = (name, json.dumps(step["args"], sort_keys=True, default=str))
            if key in errored:
                step["retry_of"] = errored[key]
        elif s.op == "invoke_agent":
            step = {**base, "type": "agent_step", "name": str(s.attrs.get("gen_ai.agent.name") or s.name)}
        else:
            continue
        step["status"] = "error" if err else "ok"
        if err:
            step["error"] = err
        step["step"] = len(steps) + 1
        if step["type"] == "tool_call" and err:
            errored[(step["name"], json.dumps(step["args"], sort_keys=True, default=str))] = step["step"]
        steps.append({k: v for k, v in step.items() if v is not None or k in ("tokens_in", "tokens_out", "cost_usd")})
    return steps


def agent_roots(spans: list[Span]) -> list[Span]:
    by_id = {(s.trace_id, s.span_id): s for s in spans}
    for s in spans:
        parent = by_id.get((s.trace_id, s.parent_id))
        if parent is not None:
            parent.children.append(s)
    roots = []
    for s in spans:
        if s.op != "invoke_agent":
            continue
        p, nested = by_id.get((s.trace_id, s.parent_id)), False
        while p is not None:  # an invoke_agent under another invoke_agent is a sub-agent, not a run
            if p.op == "invoke_agent":
                nested = True
                break
            p = by_id.get((p.trace_id, p.parent_id))
        if not nested:
            roots.append(s)
    return sorted(roots, key=lambda s: s.start_ns)


def write_run(root: Span, runs_dir: Path, task_id: str, prompt_id: str, source: str,
              prices: dict | None = None, run_id: str | None = None) -> Path:
    model_id = str(root.attrs.get("gen_ai.response.model") or root.attrs.get("gen_ai.request.model") or "unknown")
    harness = str(root.attrs.get("gen_ai.provider.name") or root.attrs.get("gen_ai.system") or "unknown")
    text, _ = _output(root)
    steps = steps_for(root, prices, model_id)
    if not text:  # fall back to the last model call's text output
        for s in reversed([d for d in _descendants(root) if d.op in LLM_OPS]):
            text, tools = _output(s)
            if text and not tools:
                break
    status = "failed" if _error(root) else ("succeeded" if text else "failed")
    rid = _SAFE.sub("-", run_id or f"otel-{prompt_id}-{root.trace_id[:12]}-{root.span_id[:6]}").strip("-")[:120]
    rd = RunDir.create(runs_dir, rid)
    record = RunRecord(
        contract_version=CONTRACT_VERSION, run_id=rid, task_id=task_id, prompt_id=prompt_id,
        prompt=_first_user_text(root), model=ModelRef(provider=harness, id=model_id, params={}),
        harness=HarnessRef(name=harness, version=None), tracer="otel-import", mode="live", status=status,
        started_at=_iso(root.start_ns), ended_at=_iso(root.end_ns), mcp_servers=[],
        rubric_id=None, source={"format": "otlp", "file": source, "trace_id": root.trace_id,
                                "span_id": root.span_id, "agent": root.attrs.get("gen_ai.agent.name")},
    )
    rd.write_run(record)
    rd.touch_trace()
    for s in steps:
        rd.append_step(Step.model_validate(s))
    rd.write_output(Output(kind="text", text=text) if text else Output(kind="none", text=""))
    return rd.path


def import_spans(spans: list[Span], runs_dir: Path, task_id: str, prompt_id: str, source: str,
                 prices: dict | None = None) -> list[Path]:
    roots = agent_roots(spans)
    if not roots:
        raise ImportError_("no agent runs found: expected at least one span with gen_ai.operation.name = invoke_agent")
    return [write_run(r, runs_dir, task_id, prompt_id, source, prices) for r in roots]
