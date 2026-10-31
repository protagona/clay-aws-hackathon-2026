// Increment 13: every prompt as a card (active, then archived); a card opens its edit page.
import { Link } from "react-router-dom";
import { Empty, ErrorBanner, PageHeader, ScoreRing, Section, Skeleton } from "../components/ui";
import type { PromptRow, PromptsResponse } from "../types";
import { usePoll } from "../usePoll";

function PromptCard({ p }: { p: PromptRow }) {
  const steps = p.stages?.length ?? 0;
  return (
    <li>
      <Link to={`/prompts/${encodeURIComponent(p.prompt_id)}`} aria-label={`${p.prompt_id}: edit`}
        className={`card-in flex h-full flex-col rounded-2xl bg-white p-4 shadow-sm ring-1 ring-slate-200 transition hover:-translate-y-0.5 hover:shadow-md dark:bg-slate-900 dark:ring-slate-800 ${p.archived ? "opacity-70" : ""}`}>
        <div className="flex items-start justify-between gap-3">
          <span className="font-semibold">{p.prompt_id}</span>
          <ScoreRing value={p.mean} label="average" />
        </div>
        <p className="mt-2 line-clamp-3 flex-1 break-words text-sm text-slate-600 dark:text-slate-300" data-content>
          {p.prompt.length > 120 ? `${p.prompt.slice(0, 120)}…` : p.prompt}
        </p>
        <div className="mt-3 flex items-center justify-between text-xs text-slate-500">
          <span>{p.runs} run{p.runs === 1 ? "" : "s"} · {steps} expected step{steps === 1 ? "" : "s"}</span>
          <span className="font-medium text-sky-600">Edit</span>
        </div>
      </Link>
    </li>
  );
}

export default function Prompts() {
  const q = usePoll<PromptsResponse>("/api/prompts?task=adb", () => null);
  const header = <PageHeader title="Prompts" caption="The questions agents are asked. Each run answers one prompt." />;
  const active = q.data?.prompts.filter((p) => !p.archived) ?? [];
  const archived = q.data?.prompts.filter((p) => p.archived) ?? [];
  const grid = "grid gap-3 sm:grid-cols-2 xl:grid-cols-3";
  return (
    <main className="space-y-10 px-4 py-6 sm:px-8">
      {header}
      {q.error && <ErrorBanner message={q.error} onRetry={q.reload} />}
      <Section n={1} title="Active prompts" caption={q.data ? `${active.length} prompt${active.length === 1 ? "" : "s"}` : "Loading…"}
        actions={<Link to="/prompts/new" className="btn-primary">+ New prompt</Link>}>
        {!q.data ? <div className={grid}>{[0, 1, 2].map((i) => <Skeleton key={i} className="h-36" />)}</div>
          : active.length === 0 ? <Empty title="No prompts yet"><Link className="text-sky-600 underline" to="/prompts/new">+ New prompt</Link></Empty>
          : <ul className={grid}>{active.map((p) => <PromptCard key={p.prompt_id} p={p} />)}</ul>}
      </Section>
      {archived.length > 0 && (
        <Section n={2} title="Archived" caption="Hidden from New run and batches. Their runs are kept.">
          <ul className={grid}>{archived.map((p) => <PromptCard key={p.prompt_id} p={p} />)}</ul>
        </Section>
      )}
    </main>
  );
}
