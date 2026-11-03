"""TOML config -> validated pydantic models. Errors name the file and the key and say what to do."""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

PROVIDERS = ("bedrock", "scripted", "offline")


class ConfigError(Exception):
    def __init__(self, file: str | Path, key: str, message: str):
        self.file, self.key, self.message = str(file), key, message
        super().__init__(f"Config {file}: {key}: {message}")


def project_root() -> Path:
    """Base for every relative path in config and for config/ and tasks/ (FOUNDRY_HOME, else cwd)."""
    return Path(os.environ.get("FOUNDRY_HOME") or os.getcwd()).resolve()


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskCfg(_Strict):
    id: str


class ModelCfg(_Strict):
    provider: Literal["bedrock", "scripted", "offline"]
    id: str
    region: str = "us-west-2"
    script: str | None = None
    temperature: float | None = None

    @model_validator(mode="after")
    def _script_for_scripted(self) -> ModelCfg:
        if self.provider == "scripted" and not self.script:
            raise ValueError("provider 'scripted' needs 'script' (path to the JSON script)")
        return self


class RoleCfg(ModelCfg):
    """Judge and summarizer: a model plus its own call settings."""

    max_tokens: int = Field(default=2048, gt=0)
    timeout_s: float = Field(default=120, gt=0)
    retries: int = Field(default=2, ge=0)


class LimitsCfg(_Strict):
    max_model_calls: int = Field(default=20, gt=0)
    max_input_tokens: int = Field(default=600_000, gt=0)
    max_seconds: float = Field(default=300, gt=0)
    max_tokens: int = Field(default=1024, gt=0)
    model_timeout_s: float = Field(default=120, gt=0)
    model_retries: int = Field(default=2, ge=0)
    mcp_start_timeout_s: int = Field(default=60, gt=0)
    tool_result_max_chars: int = Field(default=8000, gt=0)


class McpServerCfg(_Strict):
    command: str
    args: list[str] = Field(default_factory=list)
    package: str | None = None
    version: str | None = None
    error_detection: Literal["mcp", "json_status"] = "mcp"
    env: dict[str, str] = Field(default_factory=dict)

    @field_validator("args")
    @classmethod
    def _no_latest(cls, args: list[str]) -> list[str]:
        if any(a.endswith("@latest") for a in args):
            raise ValueError("pin an exact server version instead of @latest")
        return args


class McpCfg(_Strict):
    servers: dict[str, McpServerCfg] = Field(default_factory=dict)


class OtelCfg(_Strict):
    endpoint: str = "http://localhost:6006/v1/traces"
    headers_env: str = ""
    export_timeout_s: float = Field(default=10, gt=0)
    service_name: str = "foundry"


class TracingCfg(_Strict):
    tracers: list[Literal["jsonl", "otel"]] = Field(default_factory=lambda: ["jsonl"])
    otel: OtelCfg = Field(default_factory=OtelCfg)

    @field_validator("tracers")
    @classmethod
    def _jsonl_required(cls, tracers: list[str]) -> list[str]:
        if "jsonl" not in tracers:
            raise ValueError('must include "jsonl" (trace.jsonl is the record evaluation reads)')
        return tracers


class StorageCfg(_Strict):
    s3_uri: str = ""
    s3_timeout_s: float = Field(default=30, gt=0)

    @field_validator("s3_uri")
    @classmethod
    def _bucket_only(cls, uri: str) -> str:
        # "{account}" stands for the caller's AWS account (filled in when mirroring), as in the shipped configs.
        if uri and not re.fullmatch(r"s3://[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9]/?", uri.replace("{account}", "123456789012")):
            raise ValueError('must be "s3://<bucket>" with no key prefix (runs go under runs/ at the bucket root); '
                             '"{account}" in the name stands for your AWS account')
        return uri.rstrip("/")


class ScoringCfg(_Strict):
    workflow_weight: float = Field(default=0.5, ge=0, le=1)
    output_weight: float = Field(default=0.5, ge=0, le=1)

    @model_validator(mode="after")
    def _sum_to_one(self) -> ScoringCfg:
        if abs(self.workflow_weight + self.output_weight - 1) > 1e-6:
            raise ValueError("workflow_weight + output_weight must equal 1")
        return self


class ChecksCfg(_Strict):
    paths: bool = True  # path check: list each S3 location the output cites (clay-embeddings design)
    # requester-pays buckets the path check may list signed, billed to this account (requirement-gaps slice A)
    requester_pays: list[str] = []

    @field_validator("requester_pays")
    @classmethod
    def _bucket_names(cls, v: list[str]) -> list[str]:
        bad = [b for b in v if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", b)]
        if bad:
            raise ValueError(f"not S3 bucket names: {', '.join(bad)}")
        return v


class AgentsCfg(_Strict):
    mode: Literal["single", "pipeline"] = "single"  # pipeline: planner -> executor -> checker (increment 7)


class Price(_Strict):
    input: float = Field(ge=0)
    output: float = Field(ge=0)
    cache_read: float | None = Field(default=None, ge=0)  # cached input tokens; None = priced as input

    def cost(self, tokens_in: int, tokens_out: int, cached: int = 0) -> float:
        cache_rate = self.input if self.cache_read is None else self.cache_read
        return ((tokens_in - cached) * self.input + cached * cache_rate + tokens_out * self.output) / 1_000_000


class EvalConfig(_Strict):
    model_config = ConfigDict(extra="ignore")

    judge: RoleCfg
    summarizer: RoleCfg
    scoring: ScoringCfg = Field(default_factory=ScoringCfg)
    prices: dict[str, Price] = Field(default_factory=dict)
    storage: StorageCfg = Field(default_factory=StorageCfg)
    checks: ChecksCfg = Field(default_factory=ChecksCfg)
    path: str = ""


class Config(EvalConfig):
    model_config = ConfigDict(extra="forbid")

    task: TaskCfg
    model: ModelCfg
    limits: LimitsCfg = Field(default_factory=LimitsCfg)
    mcp: McpCfg = Field(default_factory=McpCfg)
    tracing: TracingCfg = Field(default_factory=TracingCfg)
    agents: AgentsCfg = Field(default_factory=AgentsCfg)


def _read(path: str | Path) -> dict:
    p = Path(path)
    if not p.is_absolute() and not p.exists():
        p = project_root() / p
    try:
        with open(p, "rb") as f:
            return _environment_overlay(tomllib.load(f))
    except FileNotFoundError:
        raise ConfigError(path, "(file)", "not found; pass an existing file such as config/offline.toml") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(path, "(file)", f"not valid TOML: {exc}") from None


def _environment_overlay(data: dict) -> dict:
    """Deployment overrides from the environment (aws-deploy design ADR-11), applied to every config read, so the
    UI, its subprocesses and a batch's children all see them:

    FOUNDRY_S3_URI           replaces [storage] s3_uri (set to "" in the cloud: the state store syncs runs instead)
    FOUNDRY_MCP_<NAME>_CMD   runs MCP server <name> from that pre-installed command instead of `uvx <package>`
                             (the cloud image installs RODA at build time, so no run downloads a package)
    """
    if "FOUNDRY_S3_URI" in os.environ and isinstance(data.get("storage", {}), dict):
        data.setdefault("storage", {})["s3_uri"] = os.environ["FOUNDRY_S3_URI"]
    servers = (data.get("mcp") or {}).get("servers") if isinstance(data.get("mcp"), dict) else None
    for name, server in (servers or {}).items():
        cmd = os.environ.get(f"FOUNDRY_MCP_{name.upper().replace('-', '_')}_CMD")
        if cmd and isinstance(server, dict) and server.get("command") == "uvx":
            server["command"], server["args"] = cmd, []
    return data


def _key(loc: tuple) -> str:
    parts = [str(p) for p in loc]
    if not parts:
        return "(file)"
    head, rest = parts[0], parts[1:]
    return f"[{head}] {'.'.join(rest)}".strip() if rest else f"[{head}]"


def _validate(model: type[BaseModel], data: dict, path: str | Path):
    try:
        return model.model_validate({**data, "path": str(path)})
    except ValidationError as exc:
        err = exc.errors()[0]
        msg = err["msg"].removeprefix("Value error, ")
        if err["type"] == "missing":
            msg = "missing (required)"
        elif err["type"] == "literal_error":
            msg = f"{msg} (got {err['input']!r})"
        raise ConfigError(path, _key(err["loc"]), msg) from None


def load_config(path: str | Path) -> Config:
    """Full config for `foundry run`; every section is validated."""
    return _validate(Config, _read(path), path)


def load_eval_config(path: str | Path) -> EvalConfig:
    """Config for `foundry evaluate`: only judge, summarizer, scoring, prices and storage are read."""
    return _validate(EvalConfig, _read(path), path)


def resolve(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else project_root() / p
