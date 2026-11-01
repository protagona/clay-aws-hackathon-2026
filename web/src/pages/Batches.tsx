import { type FormEvent, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, getSession, post } from "../api";
import { useToast } from "../components/toasts";
import { Empty, ErrorBanner, PageHeader, Panel, Pill, Section, Skeleton } from "../components/ui";
import { money, shortModel } from "../format";
import type { BatchesResponse, Session, StartBatchResponse } from "../types";
import { usePoll } from "../usePoll";

export default function Batches() {
  const list = usePoll<BatchesResponse>("/api/batches", (d) => (d?.running ? 2000 : 10000));
  const [session, setSession] = useState<Session | null>(null);
  const [config, setConfig] = useState("offline");
  const [models, setModels] = useState<string[]>([]);
  const [prompts, setPrompts] = useState<string[]>([]);
  const [repeats, setRepeats] = useState(1);
  const [concurrency, setConcurrency] = useState(2);
  const [maxCost, setMaxCost] = useState(2);
  const [evaluate, setEvaluate] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();
  const navigate = useNavigate();
  useEffect(() => { getSession().then(setSession); }, []);
  const toggle = (xs: string[], x: string) => (xs.includes(x) ? xs.filter((y) => y !== x) : [...xs, x]);

  async function start(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { batch_id } = await post<StartBatchResponse>("/api/batches",
        { config, models, prompts, repeats, concurrency, evaluate, max_cost: maxCost });
      toast({ tone: "info", text: `Batch ${batch_id} started` });
      navigate(`/batches/${batch_id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="space-y-8 px-4 py-6 sm:px-8">
      <PageHeader title="Batches" caption="Run several prompts on several models at once, then compare the results." />
      <div className="grid items-start gap-10 lg:grid-cols-[minmax(0,420px)_1fr]">
      <Section n={1} title="New batch" caption="Each model runs each prompt, once per repeat.">
      <Panel className="p-5">
        <form onSubmit={start} className="space-y-4">
          <label className="field">Settings (config)
            <select className="input" value={config} onChange={(e) => setConfig(e.target.value)}>{session?.configs.map((c) => <option key={c}>{c}</option>)}</select>
          </label>
          <fieldset><legend className="field">Models <span className="font-normal text-slate-400">· leave empty to use the config's model</span></legend>
            <div className="mt-1 flex flex-wrap gap-2">{session?.models.map((m) => (
              <label key={m} className={`chip cursor-pointer text-xs ${models.includes(m) ? "ring-2 ring-sky-500" : ""}`}>
                <input type="checkbox" className="sr-only" checked={models.includes(m)} onChange={() => setModels(toggle(models, m))} />{shortModel(m)}</label>
            ))}</div>
          </fieldset>
          <fieldset><legend className="field">Prompts</legend>
            <div className="mt-1 flex flex-wrap gap-2">{session?.prompts.map((p) => (
              <label key={p} className={`chip cursor-pointer text-xs ${prompts.includes(p) ? "ring-2 ring-sky-500" : ""}`}>
                <input type="checkbox" className="sr-only" checked={prompts.includes(p)} onChange={() => setPrompts(toggle(prompts, p))} />{p}</label>
            ))}</div>
          </fieldset>
          <div className="grid grid-cols-3 gap-3">
            <label className="field">Repeats<span className="block text-xs font-normal text-slate-400">runs per model and prompt</span><input className="input" type="number" min={1} max={5} value={repeats} onChange={(e) => setRepeats(+e.target.value)} /></label>
            <label className="field">At once<span className="block text-xs font-normal text-slate-400">runs side by side</span><input className="input" type="number" min={1} max={4} value={concurrency} onChange={(e) => setConcurrency(+e.target.value)} /></label>
            <label className="field">Stop at ($)<span className="block text-xs font-normal text-slate-400">cost cap</span><input className="input" type="number" min={0.01} max={50} step="any" value={maxCost} onChange={(e) => setMaxCost(+e.target.value)} /></label>
          </div>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={evaluate} onChange={(e) => setEvaluate(e.target.checked)} /> Score each run with the judge</label>
          {error && <p role="alert" className="text-sm text-rose-600">✗ {error}</p>}
          <button className="btn-primary" disabled={busy || prompts.length === 0}>{busy ? "Starting…" : "Start batch"}</button>
        </form>
      </Panel>
      </Section>
      <Section n={2} title="Past batches" caption="Newest first. Click one to see its results.">
      <div className="space-y-3">
        {list.error && <ErrorBanner message={list.error} onRetry={list.reload} />}
        {!list.data ? <Skeleton className="h-40" /> : list.data.batches.length === 0 ? <Empty title="No batches yet">Pick prompts and models on the left.</Empty> : (
          <ul className="space-y-3">{list.data.batches.map((b) => (
            <li key={b.id}><Link to={`/batches/${b.id}`} className="block rounded-2xl bg-white p-4 shadow-sm ring-1 ring-slate-200 transition hover:shadow-md dark:bg-slate-900 dark:ring-slate-800">
              <div className="flex items-center justify-between gap-3"><span className="break-all font-mono text-sm">{b.id}</span>
                <Pill tone={b.status === "running" ? "sky" : b.status === "finished" ? "emerald" : "slate"}>{b.status}</Pill></div>
              <div className="mt-2 h-1.5 rounded-full bg-slate-100 dark:bg-slate-800"><div className={`h-1.5 rounded-full ${b.status === "running" ? "working" : "bg-sky-500"}`} style={{ width: `${b.jobs ? (b.done / b.jobs) * 100 : 0}%` }} /></div>
              <div className="mt-2 flex justify-between text-xs text-slate-500"><span>{b.done}/{b.jobs} runs · {b.models.map(shortModel).join(", ")}</span><span>{money(b.spent, 2)}{b.max_cost ? ` of ${money(b.max_cost, 2)}` : ""}</span></div>
            </Link></li>
          ))}</ul>
        )}
      </div>
      </Section>
      </div>
    </main>
  );
}
