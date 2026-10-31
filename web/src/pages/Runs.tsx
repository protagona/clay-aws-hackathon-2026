import React, { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, post } from "../api";
import { Board } from "../components/Board";
import { AgentBars, Gauges, StepFeed, Waterfall } from "../components/run";
import { useToast } from "../components/toasts";
import { Empty, ErrorBanner, Kpi, PageHeader, Pill, ScoreRing, Section, Skeleton, Spinner, SubSection, Tabs } from "../components/ui";
import { clock, money, shortModel, statusWord } from "../format";
import { RubricView } from "../components/RubricView";
import type { Card, Criterion, Kpis, RubricWithMeta, RunDetail, RunsResponse } from "../types";
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
      toast({ tone: "info", text: `Scoring ${runId}…` });
      board.reload();
      setEvalTick((n) => n + 1);  // the open drawer re-polls at once and shows "Evaluating…"
    } catch (e) {
      toast({ tone: "bad", text: e instanceof ApiError ? e.message : String(e) });
    }
  }

  const filtered = !!(model || prompt || q);
  const k = useMemo(() => board.data && (filtered ? totals(runs) : board.data.kpis), [board.data, filtered, runs]);
  const all = board.data?.kpis.runs ?? 0;
  return (
    <main className={`space-y-10 px-4 py-6 sm:px-8 ${id ? "min-[900px]:mr-[540px]" : ""}`}>
      <PageHeader title="Runs" caption="Every agent run, grouped by where it is. Click a run to see its details." />
      {board.error && <ErrorBanner message={`Reconnecting… (${board.error})`} onRetry={board.reload} />}
      <Section n={1} title="At a glance" caption="Counts and totals for the runs shown below.">
        <div role="group" aria-label="Key numbers" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {k ? (<>
            <Kpi label="Runs" value={k.runs} sub={<>{filtered && `of ${all} · `}{k.today ? <span className="text-emerald-600">+{k.today} today</span> : "none today"}</>} />
            <Kpi label="Average score" value={k.mean === null ? "—" : k.mean.toFixed(2)} sub={`${k.scored} scored · active rubric`} />
            <Kpi label="In progress" value={k.live} accent sub={`${k.running} running · ${k.evaluating} being scored`} />
            <Kpi label="Spent" value={money(k.spent, 2)} sub="agent runs + judging" />
          </>) : [0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-24" />)}
        </div>
      </Section>
      <Section n={2} title="Runs by status" caption="Filter to narrow the board and the totals above."
        actions={board.data && board.data.live > 0 && <span className="flex items-center gap-2 text-xs text-sky-600"><Spinner label="Live" /> live · updating every 2 s</span>}>
        <div className="mb-4 flex flex-wrap items-center gap-2 text-sm">
          <label className="sr-only" htmlFor="f-model">Model</label>
          <select id="f-model" className="chip" value={model} onChange={(e) => setModel(e.target.value)}>
            <option value="">All models</option>{models.map((m) => <option key={m} value={m}>{shortModel(m)}</option>)}
          </select>
          <label className="sr-only" htmlFor="f-prompt">Prompt</label>
          <select id="f-prompt" className="chip" value={prompt} onChange={(e) => setPrompt(e.target.value)}>
            <option value="">All prompts</option>{prompts.map((p) => <option key={p}>{p}</option>)}
          </select>
          <label className="sr-only" htmlFor="f-q">Search runs</label>
          <input id="f-q" className="chip w-56 max-w-full" placeholder="Search runs…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        {board.loading && !board.data ? <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-40" />)}</div>
          : board.data && board.data.runs.length === 0 ? <Empty title="No runs yet">Click <strong>▶ New run</strong> to start one, or run <code>foundry run --config config/offline.toml --prompt adb-001</code>.</Empty>
          : <Board runs={runs} selected={id} onEvaluate={evaluate} />}
      </Section>
      {id && <RunDrawer id={id} onEvaluate={evaluate} refresh={evalTick} />}
    </main>
  );
}

/** The KPIs of just the cards the filters show (increment 11), from each card's share as the server counts it. */
export function totals(cards: Card[]): Kpis {
  const today = new Date().toISOString().slice(0, 10);  // UTC, like the server
  const scored = cards.map((c) => c.kpi?.overall).filter((v): v is number => typeof v === "number");
  const running = cards.filter((c) => c.column === "running").length;
  const evaluating = cards.filter((c) => c.column === "evaluating").length;
  return {
    runs: cards.length, today: cards.filter((c) => (c.started_at ?? "").startsWith(today)).length,
    scored: scored.length, mean: scored.length ? scored.reduce((a, b) => a + b, 0) / scored.length : null,
    spent: cards.reduce((a, c) => a + (c.kpi?.spent ?? 0), 0), live: running + evaluating, running, evaluating,
  };
}

type Tab = "overview" | "timeline" | "steps" | "report" | "rubric";

/** Increment 10: the rubric that scored this run (its own copy), or the active one if it isn't scored yet. */
function RunRubric({ id, scored, criteria }: { id: string; scored: boolean; criteria?: Criterion[] }) {
  const own = usePoll<RubricWithMeta>(scored ? `/api/runs/${encodeURIComponent(id)}/rubric` : null, () => null);
  const active = usePoll<RubricWithMeta>(scored ? null : "/api/rubric/active?task=adb", () => null);
  const data = scored ? own.data : active.data;
  const error = scored ? own.error : active.error;
  if (error) return <ErrorBanner message={error} />;
  if (!data) return <Skeleton className="h-48" />;
  return (
    <div className="space-y-3">
      <p data-caption className="text-xs text-slate-500">{scored
        ? <>Scored with <strong>{data.rubric.rubric_id} v{data.rubric.version}</strong> (the copy saved with this run). <Link className="text-sky-600" to={`/rubric/runs/${encodeURIComponent(id)}`}>Open full page</Link></>
        : <>Not scored yet: this run will be scored with the active rubric, <strong>{data.rubric.rubric_id} v{data.rubric.version}</strong>.</>}</p>
      <RubricView rubric={data.rubric} scores={scored ? criteria ?? null : null} />
    </div>
  );
}

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
            <h2 className="mt-0.5 line-clamp-2 font-semibold" data-content>{d?.run.prompt ?? id}</h2>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {d && <Pill tone={tone}>{status === "running" && <span className="h-1.5 w-1.5 rounded-full bg-sky-500" aria-hidden="true" />}{statusWord(status)}</Pill>}
            <span className="font-mono text-sm tabular-nums">{d?.m.start_ms ? clock(d.m.start_ms) : d?.m.elapsed}</span>
            <Link to={`/compare?a=${encodeURIComponent(id)}`} className="btn-ghost sm:hidden" aria-label="Compare with another run">⇄</Link>
            <Link to="/" className="btn-ghost" aria-label="Close run details">✕</Link>
          </div>
        </div>
        <div className="mt-4 flex items-center">
          <Tabs<Tab> value={tab} onChange={setTab} tabs={[
            { id: "overview", label: "Overview" }, { id: "timeline", label: "Timeline" },
            { id: "steps", label: <>Steps <span className="font-mono text-xs">{d?.m.feed.length ?? ""}</span></> },
            { id: "report", label: "Report" }, { id: "rubric", label: "Rubric" }]} />
          <Link className="ml-auto hidden pb-2 text-sm text-slate-500 hover:text-sky-600 sm:inline" to={`/compare?a=${encodeURIComponent(id)}`}>Compare ↗</Link>
        </div>
      </div>
      <div className="space-y-5 p-6">
        {run.error && !d && <ErrorBanner message={run.error === "Run not found" ? "Run not found" : run.error} onRetry={run.reload} />}
        {!d && !run.error && <Skeleton className="h-64" />}
        {d && tab === "overview" && (<div className="space-y-7">
          <SubSection title="Scores" caption="How the judge graded this run, from 0 to 1.">
            <RunScores d={d} id={id} onEvaluate={onEvaluate} />
          </SubSection>
          <SubSection title="Limits used" caption="How much of each budget the run used."><Gauges m={d.m} /></SubSection>
          {d.m.rows.some((r) => r.kind === "agent") && <SubSection title="Agents" caption="Time each agent took, in order."><AgentBars m={d.m} /></SubSection>}
          <SubSection title="Latest steps" caption="The last things the agent did. All steps are on the Steps tab."><StepFeed m={d.m} limit={4} /></SubSection>
        </div>)}
        {d && tab === "timeline" && <><TabCaption>Each bar is one step, placed by when it ran. Longer bars took longer.</TabCaption><Waterfall m={d.m} /></>}
        {d && tab === "steps" && <><TabCaption>Every step in order: what the model said and which tools it called.</TabCaption><StepFeed m={d.m} /></>}
        {d && tab === "rubric" && <RunRubric id={id} scored={!!d.scores} criteria={d.scores?.output.criteria ?? undefined} />}
        {d && tab === "report" && (d.report_html
          ? <><TabCaption>The judge's written report on this run.</TabCaption><article className="report" dangerouslySetInnerHTML={{ __html: d.report_html }} /></>
          : <Empty title={d.evaluating ? "Being scored…" : d.interrupted ? "Scoring was interrupted" : "Not scored yet"}>
              {d.evaluating ? <Spinner label="Being scored" /> : d.run.status !== "running" &&
                <button className="btn-primary mt-2" onClick={() => onEvaluate(id)}>Score this run</button>}
            </Empty>)}
      </div>
    </aside>
  );
}

const TabCaption = ({ children }: { children: React.ReactNode }) => <p data-caption className="text-xs text-slate-500 dark:text-slate-400">{children}</p>;

/** The Overview's Scores block, in order of precedence: being scored, interrupted, scored, running, not scored. */
function RunScores({ d, id, onEvaluate }: { d: RunDetail; id: string; onEvaluate: (id: string) => void }) {
  if (d.evaluating) return <p className="flex items-center gap-2 text-sm text-amber-600"><Spinner label="Being scored" /> Being scored…</p>;
  const button = <button className="btn-primary" onClick={() => onEvaluate(id)}>Score this run</button>;
  if (d.interrupted) return <div className="flex flex-wrap items-center gap-3 text-sm"><span>Scoring was interrupted.</span>{button}</div>;
  if (d.scores) return (
    <div className="flex flex-wrap items-center gap-5">
      {([["Overall", d.scores.overall], ["Workflow", d.scores.workflow.score], ["Output", d.scores.output.score]] as const).map(([label, v]) => (
        <div key={label} className="text-center"><ScoreRing value={v} size={64} label={label.toLowerCase()} /><div className="mt-1 text-xs text-slate-500">{label}</div></div>))}
      <div className="text-xs text-slate-500">
        {d.scores.overall === null && <div className="mb-1 font-medium text-amber-600">Partly scored: some criteria couldn't be scored.</div>}
        Workflow: how the agent worked.<br />Output: how good its plan is.
        {d.rubric_info && <><br />Rubric {d.rubric_info.id} v{d.rubric_info.version}</>}
      </div>
    </div>
  );
  if (d.run.status === "running") return <p className="text-sm text-slate-500">Scores appear after the run finishes and is graded.</p>;
  return <div className="flex flex-wrap items-center gap-3 text-sm"><span>Not scored yet.</span>{button}</div>;
}
