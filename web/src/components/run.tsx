// Run views shared by the drawer and the compare page: gauges, waterfall, step feed. Pure presentation.
import { useState } from "react";
import { money } from "../format";
import type { Monitor, Row, Step } from "../types";
import { Pill } from "./ui";

export function Gauges({ m }: { m: Monitor }) {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      {m.gauges.map((g) => (
        <div key={g.label} className="rounded-xl bg-slate-50 p-3 dark:bg-slate-800/60">
          <div className="text-xs text-slate-500">{g.label}</div>
          <div className="font-mono text-lg tabular-nums">{g.value}{g.max && <span className="text-slate-400">/{g.max}</span>}</div>
          {g.pct !== null && (
            <div className="mt-2 h-1.5 rounded-full bg-slate-200 dark:bg-slate-700" role="img" aria-label={`${g.label} ${g.pct}% of limit${g.level === "bad" ? ", limit reached" : ""}`}>
              <div className={`bar-anim h-1.5 rounded-full ${g.level === "bad" ? "bg-rose-500" : g.level === "warn" ? "bg-amber-500" : "bg-sky-500"}`} style={{ width: `${g.pct}%` }} />
            </div>
          )}
        </div>
      ))}
      <div className="grid grid-cols-2 gap-x-4 text-sm sm:col-span-3 sm:grid-cols-4">
        <div><span className="text-slate-500">Cost </span><span className="font-medium tabular-nums">{money(m.cost)}</span></div>
        <div><span className="text-slate-500">Out tokens </span><span className="font-medium">{m.tokens_out}</span></div>
        <div><span className="text-slate-500">Tools </span><span className="font-medium">{m.tools_ok} ok{m.tools_err > 0 && <span className="text-rose-600"> · {m.tools_err} ✗</span>}</span></div>
        {m.servers.length > 0 && <div className="truncate"><span className="text-slate-500">Servers </span>{m.servers.map(([n, c]) => `${n} ${c}`).join(" · ")}</div>}
      </div>
    </div>
  );
}

const barTone = (r: Row) =>
  r.kind === "agent" ? "bg-sky-500/25 ring-1 ring-sky-500/50" : r.error ? "bg-rose-500" : r.kind === "tool" ? "bg-teal-500" : "bg-sky-600";

export function Waterfall({ m }: { m: Monitor }) {
  if (!m.rows.length && !m.working) return <p className="text-sm text-slate-500">No steps recorded yet.</p>;
  return (
    <div className="overflow-x-auto">
      <div className="min-w-[520px] text-xs">
        <div className="grid grid-cols-[minmax(140px,32%)_1fr] gap-3 border-b border-slate-200 pb-1 dark:border-slate-800">
          <span />
          <span className="relative h-4">{m.axis.map((t) => <span key={t.pct} className="absolute -translate-x-1/2 text-slate-400 first:translate-x-0" style={{ left: `${t.pct}%` }}>{t.label}</span>)}</span>
        </div>
        {m.rows.map((r) => (
          <div key={`${r.kind}-${r.step}`} className={`grid grid-cols-[minmax(140px,32%)_1fr] items-center gap-3 rounded py-0.5 hover:bg-slate-50 dark:hover:bg-slate-800/50 ${r.kind === "agent" ? "mt-1 border-t border-slate-200 pt-1 dark:border-slate-800" : ""} ${r.new ? "row-in" : ""}`}>
            <span className={`truncate ${r.indent ? "pl-4" : ""}`} title={r.label}>
              {r.kind === "agent" ? (
                <><strong>{r.label}</strong>{r.verdict === "approve" && <span className="text-emerald-600"> ✓ approve</span>}{r.verdict === "revise" && <span className="text-rose-600"> ✗ revise</span>}
                  <span className="text-slate-400"> · {r.calls} call{r.calls === 1 ? "" : "s"} · {r.secs}s</span></>
              ) : (<><span className="mr-1 font-mono text-slate-400">{r.step}</span>{r.label}{r.error && <span className="text-rose-600"> ✗</span>}</>)}
            </span>
            <span className="relative h-3.5">
              <span className={`absolute top-0.5 h-2.5 rounded ${barTone(r)}`} style={{ left: `${r.left}%`, width: `${r.width}%` }}
                title={`${r.label}${r.ms !== null ? ` · ${Math.round(r.ms)} ms` : ""}${r.error ? " · error" : ""}`} />
            </span>
          </div>
        ))}
        {m.working && (
          <div className="grid grid-cols-[minmax(140px,32%)_1fr] items-center gap-3 py-0.5">
            <span className="text-sky-600">working…</span>
            <span className="relative h-3.5"><span className="working absolute top-0.5 h-2.5 rounded" style={{ left: `${m.working.left}%`, width: `${m.working.width}%` }} /></span>
          </div>
        )}
        <div className="mt-2 flex gap-4 text-slate-500">
          <span><i className="mr-1 inline-block h-2 w-3 rounded bg-sky-600" />model</span>
          <span><i className="mr-1 inline-block h-2 w-3 rounded bg-teal-500" />tool</span>
          <span><i className="mr-1 inline-block h-2 w-3 rounded bg-rose-500" />error</span>
        </div>
      </div>
    </div>
  );
}

function StepBody({ s }: { s: Step }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      {s.error && <div className="mt-1 break-words text-sm text-rose-600">{s.error.slice(0, 400)}</div>}
      {s.type === "llm_call" && s.text_html && <div className="prose-sm mt-1 max-h-80 overflow-auto break-words" dangerouslySetInnerHTML={{ __html: s.text_html }} />}
      {s.type === "llm_call" && !s.text_html && s.text && <div className="mt-1 whitespace-pre-wrap break-words text-sm">{s.text}</div>}
      {s.type === "note" && s.name === "verdict" && (
        <div className="mt-1"><Pill tone={s.text === "approve" ? "emerald" : s.text === "revise" ? "rose" : "amber"}>{s.text === "approve" ? "✓ approve" : s.text === "revise" ? "✗ revise" : `? ${s.text}`}</Pill></div>
      )}
      {s.type === "note" && s.name !== "verdict" && s.text && <div className="mt-1 text-sm text-slate-600 dark:text-slate-300">{s.text}</div>}
      {s.type === "tool_call" && (s.args || s.result_preview) && (
        <div className="mt-1">
          <button className="text-xs text-sky-600 hover:underline" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "▾" : "▸"} Arguments and result</button>
          {open && (
            <div className="mt-1 space-y-1">
              {s.args && <pre className="max-h-60 overflow-auto rounded-lg bg-slate-50 p-2 text-xs dark:bg-slate-800">{JSON.stringify(s.args, null, 1)}</pre>}
              {s.result_preview && <pre className="max-h-60 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-slate-50 p-2 text-xs dark:bg-slate-800">{s.result_preview.slice(0, 1500)}</pre>}
            </div>
          )}
        </div>
      )}
    </>
  );
}

export function StepFeed({ m, limit }: { m: Monitor; limit?: number }) {
  const items = limit ? m.feed.slice(0, limit) : m.feed;
  if (!items.length) return <p className="text-sm text-slate-500">No steps recorded yet.</p>;
  return (
    <ol className="space-y-2">
      {items.map(({ s, agent, divider, new: isNew }) => divider ? (
        <li key={s.step} className={`border-t-2 border-slate-200 pt-2 text-sm dark:border-slate-700 ${isNew ? "row-in" : ""}`}>
          <span className="mr-2 font-mono text-xs text-slate-400">{s.step}</span><strong>▸ {s.name}</strong> <span className="text-slate-500">started</span>
          {s.text && <details className="mt-1 text-xs"><summary className="cursor-pointer text-sky-600">Input</summary><pre className="mt-1 whitespace-pre-wrap rounded-lg bg-slate-50 p-2 dark:bg-slate-800">{s.text}</pre></details>}
        </li>
      ) : (
        <li key={s.step} className={`rounded-xl p-3 ring-1 ${s.status === "error" ? "ring-rose-300 dark:ring-rose-500/40" : "ring-slate-200 dark:ring-slate-800"} ${isNew ? "row-in" : ""}`}>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="font-mono text-slate-400">{s.step}</span>
            {agent && <Pill tone="violet">{agent}</Pill>}
            <span className="font-medium text-sm">{s.type === "tool_call" ? `${s.server ? `${s.server}/` : ""}${s.name}` : s.type === "llm_call" ? "model" : s.name}</span>
            {s.type === "llm_call" && <span className="text-slate-500">{(s.duration_ms / 1000).toFixed(1)}s · {s.tokens_in ?? 0} in → {s.tokens_out ?? 0} out{s.cost_usd != null && ` · ${money(s.cost_usd, 4)}`}</span>}
            {s.type === "tool_call" && <span className="text-slate-500">{Math.round(s.duration_ms)} ms</span>}
            {s.status === "error" ? <Pill tone="rose">✗ error</Pill> : s.type !== "note" && <Pill tone="emerald">ok</Pill>}
            {s.retry_of != null && <span className="text-slate-500">retry of {s.retry_of}</span>}
            {s.invalid && <Pill tone="amber">invalid</Pill>}
          </div>
          <StepBody s={s} />
        </li>
      ))}
    </ol>
  );
}

/** Agent bars for pipeline runs (from the waterfall's agent rows). */
export function AgentBars({ m }: { m: Monitor }) {
  const agents = m.rows.filter((r) => r.kind === "agent");
  if (!agents.length) return null;
  const last = agents[agents.length - 1];
  return (
    <div className="space-y-2 text-xs">
      {agents.map((a) => {
        const live = m.live && a === last;
        return (
          <div key={a.step} className="flex items-center gap-3">
            <span className={`w-36 truncate ${live ? "font-medium text-sky-600" : "text-slate-500"}`}>{a.label}</span>
            <div className="relative h-3 flex-1 rounded bg-slate-100 dark:bg-slate-800">
              <div className={`absolute h-3 rounded ${live ? "working" : "bg-sky-500"}`} style={{ left: `${a.left}%`, width: `${a.width}%` }} />
            </div>
            <span className="w-14 text-right font-mono text-slate-500">{live ? "live" : `${a.secs}s`}</span>
          </div>
        );
      })}
    </div>
  );
}
