import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Empty, ErrorBanner, Kpi, Panel, Skeleton } from "../components/ui";
import { money, score, shortModel } from "../format";
import type { Dashboard as D } from "../types";
import { usePoll } from "../usePoll";

const SERIES: Record<string, string> = { s1: "fill-sky-500", s2: "fill-orange-500", s3: "fill-emerald-500", other: "fill-slate-400" };

function Mark({ shape, series, x, y }: { shape: string; series: string; x: number; y: number }) {
  const cls = `${SERIES[series] ?? SERIES.other} stroke-white dark:stroke-slate-900`;
  if (shape === "circle") return <circle className={cls} cx={x} cy={y} r={6} strokeWidth={2} />;
  if (shape === "square") return <rect className={cls} x={x - 5.5} y={y - 5.5} width={11} height={11} rx={2} strokeWidth={2} />;
  if (shape === "triangle") return <polygon className={cls} points={`${x},${y - 7} ${x + 6.5},${y + 5} ${x - 6.5},${y + 5}`} strokeWidth={2} />;
  return <polygon className={cls} points={`${x},${y - 7} ${x + 7},${y} ${x},${y + 7} ${x - 7},${y}`} strokeWidth={2} />;
}

export default function Dashboard() {
  const [params] = useSearchParams();
  const rubric = params.get("rubric") ?? "active";
  const navigate = useNavigate();
  const q = usePoll<D>(`/api/dashboard?rubric=${encodeURIComponent(rubric)}`, () => 15000);
  const [hover, setHover] = useState<string | null>(null);
  if (q.error && !q.data) return <main className="p-8"><ErrorBanner message={q.error} onRetry={q.reload} /></main>;
  if (!q.data) return <main className="space-y-4 p-8"><Skeleton className="h-24" /><Skeleton className="h-64" /></main>;
  const { d, rubric_options, mixed, total_runs } = q.data;
  const sc = d.scatter;
  const hovered = sc.dots.find((p) => p.run_id === hover);

  return (
    <main className="space-y-6 px-4 py-6 sm:px-8">
      <div className="flex flex-wrap items-end gap-3">
        <h1 className="mr-auto text-lg font-semibold">Dashboard</h1>
        <label className="field">Scored with
          <select className="input" value={rubric} onChange={(e) => navigate(`/dashboard?rubric=${encodeURIComponent(e.target.value)}`)}>
            <option value="active">Active rubric</option>
            {rubric_options.map((o) => <option key={o.sha} value={o.sha}>{o.label} ({o.runs} runs){o.active ? " · active" : ""}</option>)}
            <option value="all">All runs (any rubric)</option>
          </select>
        </label>
        {(() => {  // increment 10: open the selected rubric (even one never in the history) through a run it scored
          const run = rubric === "all" ? null : rubric === "active" ? null : q.data.scored_by?.[rubric];
          return rubric === "active" ? <Link className="pb-2 text-sm text-sky-600" to="/rubric/active">View rubric →</Link>
            : run ? <Link className="pb-2 text-sm text-sky-600" to={`/rubric/runs/${encodeURIComponent(run)}`}>View rubric →</Link> : null;
        })()}
      </div>
      {mixed && <p className="rounded-xl bg-amber-50 px-4 py-2 text-sm text-amber-800 ring-1 ring-amber-200 dark:bg-amber-500/10 dark:text-amber-200 dark:ring-amber-500/30">These runs were scored with different rubrics, so their scores aren't directly comparable.</p>}
      {total_runs === 0 ? <Empty title="No runs yet">Start one from the Runs tab.</Empty>
        : d.kpis.runs === 0 ? <Empty title="No runs scored with this rubric yet"><Link className="text-sky-600 underline" to="/dashboard?rubric=all">Show all runs</Link></Empty> : (<>
        <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Kpi label="Runs" value={d.kpis.runs} /><Kpi label="Scored" value={d.kpis.scored} />
          <Kpi label="Mean overall" value={score(d.kpis.mean)} /><Kpi label="Spent" value={money(d.kpis.spent, 2)} sub="runs + evaluations" />
        </section>
        <Panel className="p-5">
          <h2 className="font-semibold">Leaderboard</h2>
          <p className="text-xs text-slate-500">Mean overall of each model's scored runs; the whisker spans lowest to highest.</p>
          <div className="mt-3 space-y-2">{d.leaderboard.map((m) => (
            <div key={m.model} className="grid grid-cols-[minmax(120px,1.3fr)_minmax(140px,2fr)_auto] items-center gap-3 text-sm sm:grid-cols-[minmax(160px,1.3fr)_minmax(200px,2fr)_70px_70px_70px]">
              <Link to={`/?model=${encodeURIComponent(m.model)}`} className="truncate text-sky-700 hover:underline dark:text-sky-400" title={m.model}>{shortModel(m.model)}</Link>
              <div className="relative h-3 rounded-full bg-slate-100 dark:bg-slate-800" role="img" aria-label={`mean ${score(m.overall.mean)}`}>
                {m.overall.mean !== null && <div className="bar-anim absolute inset-y-0 left-0 rounded-full bg-sky-500" style={{ width: `${m.overall.mean * 100}%` }} />}
                {m.overall.min !== null && m.overall.max !== null && m.overall.max > m.overall.min && (
                  <div className="absolute top-1/2 h-0 border-t-2 border-slate-800 dark:border-slate-200" style={{ left: `${m.overall.min * 100}%`, width: `${(m.overall.max - m.overall.min) * 100}%` }} />)}
              </div>
              <span className="font-semibold tabular-nums">{score(m.overall.mean)}</span>
              <span className="hidden text-slate-500 tabular-nums sm:inline">{money(m.cost)}</span>
              <span className="hidden text-slate-500 sm:inline">{m.succeeded}/{m.runs}</span>
            </div>
          ))}</div>
        </Panel>
        <Panel className="p-5">
          <h2 className="font-semibold">Cost vs score</h2>
          <div className="mt-2 flex flex-wrap gap-4 text-xs">{sc.legend.map((l) => (
            <span key={l.label} className="flex items-center gap-1.5"><svg width="14" height="14" viewBox="-7 -7 14 14" aria-hidden="true"><Mark shape={l.shape} series={l.series} x={0} y={0} /></svg>{shortModel(l.label)}</span>
          ))}</div>
          <div className="relative mt-2 overflow-x-auto">
            <svg viewBox={`0 0 ${sc.w} ${sc.h}`} className="min-w-[600px]" role="img" aria-label={`Cost against overall score for ${sc.dots.length} runs`}>
              {sc.yticks.map((t) => <g key={t.label}><line x1={sc.x0} x2={sc.x1} y1={t.y} y2={t.y} className="stroke-slate-200 dark:stroke-slate-800" />
                <text x={sc.x0 - 8} y={t.y + 4} textAnchor="end" className="fill-slate-400 text-[11px]">{t.label}</text></g>)}
              {sc.xticks.map((t) => <text key={t.label} x={t.x} y={sc.y1 + 18} textAnchor="middle" className="fill-slate-400 text-[11px]">{t.label}</text>)}
              {sc.dots.map((p) => (
                <g key={p.run_id} className="cursor-pointer" onMouseEnter={() => setHover(p.run_id)} onMouseLeave={() => setHover(null)}
                  onClick={() => navigate(`/runs/${encodeURIComponent(p.run_id)}`)}>
                  <circle cx={p.x} cy={p.y} r={12} fill="transparent" /><Mark shape={p.shape} series={p.series} x={p.x} y={p.y} />
                </g>
              ))}
            </svg>
            {hovered && (
              <div className="pointer-events-none absolute rounded-lg bg-slate-900 px-3 py-2 text-xs text-white shadow-lg dark:bg-white dark:text-slate-900"
                style={{ left: `${(hovered.x / sc.w) * 100}%`, top: `${(hovered.y / sc.h) * 100}%`, transform: "translate(-50%, -120%)" }}>
                <div className="font-semibold">{hovered.prompt} · {shortModel(hovered.model)}</div>
                <div>overall {score(hovered.overall)} · {money(hovered.cost)}</div>
              </div>
            )}
          </div>
        </Panel>
        <Panel className="p-5">
          <h2 className="font-semibold">Where agents struggle</h2>
          <div className="mt-3 grid gap-6 md:grid-cols-3">
            {([["Tool errors by tool", d.tool_errors, "count"], ["Missed workflow stages", d.missed, "count"], ["Weakest output criteria", d.criteria, "frac"]] as const).map(([title, items, kind]) => (
              <div key={title}><h3 className="mb-2 text-sm font-medium">{title}</h3>
                {items.length === 0 ? <p className="text-xs text-slate-500">None recorded.</p> : (
                  <ol className="space-y-1.5">{items.map((it) => (
                    <li key={it.label} className="grid grid-cols-[1fr_70px_auto] items-center gap-2 text-xs">
                      <span className="break-words">{it.label}</span>
                      <span className="h-2 rounded-full bg-slate-100 dark:bg-slate-800"><span className="block h-2 rounded-full bg-sky-500" style={{ width: `${it.pct}%` }} /></span>
                      <span className="tabular-nums">{kind === "frac" ? `${Math.round(it.value * 100)}%` : it.value}</span>
                    </li>))}</ol>)}
              </div>))}
          </div>
        </Panel>
      </>)}
    </main>
  );
}
