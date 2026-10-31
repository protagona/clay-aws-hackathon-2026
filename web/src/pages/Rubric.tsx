import { type FormEvent, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, getSession, post, upload } from "../api";
import { Dropzone } from "../components/Dropzone";
import { useToast } from "../components/toasts";
import { ErrorBanner, Panel, Pill, Skeleton, Spinner, Tabs } from "../components/ui";
import type { RubricOverview, Session } from "../types";
import { usePoll } from "../usePoll";

type Kind = "file" | "text" | "url" | "mcp";

export default function Rubric() {
  const ov = usePoll<RubricOverview>("/api/rubric?task=adb", () => null);
  const [session, setSession] = useState<Session | null>(null);
  const [kind, setKind] = useState<Kind>("file");
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [mcp, setMcp] = useState("");
  const [args, setArgs] = useState("");
  const [config, setConfig] = useState("bedrock");
  const [phase, setPhase] = useState<"idle" | "uploading" | "drafting">("idle");
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();
  const navigate = useNavigate();
  useEffect(() => { getSession().then(setSession); }, []);

  async function draft(e: FormEvent) {
    e.preventDefault();
    const form = new FormData();
    form.append("task", "adb");
    form.append("kind", kind);
    form.append("config", config);
    if (kind === "file" && file) form.append("file", file);
    if (kind === "text") form.append("text", text);
    if (kind === "url") form.append("url", url);
    if (kind === "mcp") { form.append("mcp", mcp); form.append("args", args); }
    setError(null);
    setPhase(kind === "file" ? "uploading" : "drafting");
    try {
      const { draft_id } = await upload<{ draft_id: string }>("/api/rubric/drafts", form, (p) => {
        setProgress(p);
        if (p >= 100) setPhase("drafting");
      });
      toast({ tone: "ok", text: "Draft ready for review" });
      navigate(`/rubric/drafts/${draft_id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setPhase("idle");
    }
  }

  async function activate(file: string) {
    try {
      await post("/api/rubric/history/activate", { task: "adb", file });
      toast({ tone: "ok", text: `Activated ${file}` });
      getSession(true);
      ov.reload();
    } catch (err) {
      toast({ tone: "bad", text: err instanceof ApiError ? err.message : String(err) });
    }
  }

  const ready = kind === "file" ? !!file : kind === "text" ? !!text.trim() : kind === "url" ? !!url.trim() : !!mcp.trim();
  const d = ov.data;
  return (
    <main className="grid gap-6 px-4 py-6 sm:px-8 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <div className="space-y-6">
        {ov.error && <ErrorBanner message={ov.error} onRetry={ov.reload} />}
        <Panel className="p-5">
          <h1 className="text-lg font-semibold">Active rubric</h1>
          {!d ? <Skeleton className="mt-3 h-24" /> : d.active ? (<>
            <p className="mt-1">{(() => { const f = d.versions.find((v) => v.active)?.file;
              return f ? <Link className="font-semibold text-sky-700 dark:text-sky-400" to={`/rubric/history/${f}`}>{d.active.rubric_id}</Link> : <strong>{d.active.rubric_id}</strong>; })()} v{d.active.version} · {d.active.criteria.length} criteria</p>
            <div className="mt-3 flex flex-wrap gap-2">{d.active.criteria.map((c) => <Pill key={c.id}>{c.id} · 1–{c.max} · ×{c.weight}</Pill>)}</div>
            <p className="mt-3 text-xs text-slate-500">Future evaluations use the active rubric. Past runs keep the rubric they were scored with.</p>
          </>) : <p className="mt-2 text-sm text-rose-600">✗ No valid rubric.json for this task.</p>}
        </Panel>
        <Panel className="overflow-x-auto p-2">
          <h2 className="px-3 pt-2 font-semibold">History</h2>
          {d && d.versions.length === 0 ? <p className="px-3 py-2 text-sm text-slate-500">No history yet; the current rubric is archived on the first activation.</p> : (
            <table className="table"><thead><tr><th>Version</th><th>Rubric</th><th>Activated</th><th>From</th><th>Runs</th><th></th></tr></thead>
              <tbody>{d?.versions.map((v) => (
                <tr key={v.sha256}><td>v{v.version}</td><td><Link className="text-sky-600" to={`/rubric/history/${v.file}`}>{v.rubric_id}</Link></td>
                  <td className="whitespace-nowrap">{v.activated_at ? v.activated_at.slice(0, 16).replace("T", " ") : "(original)"}</td>
                  <td className="max-w-[12rem] truncate" title={v.source?.label ?? v.note}>{v.source?.label ?? v.note ?? ""}</td><td>{v.runs}</td>
                  <td>{v.active ? <span className="font-medium text-emerald-600">✓ active</span> : <button className="btn-ghost" onClick={() => activate(v.file)}>Activate</button>}</td></tr>
              ))}</tbody></table>
          )}
        </Panel>
      </div>
      <div className="space-y-6">
        <Panel className="p-5">
          <h2 className="text-lg font-semibold">Import a rubric</h2>
          <p className="mb-4 text-sm text-slate-500">A model drafts structured criteria from the source; you review and edit the draft before anything changes.</p>
          <form onSubmit={draft} className="space-y-4">
            <Tabs<Kind> value={kind} onChange={setKind} tabs={[{ id: "file", label: "File" }, { id: "text", label: "Text" }, { id: "url", label: "URL" }, { id: "mcp", label: "MCP tool" }]} />
            {kind === "file" && <Dropzone accept=".pdf,.docx,.xlsx,.csv,.md,.markdown,.txt,.html,.htm,.json" file={file} onFile={setFile} hint="PDF, Word, Excel (every tab), CSV, Markdown, text, HTML or JSON · up to 4.5 MB" />}
            {kind === "text" && <label className="field">A few sentences, or another tool's output<textarea className="input min-h-32" value={text} onChange={(e) => setText(e.target.value)} maxLength={50000} /></label>}
            {kind === "url" && <label className="field">https URL<input className="input" placeholder="https://…" value={url} onChange={(e) => setUrl(e.target.value)} /></label>}
            {kind === "mcp" && <div className="grid gap-3 sm:grid-cols-2">
              <label className="field">server/tool<input className="input" placeholder="roda/get_dataset_details" value={mcp} onChange={(e) => setMcp(e.target.value)} /></label>
              <label className="field">Arguments (JSON)<input className="input" placeholder='{"slug": "naip"}' value={args} onChange={(e) => setArgs(e.target.value)} /></label></div>}
            <label className="field">Config (its judge drafts the rubric)<select className="input" value={config} onChange={(e) => setConfig(e.target.value)}>{session?.configs.map((c) => <option key={c}>{c}</option>)}</select></label>
            {phase !== "idle" && (
              <div className="flex items-center gap-3 rounded-xl bg-sky-50 px-4 py-3 text-sm text-sky-800 dark:bg-sky-500/10 dark:text-sky-200" role="status">
                <Spinner label="Working" />
                {phase === "uploading" ? <>Uploading… {progress}%</> : <>Drafting with the judge model… this takes up to a minute.</>}
              </div>
            )}
            {error && <p role="alert" className="text-sm text-rose-600">✗ {error}</p>}
            <button className="btn-primary" disabled={!ready || phase !== "idle"}>Draft rubric</button> <span className="text-xs text-slate-500">about $0.01–0.15</span>
          </form>
        </Panel>
        {d && d.drafts.length > 0 && (
          <Panel className="p-5"><h2 className="font-semibold">Drafts waiting for review</h2>
            <ul className="mt-2 space-y-2 text-sm">{d.drafts.map((m) => (
              <li key={m.draft_id} className="flex items-center justify-between gap-3">
                <span className="truncate" title={m.source.label}>{m.source.label}</span>
                <span className="flex shrink-0 items-center gap-2">{m.valid ? <Pill tone="emerald">✓ valid</Pill> : <Pill tone="rose">✗ invalid</Pill>}
                  <Link className="text-sky-600" to={`/rubric/drafts/${m.draft_id}`}>Review</Link></span>
              </li>))}</ul>
          </Panel>
        )}
      </div>
    </main>
  );
}
