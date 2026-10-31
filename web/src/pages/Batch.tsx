import { Link, useParams } from "react-router-dom";
import { Empty, ErrorBanner, PageHeader, Panel, Pill, Section, Skeleton } from "../components/ui";
import { money, score, score2, shortModel, statusWord } from "../format";
import type { BatchDetail } from "../types";
import { usePoll } from "../usePoll";

/** A variant's name is slug(model) for --models batches (batch.py `slug`); show it only when it says more than the model. */
const slug = (s: string) => s.replace(/[^A-Za-z0-9.-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60);

function ModelCell({ model, variant }: { model: string; variant: string }) {
  return (
    <>
      <div>{shortModel(model)}</div>
      {variant !== slug(model) && variant !== model && <div className="text-xs text-slate-500">{variant}</div>}
    </>
  );
}

export default function Batch() {
  const { id = "" } = useParams();
  const b = usePoll<BatchDetail>(`/api/batches/${encodeURIComponent(id)}`, (d) => (d?.running ? 2000 : null));
  const back = <Link to="/batches" className="text-sm text-sky-600">← All batches</Link>;
  if (b.error && !b.data) return (
    <main className="space-y-6 px-4 py-6 sm:px-8">
      <PageHeader title={<span className="break-all">Batch {id}</span>} caption="Couldn't load this batch.">{back}</PageHeader>
      <ErrorBanner message={b.error} onRetry={b.reload} />
    </main>
  );
  if (!b.data) return (
    <main className="space-y-6 px-4 py-6 sm:px-8">
      <PageHeader title={<span className="break-all">Batch {id}</span>} caption="Loading…">{back}</PageHeader>
      <Skeleton className="h-64" />
    </main>
  );
  const d = b.data;
  const total = d.meta.jobs ?? d.rows.length;
  const models = new Set(d.variants.map((v) => v.model)).size;
  const state = d.running ? "running" : d.meta.status === "finished" ? "all finished" : d.meta.status ?? "starting";
  const spent = d.meta.spent_usd !== undefined
    ? d.meta.max_cost !== undefined ? ` ${money(d.meta.spent_usd, 2)} of the ${money(d.meta.max_cost, 2)} cap spent.` : ` ${money(d.meta.spent_usd, 2)} spent.`
    : "";
  const modelOf = new Map(d.variants.map((v) => [v.variant, v.model]));
  return (
    <main className="space-y-10 px-4 py-6 sm:px-8">
      <div className="space-y-3">
        <PageHeader title={<span className="break-all">Batch {d.batch_id}</span>}
          caption={`${total} runs across ${models} model${models === 1 ? "" : "s"}, ${state}.${spent}`}>
          <Pill tone={d.running ? "sky" : d.meta.status === "finished" ? "emerald" : "slate"}>{d.running ? "running" : d.meta.status ?? "starting"}</Pill>
          {back}
        </PageHeader>
        <div className="h-2 rounded-full bg-slate-100 dark:bg-slate-800"><div className={`h-2 rounded-full ${d.running ? "working" : "bg-sky-500"}`} style={{ width: `${total ? (d.done / total) * 100 : 0}%` }} /></div>
      </div>
      <Section n={1} title="Results by model" caption="Average overall score per model. Spread needs 2+ runs.">
        <Panel className="overflow-x-auto p-2">
          <table className="table"><thead><tr><th>Model</th><th>Succeeded</th><th>Average</th><th>Lowest</th><th>Highest</th><th>Spread</th><th>Cost / run</th></tr></thead>
            <tbody>{d.variants.map((v) => (
              <tr key={v.variant}><td><ModelCell model={v.model} variant={v.variant} /></td><td>{v.succeeded}/{v.total}</td>
                <td className="font-semibold tabular-nums">{score(v.overall.mean)}</td><td className="tabular-nums">{score(v.overall.min)}</td>
                <td className="tabular-nums">{score(v.overall.max)}</td><td className="tabular-nums">{score(v.overall.stdev)}</td><td className="tabular-nums">{money(v.cost)}</td></tr>
            ))}</tbody></table>
        </Panel>
      </Section>
      <Section n={2} title="Every run" caption="One row per run. Click a prompt to open the run.">
        <Panel className="overflow-x-auto p-2">
          {d.rows.length === 0 ? <div className="p-3"><Empty title="Runs appear here as they start" /></div> : (
            <table className="table"><thead><tr><th>Model</th><th>Prompt</th><th>Repeat</th><th>Status</th><th>Overall</th><th>Workflow</th><th>Output</th><th>Model calls</th><th>Tool calls</th><th>Cost</th></tr></thead>
              <tbody>{d.rows.map((r, i) => (
                <tr key={i} className="row-in"><td><ModelCell model={modelOf.get(r.variant) ?? r.variant} variant={r.variant} /></td>
                  <td>{r.link ? <Link className="text-sky-600" to={`/runs/${encodeURIComponent(r.link)}`}>{r.prompt}</Link> : r.prompt}</td>
                  <td>{r.repeat}</td><td className={r.status === "failed" ? "text-rose-600" : ""}>{statusWord(r.status)}</td>
                  <td className="tabular-nums">{score2(r.overall)}</td><td className="tabular-nums">{score2(r.workflow)}</td><td className="tabular-nums">{score2(r.output)}</td>
                  <td className="tabular-nums">{r.model_calls}</td>
                  <td className="tabular-nums">{r.tool_calls}{Number(r.tool_errors) > 0 && <span className="text-rose-600"> ({r.tool_errors} failed)</span>}</td>
                  <td className="tabular-nums">{money(r.cost)}</td></tr>
              ))}</tbody></table>
          )}
        </Panel>
      </Section>
    </main>
  );
}
