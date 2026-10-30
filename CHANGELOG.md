# Changelog

## 0.1.0 (2026-10-30, `prototype` branch)

First working version of the agentic eval sandbox. See [the spec](docs/eval-sandbox/spec.md).

- **`foundry run`:** runs a Strands agent over MCP tools (the real RODA server) and records a contract-valid run directory. The run directory holds `run.json`, `trace.jsonl` (every model and tool call, with tokens, cost, retries, invalid calls and RODA in-body errors) and `output.json`. Runs are bounded by limits on model calls, input tokens and wall clock.
- **`foundry evaluate`:** produces a deterministic workflow score, a judge-model output score against a rubric, a narrative, and `report.md` with provenance. It works on any contract-valid run directory, which also makes it the replay path.
- **`foundry validate`:** checks a run directory against the trace contract (v0.2).
- **`foundry ui`:** a local score-card UI on 127.0.0.1 that starts runs and evaluations.
- **Providers:** `scripted` and `offline` (no credentials needed) and `bedrock`. Swapping the model is a config change.
- **Tracers:** JSONL (always on) and OpenTelemetry GenAI spans over OTLP/HTTP. Swapping the tracer is a config change.
- **Storage:** an optional S3 mirror of run directories.
- **Infra:** a CDK stack (us-west-2) with a private runs bucket and a least-privilege harness role.
