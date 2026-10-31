import { Link, useParams } from "react-router-dom";
import { RubricView } from "../components/RubricView";
import { ErrorBanner, PageHeader, Pill, Section, Skeleton } from "../components/ui";
import type { RubricVersion as RubricVersionT, RubricWithMeta } from "../types";
import { usePoll } from "../usePoll";

/** A history version (/rubric/history/:file), the active rubric (/rubric/active), or a run's copy (/rubric/runs/:id). */
export default function RubricVersion({ kind = "history" }: { kind?: "history" | "active" | "run" }) {
  const { file = "", id = "" } = useParams();
  const url = kind === "active" ? "/api/rubric/active?task=adb"
    : kind === "run" ? `/api/runs/${encodeURIComponent(id)}/rubric`
    : `/api/rubric/history/${encodeURIComponent(file)}?task=adb`;
  const q = usePoll<RubricWithMeta | RubricVersionT>(url, () => null);
  const caption = kind === "active" ? "The rubric used to score new runs." : kind === "run" ? `The exact rubric that scored run ${id}.` : "An earlier version of the rubric.";
  const back = <Link to={kind === "run" ? `/runs/${encodeURIComponent(id)}` : "/rubric"} className="text-sm text-sky-600">← {kind === "run" ? "Run" : "Rubric"}</Link>;
  if (q.error) return <main className="space-y-6 px-4 py-6 sm:px-8"><PageHeader title="Rubric" caption={caption}>{back}</PageHeader><ErrorBanner message={q.error} onRetry={q.reload} /></main>;
  if (!q.data) return <main className="space-y-6 px-4 py-6 sm:px-8"><PageHeader title="Rubric" caption={caption}>{back}</PageHeader><Skeleton className="h-64" /></main>;
  const r = q.data.rubric;
  return (
    <main className="space-y-8 px-4 py-6 sm:px-8">
      <PageHeader title={<span className="break-all">{r.rubric_id} v{r.version}</span>} caption={<span className="break-all">{caption}</span>}>
        {q.data.active && <Pill tone="emerald">✓ active</Pill>}
        {"sha256" in q.data && <span className="font-mono text-xs text-slate-400">sha256 {q.data.sha256.slice(0, 12)}…</span>}
        {back}
      </PageHeader>
      <Section title="Criteria" caption="Each criterion is scored on its own scale. The levels say what each score means.">
        <RubricView rubric={r} />
      </Section>
    </main>
  );
}
