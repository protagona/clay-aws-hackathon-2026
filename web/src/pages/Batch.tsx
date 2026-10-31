import { Link, useParams } from "react-router-dom";
import { Empty, ErrorBanner, Panel, Pill, Skeleton } from "../components/ui";
import { money, score, shortModel } from "../format";
import type { BatchDetail } from "../types";
import { usePoll } from "../usePoll";

export default function Batch() {
  const { id = "" } = useParams();
  const b = usePoll<BatchDetail>(`/api/batches/${encodeURIComponent(id)}`, (d) => (d?.running ? 2000 : null));
  if (b.error && !b.data) return <main className="p-8"><ErrorBanner message={b.error} /></main>;
  if (!b.data) return <main className="p-8"><Skeleton className="h-64" /></main>;
  const d = b.data;
  const total = d.meta.jobs ?? d.rows.length;
  return (
    <main className="space-y-6 px-4 py-6 sm:px-8">
      <div className="flex flex-wrap items-center gap-3">
        <Link to="/batches" className="text-sm text-sky-600">← Batches</Link>
        <h1 className="font-mono text-lg font-semibold">{d.batch_id}</h1>
        <Pill tone={d.running ? "sky" : d.meta.status === "finished" ? "emerald" : "slate"}>{d.running ? "running" : d.meta.status ?? "starting"}</Pill>
        <span className="text-sm text-slate-500">{d.done}/{total} runs{d.meta.spent_usd !== undefined && ` · ${money(d.meta.spent_usd, 2)} of ${money(d.meta.max_cost, 2)}`}</span>
      </div>
      <div className="h-2 rounded-full bg-slate-100 dark:bg-slate-800"><div className={`h-2 rounded-full ${d.running ? "working" : "bg-sky-500"}`} style={{ width: `${total ? (d.done / total) * 100 : 0}%` }} /></div>
      <Panel className="overflow-x-auto p-2">
        <h2 className="px-3 pt-2 text-sm font-semibold">Per model</h2>
        <p className="px-3 text-xs text-slate-500">Overall score across each variant's scored runs (stdev needs 2 runs).</p>
        <table className="table"><thead><tr><th>Variant</th><th>Model</th><th>Succeeded</th><th>Mean</th><th>Min</th><th>Max</th><th>Stdev</th><th>Cost/run</th></tr></thead>
          <tbody>{d.variants.map((v) => (
            <tr key={v.variant}><td>{v.variant}</td><td>{shortModel(v.model)}</td><td>{v.succeeded}/{v.total}</td>
              <td className="font-semibold tabular-nums">{score(v.overall.mean)}</td><td className="tabular-nums">{score(v.overall.min)}</td>
              <td className="tabular-nums">{score(v.overall.max)}</td><td className="tabular-nums">{score(v.overall.stdev)}</td><td className="tabular-nums">{money(v.cost)}</td></tr>
          ))}</tbody></table>
      </Panel>
      <Panel className="overflow-x-auto p-2">
        <h2 className="px-3 pt-2 text-sm font-semibold">Runs</h2>
        {d.rows.length === 0 ? <div className="p-3"><Empty title="Runs appear here as they start" /></div> : (
          <table className="table"><thead><tr><th>Variant</th><th>Prompt</th><th>#</th><th>Status</th><th>Overall</th><th>Workflow</th><th>Output</th><th>Calls</th><th>Cost</th></tr></thead>
            <tbody>{d.rows.map((r, i) => (
              <tr key={i} className="row-in"><td>{r.variant}</td>
                <td>{r.link ? <Link className="text-sky-600" to={`/runs/${encodeURIComponent(r.link)}`}>{r.prompt}</Link> : r.prompt}</td>
                <td>{r.repeat}</td><td className={r.status === "failed" ? "text-rose-600" : ""}>{r.status}</td>
                <td className="tabular-nums">{r.overall || "—"}</td><td className="tabular-nums">{r.workflow || "—"}</td><td className="tabular-nums">{r.output || "—"}</td>
                <td className="tabular-nums">{r.model_calls} / {r.tool_calls}{Number(r.tool_errors) > 0 && <span className="text-rose-600"> ({r.tool_errors} ✗)</span>}</td>
                <td className="tabular-nums">{money(r.cost)}</td></tr>
            ))}</tbody></table>
        )}
      </Panel>
    </main>
  );
}
