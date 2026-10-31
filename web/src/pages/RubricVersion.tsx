import { Link, useParams } from "react-router-dom";
import { ErrorBanner, Panel, Pill, Skeleton } from "../components/ui";
import type { Rubric } from "../types";
import { usePoll } from "../usePoll";

export default function RubricVersion() {
  const { file = "" } = useParams();
  const q = usePoll<{ rubric: Rubric; active: boolean }>(`/api/rubric/history/${encodeURIComponent(file)}?task=adb`, () => null);
  if (q.error) return <main className="p-8"><ErrorBanner message={q.error} /></main>;
  if (!q.data) return <main className="p-8"><Skeleton className="h-64" /></main>;
  const r = q.data.rubric;
  return (
    <main className="space-y-4 px-4 py-6 sm:px-8">
      <div className="flex flex-wrap items-center gap-3"><Link to="/rubric" className="text-sm text-sky-600">← Rubric</Link>
        <h1 className="text-lg font-semibold">{r.rubric_id} v{r.version}</h1>{q.data.active && <Pill tone="emerald">✓ active</Pill>}</div>
      <p className="text-sm text-slate-600 dark:text-slate-300">{r.description}</p>
      <div className="grid gap-4 md:grid-cols-2">{r.criteria.map((c) => (
        <Panel key={c.id} className="p-5">
          <div className="flex items-baseline justify-between gap-2"><h2 className="font-semibold">{c.name}</h2><span className="font-mono text-xs text-slate-500">{c.id} · 1–{c.max} · ×{c.weight}</span></div>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-300">{c.description}</p>
          <ol className="mt-3 space-y-1 text-sm">{Object.entries(c.levels).sort(([a], [b]) => Number(a) - Number(b)).map(([k, v]) => (
            <li key={k} className="flex gap-2"><span className="w-5 shrink-0 font-mono text-slate-400">{k}</span><span>{v}</span></li>))}</ol>
        </Panel>))}</div>
    </main>
  );
}
