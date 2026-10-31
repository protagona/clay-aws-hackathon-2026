import { type FormEvent, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, getSession, post, upload } from "../api";
import { Dropzone } from "../components/Dropzone";
import { useToast } from "../components/toasts";
import { Panel, Pill } from "../components/ui";
import type { Session } from "../types";
import { usePoll } from "../usePoll";

interface Receiver { running: boolean; port: number; endpoint: string; count: number; received: string[] }

export default function Import() {
  const [session, setSession] = useState<Session | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [task, setTask] = useState("adb");
  const [promptId, setPromptId] = useState("imported");
  const [config, setConfig] = useState("");
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const rec = usePoll<Receiver>("/api/receiver", (d) => (d?.running ? 2000 : 10000));
  const toast = useToast();
  const navigate = useNavigate();
  useEffect(() => { getSession().then(setSession); }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    form.append("task", task);
    form.append("prompt_id", promptId);
    form.append("config", config);
    setError(null);
    setProgress(0);
    try {
      const { runs } = await upload<{ runs: string[] }>("/api/import", form, setProgress);
      toast({ tone: "ok", text: `Imported ${runs.length} run${runs.length === 1 ? "" : "s"}` });
      navigate(runs.length === 1 ? `/runs/${runs[0]}` : "/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setProgress(null);
    }
  }

  async function toggleReceiver() {
    try {
      await post(rec.data?.running ? "/api/receiver/stop" : "/api/receiver/start", rec.data?.running ? undefined : { task, prompt_id: promptId, config });
      rec.reload();
    } catch (err) {
      toast({ tone: "bad", text: err instanceof ApiError ? err.message : String(err) });
    }
  }

  const common = (
    <div className="grid gap-3 sm:grid-cols-3">
      <label className="field">Task<select className="input" value={task} onChange={(e) => setTask(e.target.value)}>{session?.tasks.map((t) => <option key={t}>{t}</option>)}</select></label>
      <label className="field">Prompt id<input className="input" value={promptId} onChange={(e) => setPromptId(e.target.value)} /></label>
      <label className="field">Prices from<select className="input" value={config} onChange={(e) => setConfig(e.target.value)}><option value="">(none)</option>{session?.configs.map((c) => <option key={c}>{c}</option>)}</select></label>
    </div>
  );

  return (
    <main className="space-y-6 px-4 py-6 sm:px-8">
      <Panel className="p-5">
        <h1 className="text-lg font-semibold">Import agent runs</h1>
        <p className="mb-3 text-sm text-slate-500">Runs imported below are recorded under this task and prompt id.</p>
        {common}
      </Panel>
      <div className="grid gap-6 lg:grid-cols-2">
      <Panel className="p-5">
        <h2 className="text-lg font-semibold">Upload a trace</h2>
        <p className="mb-4 text-sm text-slate-500">Evaluate an agent we didn't build: drop an OTLP/JSON trace file. Each agent run becomes a normal run.</p>
        <form onSubmit={submit} className="space-y-4">
          <Dropzone accept=".json,.jsonl" file={file} onFile={setFile} hint="OTLP/JSON or JSON Lines · up to 50 MB" />
          {progress !== null && <div className="h-2 rounded-full bg-slate-100 dark:bg-slate-800" role="progressbar" aria-valuenow={progress}><div className="h-2 rounded-full bg-sky-500 transition-all" style={{ width: `${progress}%` }} /></div>}
          {error && <p role="alert" className="text-sm text-rose-600">✗ {error}</p>}
          <button className="btn-primary" disabled={!file || progress !== null}>{progress !== null ? `Uploading… ${progress}%` : "Import"}</button>
        </form>
      </Panel>
      <Panel className="p-5">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-lg font-semibold">Live receiver</h2>
          {rec.data && <Pill tone={rec.data.running ? "emerald" : "slate"}>{rec.data.running ? "● receiving" : "stopped"}</Pill>}
        </div>
        <p className="mb-4 text-sm text-slate-500">Point any OpenTelemetry-instrumented agent at this endpoint; each run it sends appears on the board.</p>
        {rec.error && <p role="alert" className="text-sm text-rose-600">✗ Can't reach the receiver status: {rec.error}</p>}
        <div className="mt-4 rounded-xl bg-slate-50 p-3 font-mono text-xs dark:bg-slate-800">OTEL_EXPORTER_OTLP_ENDPOINT={rec.data?.endpoint ?? "…"}</div>
        <button className="btn-primary mt-4" onClick={toggleReceiver}>{rec.data?.running ? "Stop receiving" : "Start receiving"}</button>
        {rec.data?.running && (
          <div className="mt-4 text-sm"><div className="font-medium">{rec.data.count} run{rec.data.count === 1 ? "" : "s"} received</div>
            <ul className="mt-1 space-y-1">{rec.data.received.slice().reverse().map((r) => <li key={r} className="row-in"><Link className="text-sky-600" to={`/runs/${r}`}>{r}</Link></li>)}</ul>
          </div>
        )}
      </Panel>
      </div>
    </main>
  );
}
