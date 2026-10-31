import { Link, useParams } from "react-router-dom";
import { RubricView } from "../components/RubricView";
import { ErrorBanner, Pill, Skeleton } from "../components/ui";
import type { Rubric } from "../types";
import { usePoll } from "../usePoll";

/** A history version (/rubric/history/:file), the active rubric (/rubric/active), or a run's copy (/rubric/runs/:id). */
export default function RubricVersion({ kind = "history" }: { kind?: "history" | "active" | "run" }) {
  const { file = "", id = "" } = useParams();
  const url = kind === "active" ? "/api/rubric/active?task=adb"
    : kind === "run" ? `/api/runs/${encodeURIComponent(id)}/rubric`
    : `/api/rubric/history/${encodeURIComponent(file)}?task=adb`;
  const q = usePoll<{ rubric: Rubric; active: boolean; sha256?: string }>(url, () => null);
  if (q.error) return <main className="p-8"><ErrorBanner message={q.error} /></main>;
  if (!q.data) return <main className="p-8"><Skeleton className="h-64" /></main>;
  const r = q.data.rubric;
  return (
    <main className="space-y-4 px-4 py-6 sm:px-8">
      <div className="flex flex-wrap items-center gap-3">
        <Link to={kind === "run" ? `/runs/${encodeURIComponent(id)}` : "/rubric"} className="text-sm text-sky-600">← {kind === "run" ? "Run" : "Rubric"}</Link>
        <h1 className="text-lg font-semibold">{r.rubric_id} v{r.version}</h1>
        {q.data.active && <Pill tone="emerald">✓ active</Pill>}
        {kind === "run" && <span className="text-xs text-slate-500">the exact copy that scored run {id}</span>}
        {q.data.sha256 && <span className="font-mono text-xs text-slate-400">sha256 {q.data.sha256.slice(0, 12)}…</span>}
      </div>
      <RubricView rubric={r} />
    </main>
  );
}
