import { Link } from "react-router-dom";
import { money, shortModel, statusWord } from "../format";
import type { Card, Column } from "../types";
import { Pill, ScoreRing } from "./ui";

export const COLUMNS: { id: Column; title: string; caption: string; tone: string }[] = [
  { id: "running", title: "Running", caption: "The agent is working now.", tone: "text-sky-600" },
  { id: "evaluating", title: "Being scored", caption: "The judge is grading the result.", tone: "text-amber-600" },
  { id: "scored", title: "Scored", caption: "Finished and graded. Ring = overall score.", tone: "text-slate-500" },
  { id: "attention", title: "Needs attention", caption: "Failed, or not scored yet.", tone: "text-rose-600" },
];

function RunCard({ c, selected, onEvaluate }: { c: Card; selected: boolean; onEvaluate: (id: string) => void }) {
  const ring = selected ? "ring-2 ring-sky-500" : c.column === "running" ? "ring-2 ring-sky-400 glow"
    : c.column === "evaluating" ? "ring-1 ring-amber-300 dark:ring-amber-500/40"
    : c.column === "attention" ? "ring-1 ring-rose-200 dark:ring-rose-500/30" : "ring-1 ring-slate-200 dark:ring-slate-800";
  return (
    <li className={`card-in rounded-2xl bg-white shadow-sm transition hover:-translate-y-0.5 hover:shadow-md dark:bg-slate-900 ${ring}`}>
      <Link to={`/runs/${encodeURIComponent(c.run_id)}`} className="block p-4 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 rounded-2xl" aria-label={`${c.prompt_id} on ${c.model}, ${statusWord(c.headline)}`}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="font-semibold">{c.prompt_id}</div>
            <div className="truncate text-xs text-slate-500" title={c.model}>{shortModel(c.model)}</div>
          </div>
          {c.column === "scored" ? <ScoreRing value={c.overall} label="overall" />
            : c.column === "running" ? <Pill tone="sky"><span className="h-1.5 w-1.5 rounded-full bg-sky-500" aria-hidden="true" />running</Pill>
            : c.column === "evaluating" ? <Pill tone="amber">scoring…</Pill>
            : <Pill tone={c.status === "failed" || c.status === "timeout" || c.status === "interrupted" || c.unreadable ? "rose" : "slate"}>{c.unreadable ? "unreadable" : statusWord(c.status === "succeeded" ? c.headline : c.status)}</Pill>}
        </div>
        {c.column === "running" && c.agents_mode === "pipeline" && (
          <div className="mt-3 text-[11px] text-slate-500">{(c.agents_started ?? []).join(" → ") || "starting…"}</div>
        )}
        {c.column === "running" && <div className="working mt-3 h-1.5 rounded-full" aria-hidden="true" />}
        {c.column === "evaluating" && <div className="mt-3 h-1.5 animate-pulse rounded-full bg-amber-300 dark:bg-amber-500/50" aria-hidden="true" />}
        {c.column === "attention" && c.reason && <p className="mt-2 text-xs text-rose-700 dark:text-rose-300">{c.reason}</p>}
        <div className="mt-3 flex justify-between text-xs text-slate-500">
          <span>{c.column === "scored" ? `Workflow ${c.workflow?.toFixed(2) ?? "—"} · Output ${c.output?.toFixed(2) ?? "—"}` : c.when}</span>
          <span>{c.agents_mode === "pipeline" ? "pipeline · " : ""}{money(c.cost)}</span>
        </div>
      </Link>
      {/* Only a run with no overall score can gain from scoring; a scored failure would cost the same result again. */}
      {c.column === "attention" && c.status !== "running" && !c.unreadable && c.overall == null && (
        <div className="border-t border-slate-100 px-4 py-2 dark:border-slate-800">
          <button className="text-xs font-medium text-sky-600 hover:underline" onClick={() => onEvaluate(c.run_id)}>Score it →</button>
        </div>
      )}
    </li>
  );
}

export function Board({ runs, selected, onEvaluate }: { runs: Card[]; selected?: string; onEvaluate: (id: string) => void }) {
  // With the drawer open the board has half the width: two columns instead of four.
  return (
    <div className={`grid grid-cols-1 items-start gap-4 sm:grid-cols-2 ${selected ? "2xl:grid-cols-4" : "xl:grid-cols-4"}`}>
      {COLUMNS.map((col) => {
        const cards = runs.filter((r) => r.column === col.id);
        return (
          <section key={col.id} aria-label={col.title}>
            <div className="flex items-center justify-between px-1">
              <h3 className={`text-xs font-semibold uppercase tracking-wider ${col.tone}`}>{col.title}</h3>
              <span className="font-mono text-xs text-slate-400">{cards.length}</span>
            </div>
            <p className="mb-2 px-1 text-xs text-slate-500 dark:text-slate-400">{col.caption}</p>
            {cards.length ? (
              <ul className="space-y-3">{cards.map((c) => <RunCard key={c.run_id} c={c} selected={selected === c.run_id} onEvaluate={onEvaluate} />)}</ul>
            ) : (
              <div className="rounded-2xl border border-dashed border-slate-200 p-4 text-center text-xs text-slate-400 dark:border-slate-800">None right now</div>
            )}
          </section>
        );
      })}
    </div>
  );
}
