"""The trace contract (docs/trace-contract.md) as pydantic models, plus the run-directory validator.

Models allow extra fields: the contract is additive-only, so a reader must accept fields it doesn't know.
Only the fields marked required in the contract doc are required here; evaluation relies on nothing else,
which is what lets it score run directories written by other harnesses.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

CONTRACT_VERSION = "0.2"

RunStatus = Literal["running", "succeeded", "failed", "timeout"]
StepType = Literal["llm_call", "tool_call", "agent_step", "note"]
StepStatus = Literal["ok", "error"]


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class ModelRef(_Open):
    provider: str
    id: str
    params: dict[str, Any] = Field(default_factory=dict)


class HarnessRef(_Open):
    name: str
    version: str | None = None


class McpServerRef(_Open):
    name: str
    package: str | None = None
    version: str | None = None
    error_detection: str | None = None


class InputRef(_Open):
    kind: str
    uri: str


class Limits(_Open):
    max_model_calls: int | None = None
    max_input_tokens: int | None = None
    max_seconds: float | None = None


class RunRecord(_Open):
    run_id: str
    task_id: str
    prompt_id: str
    model: ModelRef
    status: RunStatus
    started_at: str
    mode: Literal["live", "replay"] = "live"
    contract_version: str | None = None
    task_version: str | None = None
    prompt: str | None = None
    harness: HarnessRef | None = None
    tracer: str | None = None
    mcp_servers: list[McpServerRef] = Field(default_factory=list)
    inputs: list[InputRef] = Field(default_factory=list)
    rubric_id: str | None = None
    reference_ids: list[str] = Field(default_factory=list)
    ended_at: str | None = None
    limits: Limits | None = None
    config: str | None = None


class Step(_Open):
    step: int
    type: StepType
    name: str
    started_at: str
    duration_ms: float
    status: StepStatus
    parent: int | None = None
    # llm_call
    tokens_in: int | None = None
    tokens_out: int | None = None
    tokens_in_cached: int | None = None  # the part of tokens_in served from the model's prompt cache
    cost_usd: float | None = None
    text: str | None = None
    # tool_call
    server: str | None = None
    args: dict[str, Any] | None = None
    result_preview: str | None = None
    result_uri: str | None = None
    retry_of: int | None = None
    invalid: bool | None = None
    error: str | None = None

    @model_validator(mode="after")
    def _error_needs_message(self) -> Step:
        if self.status == "error" and not self.error:
            raise ValueError("status 'error' requires a non-empty 'error'")
        return self


class Artifact(_Open):
    uri: str
    description: str | None = None


class Output(_Open):
    kind: str
    text: str = ""
    artifacts: list[Artifact] = Field(default_factory=list)


class Criterion(_Open):
    id: str
    name: str
    description: str
    max: int = Field(ge=2)
    weight: float = Field(default=1.0, gt=0)
    levels: dict[str, str]

    @model_validator(mode="after")
    def _levels_cover_ends(self) -> Criterion:
        missing = [lvl for lvl in ("1", str(self.max)) if lvl not in self.levels]
        if missing:
            raise ValueError(f"levels must describe {', '.join(missing)}")
        return self


class Rubric(_Open):
    rubric_id: str
    task_id: str
    version: str
    description: str = ""
    criteria: list[Criterion] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> Rubric:
        ids = [c.id for c in self.criteria]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate criterion id(s): {', '.join(dupes)}")
        return self


@dataclass(frozen=True)
class Violation:
    file: str
    line: int | None
    field: str
    message: str
    severity: Literal["error", "warning"] = "error"

    def __str__(self) -> str:
        loc = f"{self.file}:{self.line}" if self.line is not None else self.file
        prefix = "warning: " if self.severity == "warning" else ""
        return f"{loc}: {self.field}: {prefix}{self.message}"


def _pydantic_violations(file: str, line: int | None, exc: ValidationError) -> list[Violation]:
    out = []
    for err in exc.errors():
        field = ".".join(str(p) for p in err["loc"]) or "(object)"
        out.append(Violation(file, line, field, err["msg"]))
    return out


def _load_json_object(path: Path) -> tuple[dict | None, list[Violation]]:
    name = path.name
    if not path.exists():
        return None, [Violation(name, None, "(file)", "missing")]
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return None, [Violation(name, exc.lineno, "(file)", f"not valid JSON: {exc.msg}")]
    if not isinstance(data, dict):
        return None, [Violation(name, 1, "(file)", "must be a JSON object")]
    return data, []


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_run_dir(run_dir: str | Path) -> list[Violation]:
    """Check a run directory against the contract. Errors fail validation; warnings don't."""
    run_dir = Path(run_dir)
    violations: list[Violation] = []
    for name in ("run.json", "trace.jsonl", "output.json", "scores.json", "rubric.json", "report.md"):
        if (run_dir / name).is_symlink():  # never follow a planted link out of the run dir
            violations.append(Violation(name, None, "(file)", "is a symlink; run files must be regular files"))
    if violations:
        return violations

    run_data, errs = _load_json_object(run_dir / "run.json")
    violations += errs
    status = None
    if run_data is not None:
        try:
            status = RunRecord.model_validate(run_data).status
        except ValidationError as exc:
            violations += _pydantic_violations("run.json", 1, exc)
            status = run_data.get("status")

    trace = run_dir / "trace.jsonl"
    if not trace.exists():
        violations.append(Violation("trace.jsonl", None, "(file)", "missing"))
    else:
        lines = trace.read_text().splitlines()
        last_step = 0
        for lineno, raw in enumerate(lines, start=1):
            if not raw.strip():
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as exc:
                if lineno == len(lines) and status == "running":
                    violations.append(Violation("trace.jsonl", lineno, "(line)",
                                                "truncated final line (run still 'running'; likely crashed mid-write)",
                                                "warning"))
                else:
                    violations.append(Violation("trace.jsonl", lineno, "(line)", f"not valid JSON: {exc.msg}"))
                continue
            try:
                step = Step.model_validate(obj)
            except ValidationError as exc:
                violations += _pydantic_violations("trace.jsonl", lineno, exc)
                continue
            if step.step <= last_step:
                violations.append(Violation("trace.jsonl", lineno, "step",
                                            f"step numbers must increase (got {step.step} after {last_step})"))
            last_step = max(last_step, step.step)

    out_path = run_dir / "output.json"
    if out_path.exists() or status != "running":
        out_data, errs = _load_json_object(out_path)
        violations += errs
        if out_data is not None:
            try:
                Output.model_validate(out_data)
            except ValidationError as exc:
                violations += _pydantic_violations("output.json", 1, exc)

    violations += _evaluation_consistency(run_dir)
    return violations


def _evaluation_consistency(run_dir: Path) -> list[Violation]:
    """An interrupted evaluation leaves hashes that don't match; that's a warning, never an error."""
    scores_path = run_dir / "scores.json"
    if not scores_path.exists():
        return []
    try:
        scores = json.loads(scores_path.read_text())
    except json.JSONDecodeError:
        return [Violation("scores.json", None, "(file)",
                          "unreadable; evaluation was interrupted, re-run foundry evaluate", "warning")]
    out = []
    for field, name in (("rubric_sha256", "rubric.json"), ("report_sha256", "report.md")):
        expected = scores.get(field)
        path = run_dir / name
        if expected and (not path.exists() or sha256_file(path) != expected):
            out.append(Violation("scores.json", None, field,
                                 f"does not match {name}; evaluation was interrupted, re-run foundry evaluate",
                                 "warning"))
    return out


def evaluation_interrupted(run_dir: str | Path) -> bool:
    return bool(_evaluation_consistency(Path(run_dir)))
