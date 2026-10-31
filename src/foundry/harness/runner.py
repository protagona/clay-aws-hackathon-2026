"""Run the agent on one prompt and leave a complete run directory behind, whatever happens."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from importlib.metadata import version
from pathlib import Path

from strands import Agent

from foundry.data import storage
from foundry.config import Config
from foundry.contract import CONTRACT_VERSION, HarnessRef, Limits, McpServerRef, ModelRef, Output, RunRecord
from foundry.harness import roles
from foundry.harness.hooks import Limits as HookLimits
from foundry.harness.hooks import TraceHooks
from foundry.harness.mcp import McpServers, McpStartError, start_servers
from foundry.models.registry import build_provider
from foundry.data.runs import RunDir, new_run_id, utc_now
from foundry.data.tasks import Prompt, system_prompt
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

    pipeline = _PipelineState() if cfg.agents.mode == "pipeline" else None
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
        **({"agents": pipeline.as_json()} if pipeline else {}),  # written at start: a crashed run is still keyed
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
        current: list[Agent | None] = [None]
        task_system = system_prompt(cfg.task.id)
        if pipeline is None:
            def work() -> str:
                current[0] = _agent(model, servers.tools, task_system, hooks)
                return _final_text(current[0](prompt.prompt))
        else:
            def work() -> str:
                return _pipeline(model, servers.tools, task_system, prompt.prompt, hooks, tracers, current, pipeline)
        # Pipeline: a limit hit after the executor answered keeps that answer (user decision, 2026-10-31).
        status, final_text = _drive(work, hooks, tracers, cfg.limits.max_seconds, t_start, current,
                                    keep_answer_on_limit=pipeline is not None)

    # Finalize: close the tracer first so a late worker thread can't append after run.json is final.
    tracers.close()
    run_dir.write_output(Output(kind="text", text=final_text) if final_text else Output(kind="none", text=""))
    record = record.model_copy(update={"status": status, "ended_at": utc_now(),
                                       **({"agents": pipeline.as_json()} if pipeline else {})})
    run_dir.write_run(record)
    tracers.end_run(record)
    servers.stop(timeout=2.0)
    storage.mirror(run_dir.path, cfg.storage)
    return run_dir.path, status


def _drive(work, hooks: TraceHooks, tracers, max_seconds: float, t_start: float,
           current: list, keep_answer_on_limit: bool = False) -> tuple[str, str]:
    """Run work() (one agent, or the pipeline) on a worker thread under max_seconds; derive the run status."""
    outcome = _Outcome()

    def target():
        try:
            outcome.result = work()
        except BaseException as exc:  # recorded below; the run dir must still be finalized
            outcome.error = exc
        finally:
            outcome.done = True

    worker = threading.Thread(target=target, name="foundry-agent", daemon=True)
    worker.start()
    worker.join(max(0.0, max_seconds - (time.monotonic() - t_start)))
    if worker.is_alive():
        hooks.timed_out = True  # first, so a pipeline starts no further agent (P3)
        tracers.record(type="note", name="limit", text=f"max_seconds ({max_seconds:g}) reached")
        if current[0] is not None:
            current[0].cancel(message="max_seconds reached")
        worker.join(HARD_STOP_GRACE_S)
        if worker.is_alive():
            tracers.record(type="note", name="limit", text="hard timeout: agent did not stop after cancel")
        return "timeout", ""

    if outcome.error is not None:
        if not any(s.type == "llm_call" and s.status == "error" for s in tracers.steps):
            tracers.record(type="note", name="agent_error", status="error", parent=hooks.parent,
                           error=f"{type(outcome.error).__name__}: {outcome.error}"[:500])
        return "failed", ""
    text = outcome.result or ""
    if hooks.limit_hit and not (keep_answer_on_limit and text):
        return "failed", ""
    return ("succeeded", text) if text else ("failed", "")


def _agent(model, tools: list, system: str, hooks: TraceHooks) -> Agent:
    return Agent(model=model, tools=tools, system_prompt=system, hooks=[hooks], callback_handler=None,
                 retry_strategy=None)


@dataclass
class _PipelineState:
    roles: list[str] = field(default_factory=list)
    revisions: int = 0
    verdict: str = "none"  # the last checker's: approve | revise | unparseable | none

    def as_json(self) -> dict:
        return {"mode": "pipeline", "prompts": roles.PROMPTS_VERSION, "roles": list(self.roles),
                "revisions": self.revisions, "verdict": self.verdict}


def _pipeline(model, tools: list, task_system: str, prompt: str, hooks: TraceHooks, tracers, current: list,
              state: _PipelineState) -> str:
    """Planner -> executor -> checker, with one executor+checker revision on 'revise' (increment 7, P1-P3).
    Returns the latest executor answer; "" if the pipeline stopped before the executor answered."""

    def stopped() -> bool:
        return hooks.timed_out or hooks.limit_hit is not None

    def call(name: str, role: str, message: str, agent_tools: list) -> str | None:
        if stopped():  # never start an agent after a timeout or limit
            return None
        marker = tracers.record(type="agent_step", name=name, text=roles.preview(message))
        if marker is None:  # tracer closed: the run is being finalized
            return None
        hooks.start_agent(marker.step)
        state.roles.append(name)
        current[0] = _agent(model, agent_tools, roles.system_prompt(task_system, role), hooks)
        result = current[0](message)
        if getattr(result, "stop_reason", None) == "cancelled" or stopped():
            return None
        return _final_text(result)

    plan = call("planner", "planner", roles.user_message(prompt), [])
    if plan is None:
        return ""
    answer = call("executor", "executor", roles.user_message(prompt, plan=plan), tools)
    if not answer:  # stopped, or nothing to check: don't spend budget on a checker
        return ""
    for revision in (0, 1):
        review = call("checker" if revision == 0 else "checker (revision 1)", "checker",
                      roles.user_message(prompt, candidate=answer), tools)
        if review is None:  # this checker was cut off: the final answer is unchecked
            state.verdict = "none"
            return answer
        verdict = roles.parse_verdict(review)
        state.verdict = verdict or "unparseable"
        tracers.record(type="note", name="verdict", text=state.verdict, parent=hooks.parent)
        if verdict is None:
            tracers.record(type="note", name="verdict_parse", text="checker verdict unparseable", parent=hooks.parent)
        if verdict != "revise" or revision == 1:
            return answer
        state.revisions = 1
        revised = call("executor (revision 1)", "executor",
                       roles.user_message(prompt, candidate=answer, feedback=review), tools)
        if not revised:  # stopped, or an empty revision: keep the last non-empty answer
            return answer
        answer = revised
    return answer


def _final_text(result) -> str:
    message = getattr(result, "message", None) or {}
    return "\n".join(b["text"] for b in message.get("content", []) if "text" in b).strip()


def _rubric_id(task_id: str) -> str | None:
    import json

    from foundry.data.tasks import rubric_path

    try:
        return json.loads(rubric_path(task_id).read_text()).get("rubric_id")
    except (OSError, ValueError):
        return None
