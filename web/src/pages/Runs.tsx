import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, post } from "../api";
import { Board } from "../components/Board";
import { AgentBars, Gauges, StepFeed, Waterfall } from "../components/run";
import { useToast } from "../components/toasts";
import { Empty, ErrorBanner, Kpi, Pill, ScoreRing, Skeleton, Spinner, Tabs } from "../components/ui";
import { clock, money, score, shortModel } from "../format";
import type { RunDetail, RunsResponse } from "../types";
import { boardInterval, runInterval, usePoll } from "../usePoll";

export default function Runs() {
  const { id } = useParams();
  const board = usePoll<RunsResponse>("/api/runs", (d) => boardInterval(d?.live));
  const [model, setModel] = useState("");
  const [prompt, setPrompt] = useState("");
  const [q, setQ] = useState("");
  const toast = useToast();

  const runs = useMemo(() => (board.data?.runs ?? []).filter((r) =>
    (!model || r.model === model) && (!prompt || r.prompt_id === prompt)
    && (!q || `${r.run_id} ${r.prompt_id} ${r.model}`.toLowerCase().includes(q.toLowerCase()))), [board.data, model, prompt, q]);
  const models = useMemo(() => [...new Set((board.data?.runs ?? []).map((r) => r.model))].sort(), [board.data]);
  const prompts = useMemo(() => [...new Set((board.data?.runs ?? []).map((r) => r.prompt_id))].sort(), [board.data]);

  const [evalTick, setEvalTick] = useState(0);
  async function evaluate(runId: string) {
    try {
      await post(`/api/runs/${encodeURIComponent(runId)}/evaluate`);
      toast({ tone: "info", text: `Evaluating ${runId}…` });
      board.reload();
      setEvalTick((n) => n + 1);  // the open drawer re-polls at once and shows "Evaluating…"
    } catch (e) {
      toast({ tone: "bad", text: e instanceof ApiError ? e.message : String(e) });
    }
  }

  const k = board.data?.kpis;
  return (
    <main className={`space-y-6 px-4 py-6 sm:px-8 ${id ? "min-[900px]:mr-[540px]" : ""}`}>
      {board.error && <ErrorBanner message={`Reconnecting… (${board.error})`} onRetry={board.reload} />}
      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4" aria-label="Key numbers">
        {k ? (<>
          <Kpi label="Runs" value={k.runs} sub={k.today ? <span className="text-emerald-600">+{k.today} today</span> : "none today"} />
          <Kpi label="Mean overall" value={score(k.mean)} sub={`active rubric · ${k.scored} scored`} />
          <Kpi label="Live now" value={k.live} accent sub={`${k.running} running · ${k.evaluating} evaluating`} />
          <Kpi label="Spent" value={money(k.spent, 2)} sub="runs + evaluations" />
        </>) : [0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-24" />)}
      </section>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <label className="sr-only" htmlFor="f-model">Model</label>
        <select id="f-model" className="chip" value={model} onChange={(e) => setModel(e.target.value)}>
          <option value="">All models</option>{models.map((m) => <option key={m} value={m}>{shortModel(m)}</option>)}
        </select>
        <label className="sr-only" htmlFor="f-prompt">Prompt</label>
        <select id="f-prompt" className="chip" value={prompt} onChange={(e) => setPrompt(e.target.value)}>
          <option value="">All prompts</option>{prompts.map((p) => <option key={p}>{p}</option>)}
        </select>
        <label className="sr-only" htmlFor="f-q">Search runs</label>
        <input id="f-q" className="chip w-56" placeholder="Search runs…" value={q} onChange={(e) => setQ(e.target.value)} />
        {board.data && board.data.live > 0 && <span className="ml-auto flex items-center gap-2 text-xs text-sky-600"><Spinner label="Live" /> live · updating every 2 s</span>}
      </div>
      {board.loading && !board.data ? <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-40" />)}</div>
        : board.data && board.data.runs.length === 0 ? <Empty title="No runs yet">Click <strong>▶ New run</strong> to start one, or run <code>foundry run --config config/offline.toml --prompt adb-001</code>.</Empty>
        : <Board runs={runs} selected={id} onEvaluate={evaluate} />}
      {id && <RunDrawer id={id} onEvaluate={evaluate} refresh={evalTick} />}
    </main>
  );
}

type Tab = "overview" | "timeline" | "steps" | "report";

export function RunDrawer({ id, onEvaluate, refresh = 0 }: { id: string; onEvaluate: (id: string) => void; refresh?: number }) {
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>("overview");
  const run = usePoll<RunDetail>(`/api/runs/${encodeURIComponent(id)}`, (d) => runInterval(d?.m.live));
  const reloadRun = run.reload;
  useEffect(() => { if (refresh) reloadRun(); }, [refresh, reloadRun]);
  const [, tick] = useState(0);
  useEffect(() => {
    const t = window.setInterval(() => tick((n) => n + 1), 1000);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && navigate("/");
    window.addEventListener("keydown", esc);
    return () => { window.clearInterval(t); window.removeEventListener("keydown", esc); };
  }, [navigate]);
  const d = run.data;
  const status = d ? (d.evaluating ? "evaluating" : d.run.status) : "";
  const tone = status === "running" ? "sky" : status === "evaluating" ? "amber" : status === "succeeded" ? "emerald" : "rose";

  return (
    <aside aria-label="Run details" className="drawer-in fixed inset-0 top-[6.75rem] z-20 md:top-16 overflow-auto border-l border-slate-200 bg-white shadow-2xl min-[900px]:left-auto min-[900px]:w-[540px] dark:border-slate-800 dark:bg-slate-950">
      <div className="sticky top-0 z-10 border-b border-slate-200 bg-white/95 px-6 pt-5 backdrop-blur dark:border-slate-800 dark:bg-slate-950/95">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="truncate text-xs text-slate-500">{d ? `${d.run.prompt_id} · ${d.run.model.id}${d.run.agents?.mode === "pipeline" ? " · pipeline" : ""}` : id}</div>
            <div className="mt-0.5 line-clamp-2 font-semibold">{d?.run.prompt ?? id}</div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {d && <Pill tone={tone}>{status === "running" && <span className="h-1.5 w-1.5 rounded-full bg-sky-500" aria-hidden="true" />}{status}</Pill>}
            <span className="font-mono text-sm tabular-nums">{d?.m.start_ms ? clock(d.m.start_ms) : d?.m.elapsed}</span>
            <Link to={`/compare?a=${encodeURIComponent(id)}`} className="btn-ghost sm:hidden" aria-label="Compare with another run">⇄</Link>
            <Link to="/" className="btn-ghost" aria-label="Close run details">✕</Link>
          </div>
        </div>
        <div className="mt-4 flex items-center">
          <Tabs<Tab> value={tab} onChange={setTab} tabs={[
            { id: "overview", label: "Overview" }, { id: "timeline", label: "Timeline" },
            { id: "steps", label: <>Steps <span className="font-mono text-xs">{d?.m.feed.length ?? ""}</span></> },
            { id: "report", label: "Report" }]} />
          <Link className="ml-auto hidden pb-2 text-sm text-slate-500 hover:text-sky-600 sm:inline" to={`/compare?a=${encodeURIComponent(id)}`}>Compare ↗</Link>
        </div>
      </div>
      <div className="space-y-5 p-6">
        {run.error && !d && <ErrorBanner message={run.error === "Run not found" ? "Run not found" : run.error} onRetry={run.reload} />}
        {!d && !run.error && <Skeleton className="h-64" />}
        {d && tab === "overview" && (<>
          {d.scores && (
            <div className="flex flex-wrap gap-4">
              <ScoreRing value={d.scores.overall} size={72} label="overall" />
              <ScoreRing value={d.scores.workflow.score} size={72} label="workflow" />
              <ScoreRing value={d.scores.output.score} size={72} label="output" />
              <div className="self-center text-xs text-slate-500">Overall · Workflow · Output{d.rubric_info && <><br />rubric {d.rubric_info.id} v{d.rubric_info.version}</>}</div>
            </div>
          )}
          <Gauges m={d.m} />
          {d.m.rows.some((r) => r.kind === "agent") && <div><h3 className="mb-2 text-sm font-medium">Agents</h3><AgentBars m={d.m} /></div>}
          <div><h3 className="mb-2 text-sm font-medium">Latest steps</h3><StepFeed m={d.m} limit={4} /></div>
        </>)}
        {d && tab === "timeline" && <Waterfall m={d.m} />}
        {d && tab === "steps" && <StepFeed m={d.m} />}
        {d && tab === "report" && (d.report_html
          ? <article className="report" dangerouslySetInnerHTML={{ __html: d.report_html }} />
          : <Empty title={d.evaluating ? "Evaluating…" : d.interrupted ? "Evaluation was interrupted" : "Not evaluated yet"}>
              {d.evaluating ? <Spinner label="Evaluating" /> : d.run.status !== "running" &&
                <button className="btn-primary mt-2" onClick={() => onEvaluate(id)}>Evaluate this run</button>}
            </Empty>)}
      </div>
    </aside>
  );
}
