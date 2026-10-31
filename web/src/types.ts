// Response shapes of the FastAPI JSON API (src/foundry/ui/api.py). The UI only displays these.

export type Column = "running" | "evaluating" | "scored" | "attention";

export interface Session {
  csrf: string;
  configs: string[];
  prompts: string[];
  models: string[];
  tasks: string[];
  rubric: { id: string; version: string } | null;
  aws_profile: string | null;
}

export interface Card {
  run_id: string;
  prompt_id: string;
  model: string;
  status: string;
  headline: string;
  sub?: string;
  when?: string;
  started_at?: string;
  column: Column;
  unreadable?: boolean;
  overall?: number | null;
  workflow?: number | null;
  output?: number | null;
  cost?: number | null;
  agents_mode?: string | null;
  agents_started?: string[];  // live pipeline runs: agents started so far (from the trace)
}

export interface Kpis {
  runs: number;
  today: number;
  mean: number | null;
  scored: number;
  spent: number;
  live: number;
  running: number;
  evaluating: number;
}

export interface RunsResponse {
  runs: Card[];
  live: number;
  kpis: Kpis;
}

export interface Step {
  step: number;
  type: "llm_call" | "tool_call" | "agent_step" | "note";
  name: string;
  status: string;
  duration_ms: number;
  started_at: string;
  parent?: number | null;
  server?: string | null;
  args?: Record<string, unknown> | null;
  result_preview?: string | null;
  error?: string | null;
  text?: string | null;
  text_html?: string | null;
  tokens_in?: number | null;
  tokens_out?: number | null;
  cost_usd?: number | null;
  retry_of?: number | null;
  invalid?: boolean | null;
}

export interface Gauge {
  label: string;
  value: string;
  max: string | null;
  level: "ok" | "warn" | "bad" | null;
  pct: number | null;
}

export interface Row {
  step: number;
  kind: "model" | "tool" | "agent";
  label: string;
  error: boolean;
  ms: number | null;
  new: boolean;
  left: number;
  width: number;
  indent?: boolean;
  calls?: number;
  secs?: number;
  verdict?: string | null;
}

export interface Monitor {
  live: boolean;
  status: string;
  elapsed: string;
  start_ms: number | null;
  gauges: Gauge[];
  cost: number | null;
  tokens_out: string;
  tools_ok: number;
  tools_err: number;
  servers: [string, number][];
  rows: Row[];
  working: { left: number; width: number } | null;
  axis: { pct: number; label: string }[];
  feed: { s: Step; new: boolean; agent: string | null; divider: boolean }[];
  seen: number;
}

export interface Criterion {
  id: string;
  name?: string;
  score: number;
  max: number;
  rationale?: string;
}

export interface Scores {
  overall: number | null;
  workflow: { score: number | null; components?: Record<string, number> };
  output: { score: number | null; criteria?: Criterion[] };
  rubric_id_loaded?: string;
  rubric_version?: string;
}

export interface RunDetail {
  run: { run_id: string; prompt_id: string; prompt?: string; model: { id: string }; status: string;
    task_id?: string; agents?: { mode?: string } };
  scores: Scores | null;
  report_html: string | null;
  rubric_info: { id: string; version: string; link: string | null } | null;
  evaluating: boolean;
  interrupted: boolean;
  m: Monitor;
}

export interface Job {
  id: string;
  kind: string;
  run_id: string;
  label: string;
  alive: boolean;
  exit_code: number | null;
  failed: boolean;
  message: string | null;
  finished_at: number | null;
}

export interface Spread {
  mean: number | null;
  min: number | null;
  max: number | null;
  stdev: number | null;
}

export interface Dashboard {
  d: {
    kpis: { runs: number; scored: number; mean: number | null; spent: number };
    leaderboard: { model: string; runs: number; scored: number; succeeded: number; overall: Spread; cost: number }[];
    scatter: {
      dots: { run_id: string; model: string; prompt: string; cost: number; overall: number; series: string;
        shape: string; x: number; y: number }[];
      legend: { label: string; series: string; shape: string }[];
      xticks: { x: number; label: string }[];
      yticks: { y: number; label: string }[];
      w: number; h: number; x0: number; x1: number; y0: number; y1: number;
    };
    tool_errors: { label: string; value: number; pct: number }[];
    missed: { label: string; value: number; pct: number }[];
    criteria: { label: string; value: number; n: number; pct: number }[];
  };
  rubric_options: { sha: string; label: string; runs: number; active: boolean }[];
  rubric_choice: string;
  mixed: boolean;
  total_runs: number;
}

export interface CompareSide {
  run: { run_id: string; prompt_id: string; status: string; model: { id: string } };
  scores: Scores | null;
  steps: Step[];
  label: string;
  agent_of: Record<string, string>;
}

export interface Compare {
  a: CompareSide;
  b: CompareSide;
  rows: { label: string; a: string; b: string; delta: string; indent: boolean }[];
}

export interface BatchSummary {
  id: string;
  status: string;
  jobs: number;
  done: number;
  spent: number;
  max_cost: number | null;
  models: string[];
}

export interface BatchRow {
  variant: string;
  model: string;
  prompt: string;
  repeat: string;
  status: string;
  model_calls: string;
  tool_calls: string;
  tool_errors: string;
  overall: string;
  workflow: string;
  output: string;
  link: string | null;
  cost: number | null;
}

export interface BatchDetail {
  batch_id: string;
  meta: { status?: string; jobs?: number; max_cost?: number; spent_usd?: number };
  rows: BatchRow[];
  variants: { variant: string; model: string; succeeded: number; total: number; overall: Spread; cost: number | null }[];
  running: boolean;
  done: number;
  job: { id: string; alive: boolean; exit_code: number | null } | null;
}

export interface RubricCriterion {
  id: string;
  name: string;
  description: string;
  max: number;
  weight: number;
  levels: Record<string, string>;
}

export interface Rubric {
  rubric_id: string;
  task_id?: string;
  version: string;
  description: string;
  criteria: RubricCriterion[];
}

export interface RubricVersionRow {
  rubric_id: string;
  version: string;
  sha256: string;
  activated_at: string | null;
  file: string;
  runs: number;
  active: boolean;
  note?: string;
  source: { kind: string; label: string } | null;
}

export interface DraftMeta {
  draft_id: string;
  created_at: string;
  valid: boolean;
  error: string | null;
  notes: string[];
  source: { kind: string; label: string; format: string; bytes: number; sha256: string };
  model: string | null;
  prompt_id: string | null;
  usage?: { tokens_in: number; tokens_out: number; cost_usd?: number };
  raw_reply?: string | null;
}

export interface RubricOverview {
  task: string;
  active: Rubric | null;
  versions: RubricVersionRow[];
  drafts: DraftMeta[];
}

export interface DraftView {
  task: string;
  draft_id: string;
  meta: DraftMeta;
  body: Rubric;
  source_text: string | null;
}
