import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, del, getSession, put } from "../api";
import { useToast } from "../components/toasts";
import { ErrorBanner, PageHeader, Panel, Pill, Section, Skeleton } from "../components/ui";
import type { DraftSaveResult, DraftView, Rubric, RubricCriterion } from "../types";
import { usePoll } from "../usePoll";

const blank = (): RubricCriterion => ({ id: "", name: "", description: "", max: 4, weight: 1, levels: {} });

/** The field path from the API ("criteria.0.levels") as [index, key], to place the message next to its field. */
export function fieldOf(field: string | null): { index: number | null; key: string | null } {
  if (!field) return { index: null, key: null };
  const m = field.match(/^criteria\.(\d+)(?:\.(\w+))?/);
  if (m) return { index: Number(m[1]), key: m[2] ?? null };
  return { index: null, key: field };
}

export function RubricForm({ body, onChange, error, field }: { body: Rubric; onChange: (b: Rubric) => void; error: string | null; field: string | null }) {
  const at = fieldOf(field);
  const set = (i: number, patch: Partial<RubricCriterion>) => onChange({ ...body, criteria: body.criteria.map((c, j) => (j === i ? { ...c, ...patch } : c)) });
  const topErr = (k: string) => error && at.index === null && at.key === k ? <p className="mt-1 text-xs text-rose-600" role="alert">✗ {error}</p> : null;
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-[2fr_1fr]">
        <label className="field">Rubric id<input className="input" value={body.rubric_id} onChange={(e) => onChange({ ...body, rubric_id: e.target.value })} />{topErr("rubric_id")}</label>
        <label className="field">Version<input className="input" value={body.version} onChange={(e) => onChange({ ...body, version: e.target.value })} />{topErr("version")}</label>
      </div>
      <label className="field">Description<textarea className="input" rows={2} value={body.description} onChange={(e) => onChange({ ...body, description: e.target.value })} /></label>
      {body.criteria.map((c, i) => {
        const err = error && at.index === i ? error : null;
        const max = Number.isFinite(c.max) && c.max >= 2 && c.max <= 20 ? c.max : 4;
        return (
          <fieldset key={i} className={`rounded-2xl p-4 ring-1 ${err ? "ring-rose-300 dark:ring-rose-500/50" : "ring-slate-200 dark:ring-slate-800"}`}>
            <legend className="px-1 text-sm font-semibold">Criterion {i + 1}</legend>
            {err && <p className="mb-2 text-xs text-rose-600" role="alert">✗ {err}</p>}
            <div className="grid gap-3 sm:grid-cols-[1fr_1.4fr_80px_90px]">
              <label className="field">Id<input className="input" value={c.id} onChange={(e) => set(i, { id: e.target.value })} /></label>
              <label className="field">Name<input className="input" value={c.name} onChange={(e) => set(i, { name: e.target.value })} /></label>
              <label className="field">Max<input className="input" type="number" min={2} max={20} value={c.max} onChange={(e) => set(i, { max: Number(e.target.value) })} /></label>
              <label className="field">Weight<input className="input" type="number" min={0.01} step="any" value={c.weight} onChange={(e) => set(i, { weight: Number(e.target.value) })} /></label>
            </div>
            <label className="field mt-3">Description<textarea className="input" rows={2} value={c.description} onChange={(e) => set(i, { description: e.target.value })} /></label>
            <div className="mt-3 space-y-2">{Array.from({ length: max }, (_, k) => String(k + 1)).map((lvl) => (
              <label key={lvl} className="field">Level {lvl}{(lvl === "1" || lvl === String(max)) && <span className="text-xs font-normal text-slate-400"> (required)</span>}
                <input className="input" value={c.levels[lvl] ?? ""} onChange={(e) => set(i, { levels: { ...c.levels, [lvl]: e.target.value } })} /></label>
            ))}</div>
            <button type="button" className="btn-danger mt-3" onClick={() => onChange({ ...body, criteria: body.criteria.filter((_, j) => j !== i) })}>Remove criterion {i + 1}</button>
          </fieldset>
        );
      })}
      <button type="button" className="btn-ghost" onClick={() => onChange({ ...body, criteria: [...body.criteria, blank()] })}>＋ Add criterion</button>
    </div>
  );
}

export default function RubricDraft() {
  const { id = "" } = useParams();
  const q = usePoll<DraftView>(`/api/rubric/drafts/${encodeURIComponent(id)}?task=adb`, () => null);
  const [body, setBody] = useState<Rubric | null>(null);
  const [status, setStatus] = useState<{ valid: boolean; error: string | null; field: string | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const navigate = useNavigate();
  useEffect(() => {
    if (q.data) {
      setBody({ ...q.data.body, criteria: q.data.body.criteria ?? [] });
      setStatus({ valid: q.data.meta.valid, error: q.data.meta.error, field: null });
    }
  }, [q.data]);

  async function save(activate: boolean) {
    if (!body) return;
    setBusy(true);
    try {
      const r = await put<DraftSaveResult>(
        `/api/rubric/drafts/${encodeURIComponent(id)}`, { task: "adb", body, activate });
      setStatus(r);
      if (r.activated) {
        toast({ tone: "ok", text: `Activated ${body.rubric_id} v${body.version}` });
        getSession(true);
        navigate("/rubric");
      } else toast({ tone: r.valid ? "ok" : "info", text: r.valid ? "Saved" : "Saved (still invalid)" });
    } catch (err) {
      if (err instanceof ApiError) setStatus({ valid: false, error: err.message, field: err.field });
      else toast({ tone: "bad", text: String(err) });
    } finally {
      setBusy(false);
    }
  }

  async function discard() {
    await del(`/api/rubric/drafts/${encodeURIComponent(id)}?task=adb`);
    toast({ tone: "info", text: "Draft discarded" });
    navigate("/rubric");
  }

  const header = (
    <PageHeader title="Review draft" caption="Check the drafted criteria against the source, fix anything wrong, then activate.">
      {status && (status.valid ? <Pill tone="emerald">✓ Valid: ready to activate</Pill> : <Pill tone="rose">✗ Invalid</Pill>)}
      <Link to="/rubric" className="text-sm text-sky-600">← Rubric</Link>
    </PageHeader>
  );
  if (q.error) return <main className="space-y-6 px-4 py-6 sm:px-8">{header}<ErrorBanner message={q.error} /></main>;
  if (!q.data || !body) return <main className="space-y-6 px-4 py-6 sm:px-8">{header}<Skeleton className="h-96" /></main>;
  const m = q.data.meta;
  return (
    <main className="space-y-8 px-4 py-6 sm:px-8">
      {header}
      <div className="grid items-start gap-10 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
      <Section n={1} title="Criteria" caption="Edit any field. Problems show next to the field.">
      <div className="min-w-0 space-y-4">
        {status && !status.valid && status.error && !status.field && <p role="alert" className="text-sm text-rose-600">✗ {status.error}</p>}
        {m.notes.length > 0 && (
          <Panel className="border-l-4 border-amber-400 p-4"><div className="text-sm font-semibold">Notes from the model</div>
            <ul className="mt-1 list-disc pl-5 text-sm">{m.notes.map((n, i) => <li key={i}>{n}</li>)}</ul></Panel>
        )}
        <Panel className="p-5"><RubricForm body={body} onChange={setBody} error={status?.valid ? null : status?.error ?? null} field={status?.field ?? null} /></Panel>
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn-ghost" disabled={busy} onClick={() => save(false)}>Save</button>
          <button className="btn-primary" disabled={busy} onClick={() => save(true)}>Approve and activate</button>
          <button className="btn-danger" onClick={discard}>Discard draft</button>
          <span className="text-xs text-slate-500">New runs are scored with the activated rubric; past runs keep theirs.</span>
        </div>
      </div>
      </Section>
      <div className="lg:sticky lg:top-24">
      <Section n={2} title="Source" caption="What the draft was made from.">
      <Panel className="p-5">
        <p className="break-words text-sm">{m.source.kind} · {m.source.label}</p>
        <p className="text-xs text-slate-500">{m.source.format} · {m.source.bytes.toLocaleString()} bytes · sha256 {m.source.sha256.slice(0, 12)}…</p>
        {q.data.source_text !== null ? <pre className="mt-3 max-h-[28rem] overflow-auto whitespace-pre-wrap break-words rounded-xl bg-slate-50 p-3 text-xs dark:bg-slate-800">{q.data.source_text}</pre>
          : <a className="mt-3 inline-block text-sm text-sky-600 underline" href={`/api/rubric/drafts/${encodeURIComponent(id)}/source?task=adb`}>Open source</a>}
        <p className="mt-3 text-xs text-slate-500">Drafted by {m.model ?? "— (already a rubric)"}{m.prompt_id && ` · prompt ${m.prompt_id}`}{m.usage && ` · ${m.usage.tokens_in} in / ${m.usage.tokens_out} out`}{m.usage?.cost_usd != null && ` · $${m.usage.cost_usd.toFixed(3)}`}</p>
        {m.raw_reply && <details className="mt-2 text-xs"><summary className="cursor-pointer text-sky-600">Raw model reply</summary><pre className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap">{m.raw_reply.slice(0, 8000)}</pre></details>}
      </Panel>
      </Section>
      </div>
      </div>
    </main>
  );
}
