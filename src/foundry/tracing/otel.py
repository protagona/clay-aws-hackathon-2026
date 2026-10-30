"""Second tracer: the same steps as OpenTelemetry GenAI spans, exported over OTLP/HTTP (Phoenix, Langfuse, ...).

Uses a private TracerProvider so Strands' own instrumentation (global provider) stays a no-op.
"""

from __future__ import annotations

import os
import sys
import threading
from datetime import datetime

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.semconv._incubating.attributes.gen_ai_attributes import (
    GEN_AI_OPERATION_NAME,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_TOOL_NAME,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
)
from opentelemetry.trace import Status, StatusCode

from foundry.contract import RunRecord, Step


def _parse_headers(env_name: str) -> dict[str, str]:
    """'k=v,k2=v2' from the named env var. The value is never stored, logged or exported."""
    raw = os.environ.get(env_name, "") if env_name else ""
    headers = {}
    for part in raw.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            headers[k.strip()] = v.strip()
    return headers


def _ns(iso: str) -> int:
    return int(datetime.strptime(iso.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z").timestamp() * 1e9)


class _WarnOnce(SpanExporter):
    def __init__(self, inner: SpanExporter, endpoint: str):
        self.inner, self.endpoint = inner, endpoint
        self.warned = False

    def warn(self, reason: str) -> None:
        if not self.warned:
            self.warned = True
            print(f"OTel export to {self.endpoint} failed: {reason}; trace.jsonl is unaffected", file=sys.stderr)

    def export(self, spans):
        try:
            result = self.inner.export(spans)
        except Exception as exc:
            self.warn(f"{type(exc).__name__}: {exc}"[:300])
            return SpanExportResult.FAILURE
        if result is not SpanExportResult.SUCCESS:
            self.warn("the endpoint did not accept the spans")
        return result

    def shutdown(self):
        self.inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


class OtelTracer:
    def __init__(self, cfg):
        self.cfg = cfg
        exporter = OTLPSpanExporter(endpoint=cfg.endpoint, headers=_parse_headers(cfg.headers_env),
                                    timeout=cfg.export_timeout_s)
        self.exporter = _WarnOnce(exporter, cfg.endpoint)
        self.provider = TracerProvider(resource=Resource.create({"service.name": cfg.service_name}))
        self.provider.add_span_processor(BatchSpanProcessor(self.exporter))
        self.tracer = self.provider.get_tracer("foundry")
        self.root = None
        self.ctx = None
        self.model_id = None

    def start_run(self, run: RunRecord) -> None:
        self.model_id = run.model.id
        self.root = self.tracer.start_span(
            "invoke_agent foundry", start_time=_ns(run.started_at),
            attributes={GEN_AI_OPERATION_NAME: "invoke_agent", GEN_AI_REQUEST_MODEL: run.model.id,
                        "foundry.run_id": run.run_id, "foundry.task_id": run.task_id,
                        "foundry.prompt_id": run.prompt_id})
        self.ctx = trace.set_span_in_context(self.root)

    def record_step(self, step: Step) -> None:
        start = _ns(step.started_at)
        attrs = {"foundry.step": step.step, "foundry.type": step.type, GEN_AI_REQUEST_MODEL: self.model_id}
        if step.type == "llm_call":
            name, op = f"chat {self.model_id}", "chat"
            if step.tokens_in is not None:
                attrs[GEN_AI_USAGE_INPUT_TOKENS] = step.tokens_in
            if step.tokens_out is not None:
                attrs[GEN_AI_USAGE_OUTPUT_TOKENS] = step.tokens_out
            if step.cost_usd is not None:
                attrs["foundry.cost_usd"] = step.cost_usd
        elif step.type == "tool_call":
            name, op = f"execute_tool {step.name}", "execute_tool"
            attrs[GEN_AI_TOOL_NAME] = step.name
            if step.server:
                attrs["foundry.mcp_server"] = step.server
            if step.retry_of is not None:
                attrs["foundry.retry_of"] = step.retry_of
            if step.invalid:
                attrs["foundry.invalid"] = True
        else:
            name, op = f"{step.type} {step.name}", step.type
        attrs[GEN_AI_OPERATION_NAME] = op
        span = self.tracer.start_span(name, context=self.ctx, start_time=start, attributes=attrs)
        if step.status == "error":
            span.set_status(Status(StatusCode.ERROR, (step.error or "")[:200]))
        span.end(end_time=start + int(step.duration_ms * 1e6))

    def end_run(self, run: RunRecord) -> None:
        if self.root is not None:
            self.root.set_attribute("foundry.status", run.status)
            if run.status != "succeeded":
                self.root.set_status(Status(StatusCode.ERROR, run.status))
            self.root.end(end_time=_ns(run.ended_at) if run.ended_at else None)

        timeout = self.cfg.export_timeout_s

        def flush():
            self.provider.force_flush(int(timeout * 1000))
            self.provider.shutdown()

        t = threading.Thread(target=flush, daemon=True)
        t.start()
        t.join(timeout)  # bounded by us, whatever the exporter's retry/backoff does
        if t.is_alive():
            self.exporter.warn(f"no response within {timeout:g}s")
