// Increment 13: one prompt's page - its text (locked once it has runs), expected tool steps, runs, and archive.
// The same page at /prompts/new creates a prompt.
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, get, getSession, isStale, post, put } from "../api";
import { StepEditor } from "../components/StepEditor";
import { useToast } from "../components/toasts";
import { Empty, ErrorBanner, PageHeader, Panel, Pill, Section, Skeleton, STALE } from "../components/ui";
import { money, score, shortModel } from "../format";
import type { CreatePromptResponse, PromptDetail, PromptsResponse, ToolMenu } from "../types";
import { usePoll } from "../usePoll";

const back = <Link to="/prompts" className="text-sm text-sky-600">← All prompts</Link>;
const err = (e: unknown) => (e instanceof ApiError ? e : new ApiError(0, String(e)));

function NewPrompt() {
  const navigate = useNavigate();
  const toast = useToast();
  const [id, setId] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [base, setBase] = useState<number | null>(null);  // the project version this page loaded (cloud only)
  const load = () => get<PromptsResponse>("/api/prompts?task=adb").then((d) => { setId((cur) => cur || d.next_id); setBase(d.base_version ?? null); }).catch(() => {});
  useEffect(() => { load(); }, []);
  async function create() {
    setBusy(true);
    setError(null);
    try {
      const { prompt_id } = await post<CreatePromptResponse>("/api/prompts", { task: "adb", prompt_id: id, prompt: text, base_version: base });
      toast({ tone: "ok", text: `Created ${prompt_id}` });
      getSession(true);
      navigate(`/prompts/${encodeURIComponent(prompt_id)}`);
    } catch (e) {
      setError(err(e));
    } finally {
      setBusy(false);
    }
  }
  const at = (field: string) => error && error.field === field && <p role="alert" className="mt-1 text-xs text-rose-600">✗ {error.message}</p>;
  return (
    <main className="space-y-10 px-4 py-6 sm:px-8">
      <PageHeader title="New prompt" caption="Write the question an agent will answer. Add expected tool steps after creating it.">{back}</PageHeader>
      <Section n={1} title="Text" caption="What the agent is asked.">
        <Panel className="space-y-4 p-5">
          <label className="field">Id<input className="input max-w-xs font-mono" value={id} placeholder="Pick an id, like adb-007" onChange={(e) => setId(e.target.value)} />{at("prompt_id")}</label>
          <label className="field">Prompt<textarea className="input min-h-40" value={text} maxLength={20000} onChange={(e) => setText(e.target.value)} />{at("prompt")}</label>
          {error && !error.field && (isStale(error)
            ? <ErrorBanner message={STALE} retryLabel="Reload" onRetry={() => { setError(null); load(); }} />
            : <ErrorBanner message={error.message} />)}
          <button className="btn-primary" disabled={busy || !id.trim() || !text.trim()} onClick={create}>{busy ? "Creating…" : "Create"}</button>
        </Panel>
      </Section>
    </main>
  );
}

function EditPrompt({ id }: { id: string }) {
  const navigate = useNavigate();
  const toast = useToast();
  const q = usePoll<PromptDetail>(`/api/prompts/${encodeURIComponent(id)}?task=adb`, () => null);
  const tools = usePoll<ToolMenu>("/api/prompts/tools?task=adb", (m) => (m?.refreshing ? 2000 : null));  // poll while listing
  const [text, setText] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [stepError, setStepError] = useState<string | null>(null);
  useEffect(() => { setText(null); setError(null); setStepError(null); }, [id]);

  if (q.error && !q.data) {
    const missing = q.error.startsWith("No prompt") || q.error === "No such prompt";
    return (
      <main className="space-y-6 px-4 py-6 sm:px-8">
        <PageHeader title={missing ? "Prompt not found" : <span className="break-all">{id}</span>} caption={missing ? `No prompt has the id ${id}.` : "Couldn't load this prompt."}>{back}</PageHeader>
        {!missing && <ErrorBanner message={q.error} onRetry={q.reload} />}
      </main>
    );
  }
  if (!q.data) return <main className="space-y-6 px-4 py-6 sm:px-8"><PageHeader title={<span className="break-all">{id}</span>} caption="Loading…">{back}</PageHeader><Skeleton className="h-64" /></main>;
  const d = q.data, p = d.prompt;
  const value = text ?? p.prompt;
  const base_version = d.base_version ?? null;  // sent with every edit: a 409 if someone else changed the project
  const reload = () => { setText(null); setError(null); setStepError(null); q.reload(); };
  async function act(fn: () => Promise<unknown>, ok: string) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      toast({ tone: "ok", text: ok });
      q.reload();
    } catch (e) {
      setError(err(e));
    } finally {
      setBusy(false);
    }
  }
  const lockedWhy = d.runs_total > 0 ? `Locked: ${d.runs_total} run${d.runs_total === 1 ? "" : "s"} used this text.` : "Locked while a run is in progress.";

  return (
    <main className="space-y-10 px-4 py-6 sm:px-8">
      <PageHeader title={<span className="break-all">{p.prompt_id}</span>} caption={<span data-content>{p.prompt.length > 120 ? `${p.prompt.slice(0, 120)}…` : p.prompt}</span>}>
        {p.archived && <Pill tone="amber">archived</Pill>}
        <Pill>{d.runs_total} run{d.runs_total === 1 ? "" : "s"}{d.mean !== null && ` · avg ${score(d.mean)}`}</Pill>
        {back}
      </PageHeader>
      <Section n={1} title="Text" caption={d.locked ? `What the agent is asked. ${lockedWhy}` : "What the agent is asked."}>
        <Panel className="space-y-3 p-5">
          <textarea aria-label="Prompt text" className="input min-h-40 read-only:bg-slate-50 read-only:text-slate-700 dark:read-only:bg-slate-800/60 dark:read-only:text-slate-300" value={value}
            readOnly={d.locked} maxLength={20000} onChange={(e) => setText(e.target.value)} />
          {error && error.field === "prompt" && <p role="alert" className="text-xs text-rose-600">✗ {error.message}</p>}
          {error && error.field !== "prompt" && (isStale(error)
            ? <ErrorBanner message={STALE} retryLabel="Reload" onRetry={reload} />
            : <ErrorBanner message={error.message} />)}
          {d.locked ? (
            d.next_version_id ? <button className="btn-primary" disabled={busy} onClick={() => act(async () => {
              const { prompt_id } = await post<CreatePromptResponse>(`/api/prompts/${encodeURIComponent(id)}/version`, { task: "adb", base_version });
              getSession(true);
              navigate(`/prompts/${encodeURIComponent(prompt_id)}`);
            }, `Created ${d.next_version_id}`)}>Make a new version → {d.next_version_id}</button>
              : <p className="text-xs text-slate-500">The id is too long to version; create a new prompt instead.</p>
          ) : (
            <button className="btn-primary" disabled={busy || value === p.prompt}
              onClick={() => act(() => put(`/api/prompts/${encodeURIComponent(id)}`, { task: "adb", prompt: value, base_version }).then(() => setText(null)), `Saved ${id}`)}>
              {busy ? "Saving…" : "Save"}</button>
          )}
        </Panel>
      </Section>
      <Section n={2} title="Expected tool steps" caption="The order a good run calls tools in. Used for the workflow score's sequence check.">
        <Panel className="p-5">
          <StepEditor stages={p.stages ?? []} error={stepError}
            menu={tools.data ?? (tools.error ? { tools: [], listed_at: null, refreshing: false, errors: [{ server: "*", error: `Couldn't load the tool list: ${tools.error}` }] } : undefined)}
            onRefresh={async () => {
              try { await post("/api/prompts/tools/refresh"); } catch (e) { if (err(e).status !== 409) toast({ tone: "bad", text: err(e).message }); }
              tools.reload();
            }}
            onSave={async (stages) => {
              setStepError(null);
              try {
                await put(`/api/prompts/${encodeURIComponent(id)}/stages`, { task: "adb", stages, base_version });
                toast({ tone: "ok", text: "Steps saved" });
                q.reload();
              } catch (e) {
                if (isStale(e)) { reload(); setStepError("Someone else changed this prompt since you opened it; it has been reloaded. Make your change again."); }
                else setStepError(err(e).message);
              }
            }} />
        </Panel>
      </Section>
      <Section n={3} title="Runs of this prompt" caption="Latest first. Click to open.">
        {d.runs.length === 0 ? <Empty title="No runs yet">Start one with <strong>▶ New run</strong>.</Empty> : (
          <Panel className="p-2">
            <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">{d.runs.map((r) => (
              <li key={r.run_id}><Link to={`/runs/${encodeURIComponent(r.run_id)}`} className="grid grid-cols-[minmax(0,1fr)_3.5rem_4.5rem] items-center gap-x-4 gap-y-1 rounded-lg px-3 py-2 hover:bg-slate-50 sm:grid-cols-[minmax(0,16rem)_3.5rem_4.5rem_1fr] dark:hover:bg-slate-800/60">
                <span className="truncate font-medium text-sky-700 dark:text-sky-400" title={r.model}>{shortModel(r.model)}</span>
                <span className="tabular-nums">{score(r.overall)}</span>
                <span className="tabular-nums text-slate-500">{money(r.cost)}</span>
                <span className="hidden text-right text-xs text-slate-500 sm:block">{r.started_at?.slice(0, 16).replace("T", " ") ?? ""}</span>
              </Link></li>
            ))}</ul>
            {d.runs_total > d.runs.length && <p className="px-3 py-2 text-xs text-slate-500">Showing the latest {d.runs.length} of {d.runs_total}.</p>}
          </Panel>
        )}
      </Section>
      <div className="flex justify-end">
        <button className={p.archived ? "btn-ghost" : "btn-danger"} disabled={busy}
          onClick={() => act(async () => {
            await post(`/api/prompts/${encodeURIComponent(id)}/archive`, { task: "adb", archived: !p.archived, base_version });
            getSession(true);
          }, p.archived ? `Unarchived ${id}` : `Archived ${id}`)}>{p.archived ? "Unarchive" : "Archive this prompt"}</button>
      </div>
    </main>
  );
}

export default function PromptEdit() {
  const { id = "" } = useParams();
  return id === "new" ? <NewPrompt /> : <EditPrompt id={id} />;
}
