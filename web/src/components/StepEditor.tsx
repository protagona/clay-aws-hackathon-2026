// Increment 13: a prompt's expected tool steps - an ordered list of steps, each "any of" one or more tools.
import { useEffect, useState } from "react";
import type { ToolMenu } from "../types";
import { ToolPicker } from "./ToolPicker";

const EMPTY: ToolMenu = { tools: [], listed_at: null, errors: [], refreshing: false };

export function StepEditor({ stages, menu = EMPTY, onRefresh = () => {}, onSave, error }: {
  stages: string[][]; menu?: ToolMenu; onRefresh?: () => void; onSave: (stages: string[][]) => Promise<void>; error: string | null;
}) {
  const [steps, setSteps] = useState<string[][]>(stages);
  const [busy, setBusy] = useState(false);
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
  const add = (i: number, t: string) => { if (t && !steps[i].includes(t)) set(i, [...steps[i], t]); };

  return (
    <div className="space-y-3">
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
            <ToolPicker label={`Add a tool to step ${i + 1}`} tools={menu.tools ?? []} onPick={(t) => add(i, t)} refreshing={menu.refreshing} loading={menu === EMPTY}
              errors={menu.errors} listedAt={menu.listed_at} onRefresh={onRefresh} />
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
