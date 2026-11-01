"""The API contract (increment 17): every request body and response shape, as Pydantic models.

FastAPI checks each response against its model (routes.py), and scripts/gen_api_types.py turns these models into
web/src/types.ts. Two kinds of model:

    Closed  data the app builds itself: an unknown key is a bug, so it fails the response (500)
    Open    a file format that admits unknown keys (run.json, scores.json, trace steps, batch.json, summary.csv
            rows, rubrics, history.jsonl, draft meta.json): unknown keys pass through, and only the fields the web
            app reads are typed; foundry.data guarantees those types (docs/eval-sandbox/increment-17/design.md)

Field rules: required = the service always sends it; `= None` = it is sometimes absent (responses use
exclude_unset, so absent stays absent); numbers are float unless they are counts.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from foundry.contract import Step
from foundry.services.batches import CONCURRENCY, MAX_COST, REPEATS


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Open(BaseModel):
    model_config = ConfigDict(extra="allow")


Column = Literal["running", "evaluating", "scored", "attention"]


# --- small, shared ----------------------------------------------------------------------------------------------


class Ok(Closed):
    ok: Literal[True]


class Spread(Closed):
    mean: float | None
    min: float | None
    max: float | None
    stdev: float | None


# --- session ----------------------------------------------------------------------------------------------------


class RubricLabel(Closed):
    id: str
    version: str


class Session(Closed):
    csrf: str
    user: str | None = None  # the signed-in token's name (deployments with auth on)
    configs: list[str]
    prompts: list[str]
    models: list[str]
    tasks: list[str]
    rubric: RubricLabel | None
    aws_profile: str | None
    receiver: bool = True  # whether the live trace receiver can be started here (off in the cloud, I7)


# --- runs: the board --------------------------------------------------------------------------------------------


class CardKpi(Closed):
    spent: float
    overall: float | None


class Card(Closed):
    run_id: str
    prompt_id: str
    model: str
    status: str
    headline: str
    column: Column
    unreadable: bool
    kpi: CardKpi
    # Absent on unreadable cards:
    sub: str | None = None
    when: str | None = None
    footer: str | None = None
    started_at: str | None = None
    overall: float | None = None
    workflow: float | None = None
    output: float | None = None
    cost: float | None = None
    agents_mode: str | None = None
    agents_started: list[str] | None = None  # live pipeline runs: agents started so far (from the trace)


class Kpis(Closed):
    runs: int
    today: int
    mean: float | None
    scored: int
    spent: float
    live: int
    running: int
    evaluating: int


class RunsResponse(Closed):
    runs: list[Card]
    live: int
    kpis: Kpis


# --- runs: one run (run.json and scores.json are untrusted: Open, with the fields the web reads typed) -----------


class RunModel(Open):
    id: str | None = None


class RunAgents(Open):
    mode: str | None = None


class RunInfo(Open):
    run_id: str
    prompt_id: str
    status: str
    model: RunModel
    prompt: str | None = None
    task_id: str | None = None
    started_at: str | None = None
    agents: RunAgents | None = None


class Criterion(Open):
    id: str
    score: float
    max: float | None = None
    name: str | None = None
    rationale: str | None = None


class WorkflowScores(Open):
    score: float | None


class OutputScores(Open):
    score: float | None
    criteria: list[Criterion] | None = None


class Scores(Open):
    overall: float | None
    workflow: WorkflowScores
    output: OutputScores


class RubricInfo(Closed):
    id: str
    version: str
    link: str | None


class Gauge(Closed):
    label: str
    value: str
    max: str | None
    level: Literal["ok", "warn", "bad"] | None
    pct: float | None


class Row(Closed):
    step: int
    kind: Literal["model", "tool", "agent"]
    label: str
    error: bool
    ms: float | None
    new: bool
    left: float
    width: float
    indent: bool | None = None  # model and tool rows
    calls: int | None = None  # agent rows
    secs: float | None = None
    verdict: str | None = None


class Working(Closed):
    left: float
    width: float


class AxisTick(Closed):
    pct: float
    label: str


class StepView(Step):
    """A trace step as the run feed shows it: model text also rendered to safe HTML."""

    text_html: str | None = None


class FeedItem(Closed):
    s: StepView
    new: bool
    agent: str | None
    divider: bool


class Monitor(Closed):
    live: bool
    status: str
    elapsed: str
    start_ms: int | None
    gauges: list[Gauge]
    cost: float | None
    tokens_out: str
    tools_ok: int
    tools_err: int
    servers: list[tuple[str, int]]
    rows: list[Row]
    working: Working | None
    axis: list[AxisTick]
    feed: list[FeedItem]
    seen: int


class RunDetail(Closed):
    run: RunInfo
    scores: Scores | None
    report_html: str | None
    rubric_info: RubricInfo | None
    interrupted: bool
    evaluating: bool
    m: Monitor


class StartRunResponse(Closed):
    run_id: str


# --- compare ----------------------------------------------------------------------------------------------------


class CompareSide(Closed):
    run: RunInfo
    scores: Scores | None
    steps: list[Step]
    cost: float | None
    label: str
    agent_of: dict[str, str]
    model_calls: int
    tool_calls: int
    tool_errors: int
    duration: float | None


class CompareRow(Closed):
    label: str
    a: str
    b: str
    delta: str
    indent: bool
    better: Literal["higher", "lower"] | None


class Compare(Closed):
    a: CompareSide
    b: CompareSide
    rows: list[CompareRow]


# --- dashboard --------------------------------------------------------------------------------------------------


class DashKpis(Closed):
    runs: int
    scored: int
    mean: float | None
    spent: float


class LeaderRow(Closed):
    model: str
    runs: int
    scored: int
    succeeded: int
    overall: Spread
    cost: float


class Dot(Closed):
    run_id: str
    model: str
    prompt: str
    cost: float
    overall: float
    series: str
    shape: str
    x: float
    y: float


class LegendItem(Closed):
    label: str
    series: str
    shape: str


class XTick(Closed):
    x: float
    label: str


class YTick(Closed):
    y: float
    label: str


class Scatter(Closed):
    dots: list[Dot]
    legend: list[LegendItem]
    xticks: list[XTick]
    yticks: list[YTick]
    w: float
    h: float
    x0: float
    x1: float
    y0: float
    y1: float


class Bar(Closed):
    label: str
    value: int
    pct: float


class CriterionBar(Closed):
    label: str
    value: float
    n: int
    pct: float


class DashboardData(Closed):
    kpis: DashKpis
    leaderboard: list[LeaderRow]
    scatter: Scatter
    tool_errors: list[Bar]
    missed: list[Bar]
    criteria: list[CriterionBar]


class RubricOption(Closed):
    sha: str
    label: str
    runs: int
    active: bool


class Dashboard(Closed):
    d: DashboardData
    rubric_options: list[RubricOption]
    scored_by: dict[str, str]  # rubric sha -> a run scored with it (to open that rubric's copy)
    rubric_choice: str
    mixed: bool
    total_runs: int


# --- batches (batch.json and summary.csv are untrusted: Open) ---------------------------------------------------


class BatchSummary(Closed):
    id: str
    status: str
    jobs: int
    done: int
    spent: float
    max_cost: float | None
    models: list[str]


class BatchesResponse(Closed):
    batches: list[BatchSummary]
    running: bool


class BatchMeta(Open):
    status: str | None = None
    jobs: int | None = None
    max_cost: float | None = None
    spent_usd: float | None = None
    models: list[str] | None = None
    configs: list[str] | None = None


class BatchRow(Open):
    variant: str
    model: str
    prompt: str
    repeat: str
    status: str
    model_calls: str
    tool_calls: str
    tool_errors: str
    overall: str
    workflow: str
    output: str
    run_dir: str
    link: str | None
    cost: float | None
    overall_f: float | None


class Variant(Closed):
    variant: str
    model: str
    succeeded: int
    total: int
    overall: Spread
    cost: float | None


class BatchJob(Closed):
    id: str
    alive: bool
    exit_code: int | None


class BatchDetail(Closed):
    batch_id: str
    meta: BatchMeta
    rows: list[BatchRow]
    variants: list[Variant]
    running: bool
    done: int
    job: BatchJob | None


class StartBatchResponse(Closed):
    batch_id: str


# --- import & receiver ------------------------------------------------------------------------------------------


class ImportResponse(Closed):
    runs: list[str]


class ReceiverStatus(Closed):
    running: bool
    port: int
    endpoint: str
    count: int
    received: list[str]


# --- rubric (the project's own files, written by the rubric store; Open where the file format allows more keys) --


class RubricCriterion(Open):
    id: str
    name: str
    description: str
    max: int
    weight: float
    levels: dict[str, str]


class Rubric(Open):
    rubric_id: str
    task_id: str | None = None
    version: str
    description: str
    criteria: list[RubricCriterion]


class VersionSource(Open):
    kind: str | None = None
    label: str | None = None
    sha256: str | None = None


class RubricVersionRow(Open):
    rubric_id: str
    version: str
    sha256: str
    activated_at: str | None
    file: str
    runs: int
    active: bool
    draft_id: str | None = None
    note: str | None = None
    source: VersionSource | None = None


class DraftSource(Open):
    kind: str
    label: str
    format: str
    bytes: int
    sha256: str


class DraftUsage(Open):
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None


class DraftMeta(Open):
    draft_id: str
    created_at: str
    valid: bool
    error: str | None
    notes: list[str]
    source: DraftSource
    model: str | None
    prompt_id: str | None
    usage: DraftUsage | None = None
    raw_reply: str | None = None


class RubricOverview(Closed):
    task: str
    active: Rubric | None
    versions: list[RubricVersionRow]
    drafts: list[DraftMeta]
    active_entry: RubricVersionRow | None
    base_version: int | None = None  # send back with edits (cloud only)


class DraftView(Closed):
    task: str
    draft_id: str
    meta: DraftMeta
    body: Rubric
    source_text: str | None
    base_version: int | None = None  # send back with edits (cloud only)


class CreateDraftResponse(Closed):
    draft_id: str


class DraftSaveResult(Closed):
    valid: bool
    error: str | None
    field: str | None
    activated: bool


class RubricWithMeta(Closed):
    rubric: Rubric
    sha256: str
    active: bool
    file: str | None


class RubricVersion(Closed):
    task: str
    rubric: Rubric
    active: bool


# --- prompts and the tool menu ----------------------------------------------------------------------------------


class PromptRow(Closed):
    prompt_id: str
    prompt: str
    archived: bool
    stages: list[list[str]] | None
    runs: int
    pending: bool
    scored: int
    mean: float | None


class PromptsResponse(Closed):
    prompts: list[PromptRow]
    next_id: str
    root_name: str
    base_version: int | None = None  # send back with edits (cloud only)


class PromptRecord(Closed):
    prompt_id: str
    prompt: str
    archived: bool
    stages: list[list[str]] | None


class PromptRun(Closed):
    run_id: str
    model: str
    started_at: str | None
    overall: float | None
    cost: float | None


class PromptDetail(Closed):
    prompt: PromptRecord
    runs_total: int
    pending: bool
    next_version_id: str | None
    locked: bool
    scored: int
    mean: float | None
    runs: list[PromptRun]
    base_version: int | None = None  # send back with edits (cloud only)


class CreatePromptResponse(Closed):
    prompt_id: str


class StagesSaved(Closed):
    ok: Literal[True]
    expected_sha256: str


class ToolInfo(Closed):
    server: str | None
    name: str
    description: str | None
    runs: int


class ToolError(Closed):
    server: str
    error: str


class ToolMenu(Closed):
    tools: list[ToolInfo]
    listed_at: str | None
    errors: list[ToolError]
    refreshing: bool


# --- jobs -------------------------------------------------------------------------------------------------------


class Job(Closed):
    id: str
    kind: str
    run_id: str
    label: str
    alive: bool
    exit_code: int | None
    failed: bool
    finished_at: float | None
    message: str | None


class JobsResponse(Closed):
    jobs: list[Job]


class JobLog(Closed):
    log: str


# --- request bodies (all Closed: an unknown field is a 422) -----------------------------------------------------


class RunBody(Closed):
    prompt_id: str = Field(max_length=100)
    config: str = Field(max_length=100)


class BatchBody(Closed):
    config: str = Field(max_length=100)
    models: list[str] = Field(default_factory=list, max_length=20)
    prompts: list[str] = Field(default_factory=list, max_length=50)
    repeats: int = Field(1, ge=REPEATS[0], le=REPEATS[1])
    concurrency: int = Field(2, ge=CONCURRENCY[0], le=CONCURRENCY[1])
    evaluate: bool = False
    max_cost: float = Field(2.0, gt=0, le=MAX_COST)


class ReceiverBody(Closed):
    task: str = "adb"
    prompt_id: str = "imported"
    config: str = ""


class DraftBody(Closed):
    task: str = "adb"
    body: dict
    activate: bool = False
    base_version: int | None = None  # the project version the page loaded (aws-deploy spec S2)


class PromptBody(Closed):  # limits are checked by the store, so errors come back as 400 + field
    task: str = "adb"
    prompt_id: str | None = None
    prompt: str = ""
    base_version: int | None = None  # the project version the page loaded (aws-deploy spec S2)


class TaskBody(Closed):
    task: str = "adb"
    base_version: int | None = None  # the project version the page loaded (aws-deploy spec S2)


class ArchiveBody(Closed):
    task: str = "adb"
    archived: bool
    base_version: int | None = None  # the project version the page loaded (aws-deploy spec S2)


class StagesBody(Closed):
    task: str = "adb"
    stages: list[list[str]]
    base_version: int | None = None  # the project version the page loaded (aws-deploy spec S2)


class HistoryBody(Closed):
    task: str = "adb"
    file: str = Field(max_length=200)
    base_version: int | None = None  # the project version the page loaded (aws-deploy spec S2)


# --- sign-in (aws-deploy ADR-9) ----------------------------------------------------------------------------------


class LoginBody(Closed):
    token: str = Field(min_length=1, max_length=200)


class AuthMe(Closed):
    name: str


# --- two-step uploads (aws-deploy ADR-10) -------------------------------------------------------------------------


class UploadRequest(Closed):
    kind: Literal["trace", "rubric_source"]
    filename: str = Field(min_length=1, max_length=200)
    size: int = Field(ge=1)


class UploadTicket(Closed):
    upload_id: str
    url: str
    fields: dict[str, str]


class ImportByUpload(Closed):
    upload_id: str = Field(max_length=32)
    task: str = "adb"
    prompt_id: str = "imported"
    config: str = ""


class DraftByUpload(Closed):
    task: str = "adb"
    kind: Literal["file", "text", "url", "mcp"] = "file"
    config: str = "bedrock"
    upload_id: str | None = Field(default=None, max_length=32)
    text: str = Field(default="", max_length=200_000)
    url: str = Field(default="", max_length=2000)
    mcp: str = Field(default="", max_length=200)
    args: str = Field(default="", max_length=20_000)
