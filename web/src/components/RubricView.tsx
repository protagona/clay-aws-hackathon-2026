// One way to show any rubric (history version, active, the one that scored a run). Text only: no HTML sinks.
import type { Criterion, Rubric } from "../types";
import { Panel, Pill } from "./ui";

export function RubricView({ rubric, scores }: { rubric: Rubric; scores?: Criterion[] | null }) {
  const byId = new Map((scores ?? []).map((c) => [c.id, c]));
  return (
    <div className="space-y-4">
      {rubric.description && <p className="text-sm text-slate-600 dark:text-slate-300">{rubric.description}</p>}
      <div className="grid gap-4 md:grid-cols-2">
        {rubric.criteria.map((c) => {
          const got = byId.get(c.id);
          const reached = got ? String(Math.round(got.score)) : null;
          return (
            <Panel key={c.id} className="p-5">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h3 className="font-semibold">{c.name}</h3>
                <span className="font-mono text-xs text-slate-500">{c.id} · 1–{c.max} · weight {c.weight}</span>
              </div>
              {got && (
                <div className="mt-2 flex items-center gap-2">
                  <Pill tone={got.score >= c.max ? "emerald" : got.score >= c.max * 0.75 ? "sky" : got.score >= c.max / 2 ? "amber" : "rose"}>
                    scored {got.score}/{got.max ?? c.max}
                  </Pill>
                </div>
              )}
              <p className="mt-2 text-sm text-slate-600 dark:text-slate-300">{c.description}</p>
              <ol className="mt-3 space-y-1 text-sm" aria-label={`${c.name} levels`}>
                {Object.entries(c.levels).sort(([a], [b]) => Number(a) - Number(b)).map(([k, v]) => {
                  const hit = reached === k;
                  return (
                    <li key={k} aria-current={hit ? "true" : undefined}
                      className={`flex gap-2 rounded-lg px-2 py-1 ${hit ? "bg-sky-50 ring-1 ring-sky-300 dark:bg-sky-500/10 dark:ring-sky-500/40" : ""}`}>
                      <span className="w-5 shrink-0 font-mono text-slate-400">{k}</span>
                      <span>{v}{hit && <span className="ml-1 text-xs font-medium text-sky-700 dark:text-sky-300">← this run</span>}</span>
                    </li>
                  );
                })}
              </ol>
              {got?.rationale && <p className="mt-3 border-t border-slate-100 pt-2 text-xs text-slate-500 dark:border-slate-800">Judge: {got.rationale}</p>}
            </Panel>
          );
        })}
      </div>
    </div>
  );
}
