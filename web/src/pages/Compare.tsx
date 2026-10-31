import { useMemo } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Empty, ErrorBanner, PageHeader, Panel, Section, Skeleton } from "../components/ui";
import { deltaTone, pretty, seconds, shortModel, statusWord, stepKind } from "../format";
import type { Compare as CompareT, RunsResponse } from "../types";
import { usePoll } from "../usePoll";

const TONE = { good: "text-emerald-600", bad: "text-rose-600", none: "text-slate-400" };

export default function Compare() {
  const [params] = useSearchParams();
  const a = params.get("a") ?? "", b = params.get("b") ?? "";
  const navigate = useNavigate();
  const runs = usePoll<RunsResponse>("/api/runs", () => null);
  const cmp = usePoll<CompareT>(a && b ? `/api/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}` : null, () => null);
  const options = useMemo(() => (runs.data?.runs ?? []).filter((r) => !r.unreadable), [runs.data]);
  const pick = (side: "a" | "b", v: string) => navigate(`/compare?a=${encodeURIComponent(side === "a" ? v : a)}&b=${encodeURIComponent(side === "b" ? v : b)}`);

  return (
    <main className="space-y-8 px-4 py-6 sm:px-8">
      <PageHeader title="Compare two runs" caption="Pick two runs to see their scores, cost and steps side by side." />
      <div className="grid gap-3 sm:grid-cols-2">
        {(["a", "b"] as const).map((side) => (
          <label key={side} className="field">Run {side.toUpperCase()}{side === "a" && <span className="font-normal text-slate-400"> · the baseline</span>}
            <select className="input" value={side === "a" ? a : b} onChange={(e) => pick(side, e.target.value)}>
              <option value="">Choose a run…</option>
              {options.map((r) => <option key={r.run_id} value={r.run_id}>{r.prompt_id} · {shortModel(r.model)} · {statusWord(r.headline)} · {r.when}</option>)}
            </select>
          </label>
        ))}
      </div>
      {!a || !b ? <Empty title="Pick two runs to compare" /> : cmp.error ? <ErrorBanner message={cmp.error} /> : !cmp.data ? <Skeleton className="h-64" /> : (<>
        <Section n={1} title="Scores and cost" caption="B minus A. Green means B did better, red means worse.">
          <Panel className="overflow-x-auto p-2">
            <table className="table">
              <thead><tr><th></th><th>A · <Link className="text-sky-600" to={`/runs/${encodeURIComponent(a)}`}>{cmp.data.a.label}</Link></th>
                <th>B · <Link className="text-sky-600" to={`/runs/${encodeURIComponent(b)}`}>{cmp.data.b.label}</Link></th><th>Difference</th></tr></thead>
              <tbody>{cmp.data.rows.map((r, i) => (
                <tr key={i}><td className={r.indent ? "pl-8 text-slate-500" : "font-medium"}>{pretty(r.label)}</td>
                  <td className="tabular-nums">{r.a}</td><td className="tabular-nums">{r.b}</td>
                  <td className={`tabular-nums ${TONE[deltaTone(r.delta, r.better)]}`}>{r.delta}</td></tr>
              ))}</tbody>
            </table>
          </Panel>
        </Section>
        <Section n={2} title="Steps side by side" caption="Every step each run took, in order.">
          <div className="grid gap-4 xl:grid-cols-2">
            {(["a", "b"] as const).map((side) => {
              const s = cmp.data![side];
              const hasAgents = Object.keys(s.agent_of).length > 0;
              return (
                <Panel key={side} className="overflow-x-auto p-4">
                  <h3 className="mb-2 text-sm font-semibold">{side.toUpperCase()} · {s.label} · {s.run.prompt_id}</h3>
                  <table className="table text-xs">
                    <thead><tr><th>Step</th>{hasAgents && <th>Agent</th>}<th>Kind</th><th>Name</th><th>Status</th><th>Time</th></tr></thead>
                    <tbody>{s.steps.map((st) => (
                      <tr key={st.step}><td className="font-mono">{st.step}</td>{hasAgents && <td>{s.agent_of[String(st.step)] ?? ""}</td>}
                        <td>{stepKind(st.type)}</td><td>{st.server ? `${st.server}/` : ""}{st.name}</td>
                        <td className={st.status === "error" ? "text-rose-600" : ""}>{st.status === "error" ? "✗ error" : st.status}</td>
                        <td className="whitespace-nowrap tabular-nums">{seconds(st.duration_ms)}</td></tr>
                    ))}</tbody>
                  </table>
                </Panel>
              );
            })}
          </div>
        </Section>
      </>)}
    </main>
  );
}
