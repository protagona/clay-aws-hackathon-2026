"""Run the agent on one prompt and leave a complete run directory behind, whatever happens."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

from strands import Agent

from foundry import storage
from foundry.config import Config
from foundry.contract import CONTRACT_VERSION, HarnessRef, Limits, McpServerRef, ModelRef, Output, RunRecord
from foundry.harness.hooks import Limits as HookLimits
from foundry.harness.hooks import TraceHooks
from foundry.harness.mcp import McpServers, McpStartError, start_servers
from foundry.models.registry import build_provider
from foundry.runstore import RunDir, new_run_id, utc_now
from foundry.tasks import Prompt, system_prompt
from foundry.tracing.base import build_tracers, tracer_label

HARD_STOP_GRACE_S = 3


@dataclass
class _Outcome:
    result: object = None
    error: BaseException | None = None
    done: bool = False


def run(cfg: Config, prompt: Prompt, runs_dir: str | Path, run_id: str | None = None,
        config_path: str | None = None) -> tuple[Path, str]:
    provider = build_provider(cfg.model, prompt_id=prompt.prompt_id, max_tokens=cfg.limits.max_tokens,
                              timeout_s=cfg.limits.model_timeout_s, retries=cfg.limits.model_retries)
    model = provider.strands_model()  # fails before the run dir exists if, e.g., the script is missing

    run_dir = RunDir.create(runs_dir, run_id or new_run_id(prompt.prompt_id))
    record = RunRecord(
        contract_version=CONTRACT_VERSION, run_id=run_dir.run_id, task_id=cfg.task.id, task_version=None,
        prompt_id=prompt.prompt_id, prompt=prompt.prompt,
        model=ModelRef(provider=cfg.model.provider, id=cfg.model.id,
                       params={"max_tokens": cfg.limits.max_tokens,
                               **({"temperature": cfg.model.temperature} if cfg.model.temperature is not None else {})}),
        harness=HarnessRef(name="strands-agents", version=version("strands-agents")),
        tracer=tracer_label(cfg.tracing),
        mcp_servers=[McpServerRef(name=n, package=s.package, version=s.version, error_detection=s.error_detection)
                     for n, s in cfg.mcp.servers.items()],
        rubric_id=_rubric_id(cfg.task.id), mode="live", status="running", started_at=utc_now(),
        limits=Limits(max_model_calls=cfg.limits.max_model_calls, max_input_tokens=cfg.limits.max_input_tokens,
                      max_seconds=cfg.limits.max_seconds),
        config=str(config_path) if config_path else None,
    )
    run_dir.write_run(record)
    tracers = build_tracers(cfg.tracing, run_dir.path)
    tracers.start_run(record)
    t_start = time.monotonic()

    servers = McpServers()
    status, final_text = "failed", ""
    try:
        servers = start_servers(cfg.mcp.servers, cfg.limits.mcp_start_timeout_s, run_dir.path)
    except McpStartError as exc:
        tracers.record(type="note", name="mcp_start", text=str(exc))
    else:
        hooks = TraceHooks(
            tracers=tracers,
            limits=HookLimits(cfg.limits.max_model_calls, cfg.limits.max_input_tokens, cfg.limits.tool_result_max_chars),
            model_id=cfg.model.id, prices=cfg.prices, tool_server=servers.tool_server,
            tool_schema=servers.tool_schema,
            error_detection={n: s.error_detection for n, s in cfg.mcp.servers.items()},
        )
        agent = Agent(model=model, tools=servers.tools, system_prompt=system_prompt(cfg.task.id), hooks=[hooks],
                      callback_handler=None, retry_strategy=None)
        status, final_text = _drive(agent, prompt.prompt, hooks, tracers, cfg.limits.max_seconds, t_start)

    # Finalize: close the tracer first so a late worker thread can't append after run.json is final.
    tracers.close()
    run_dir.write_output(Output(kind="text", text=final_text) if final_text else Output(kind="none", text=""))
    record = record.model_copy(update={"status": status, "ended_at": utc_now()})
    run_dir.write_run(record)
    tracers.end_run(record)
    servers.stop(timeout=2.0)
    storage.mirror(run_dir.path, cfg.storage)
    return run_dir.path, status


def _drive(agent: Agent, prompt: str, hooks: TraceHooks, tracers, max_seconds: float,
           t_start: float) -> tuple[str, str]:
    outcome = _Outcome()

    def work():
        try:
            outcome.result = agent(prompt)
        except BaseException as exc:  # recorded below; the run dir must still be finalized
            outcome.error = exc
        finally:
            outcome.done = True

    worker = threading.Thread(target=work, name="foundry-agent", daemon=True)
    worker.start()
    worker.join(max(0.0, max_seconds - (time.monotonic() - t_start)))
    if worker.is_alive():
        hooks.timed_out = True
        tracers.record(type="note", name="limit", text=f"max_seconds ({max_seconds:g}) reached")
        agent.cancel(message="max_seconds reached")
        worker.join(HARD_STOP_GRACE_S)
        if worker.is_alive():
            tracers.record(type="note", name="limit", text="hard timeout: agent did not stop after cancel")
        return "timeout", ""

    if outcome.error is not None:
        if not any(s.type == "llm_call" and s.status == "error" for s in tracers.steps):
            tracers.record(type="note", name="agent_error", status="error",
                           error=f"{type(outcome.error).__name__}: {outcome.error}"[:500])
        return "failed", ""
    if hooks.limit_hit:
        return "failed", ""
    text = _final_text(outcome.result)
    return ("succeeded", text) if text else ("failed", "")


def _final_text(result) -> str:
    message = getattr(result, "message", None) or {}
    return "\n".join(b["text"] for b in message.get("content", []) if "text" in b).strip()


def _rubric_id(task_id: str) -> str | None:
    import json

    from foundry.tasks import rubric_path

    try:
        return json.loads(rubric_path(task_id).read_text()).get("rubric_id")
    except (OSError, ValueError):
        return None
