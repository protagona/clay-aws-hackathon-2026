// Increment 13: a prompt's expected tool steps - an ordered list of steps, each "any of" one or more tools.
import { useEffect, useId, useState } from "react";

export function StepEditor({ stages, tools, onSave, error }: {
  stages: string[][]; tools: string[]; onSave: (stages: string[][]) => Promise<void>; error: string | null;
}) {
  const [steps, setSteps] = useState<string[][]>(stages);
  const [draft, setDraft] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState(false);
  const list = useId();
  // Reset only when the saved steps really change: the parent passes a fresh array on every render, and resetting on
  // identity would wipe unsaved edits (verify finding).
  const saved = JSON.stringify(stages);
  useEffect(() => setSteps(JSON.parse(saved)), [saved]);
  const dirty = JSON.stringify(steps) !== saved;
  const set = (i: number, s: string[]) => setSteps(steps.map((x, j) => (j === i ? s : x)));
  const move = (i: number, d: -1 | 1) => {
    const next = [...steps];
    [next[i], next[i + d]] = [next[i + d], next[i]];
    setSteps(next);
  };
  const add = (i: number) => {
    const t = (draft[i] ?? "").trim();
    if (t && !steps[i].includes(t)) set(i, [...steps[i], t]);
    setDraft({ ...draft, [i]: "" });
  };

  return (
    <div className="space-y-3">
      <datalist id={list}>{tools.map((t) => <option key={t} value={t} />)}</datalist>
      {steps.length === 0 && <p className="text-sm text-slate-500">No steps: this prompt's workflow score has no sequence check.</p>}
      <ol className="space-y-2">
        {steps.map((step, i) => (
          <li key={i} className="flex flex-wrap items-center gap-2 rounded-xl p-3 ring-1 ring-slate-200 dark:ring-slate-800">
            <span className="w-14 text-sm font-medium">Step {i + 1}</span>
            <span className="text-xs text-slate-500">any of:</span>
            {step.map((t) => (
              <span key={t} className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-0.5 font-mono text-xs dark:bg-slate-800">
                {t}<button className="text-slate-500 hover:text-rose-600" aria-label={`Remove ${t} from step ${i + 1}`} onClick={() => set(i, step.filter((x) => x !== t))}>×</button>
              </span>
            ))}
            <span className="inline-flex items-center gap-1">
              <input list={list} aria-label={`Add a tool to step ${i + 1}`} placeholder="+ tool" value={draft[i] ?? ""}
                className="w-48 max-w-full rounded-full bg-white px-3 py-1 font-mono text-xs ring-1 ring-slate-300 dark:bg-slate-900 dark:ring-slate-700"
                onChange={(e) => setDraft({ ...draft, [i]: e.target.value })}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(i); } }} />
              <button className="btn-ghost text-xs" onClick={() => add(i)} disabled={!(draft[i] ?? "").trim()}>Add</button>
            </span>
            <span className="ml-auto flex gap-1">
              <button className="btn-ghost" aria-label={`Move step ${i + 1} up`} disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
              <button className="btn-ghost" aria-label={`Move step ${i + 1} down`} disabled={i === steps.length - 1} onClick={() => move(i, 1)}>↓</button>
              <button className="btn-ghost" aria-label={`Remove step ${i + 1}`} onClick={() => setSteps(steps.filter((_, j) => j !== i))}>✕</button>
            </span>
          </li>
        ))}
      </ol>
      {error && <p role="alert" className="text-sm text-rose-600">✗ {error}</p>}
      <div className="flex flex-wrap items-center gap-3">
        <button className="btn-ghost" onClick={() => setSteps([...steps, []])} disabled={steps.length >= 12}>+ Add step</button>
        <span className="ml-auto text-xs text-amber-600">{dirty && "Unsaved changes"}</span>
        <button className="btn-primary" disabled={!dirty || busy}
          onClick={async () => { setBusy(true); try { await onSave(steps); } finally { setBusy(false); } }}>{busy ? "Saving…" : "Save steps"}</button>
      </div>
    </div>
  );
}
