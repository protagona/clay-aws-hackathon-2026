import { createContext, type ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { get } from "../api";
import type { Job, JobsResponse } from "../types";

export interface Toast { id: string; tone: "ok" | "bad" | "info"; text: string; href?: string }

const Ctx = createContext<(t: Omit<Toast, "id"> & { id?: string }) => void>(() => {});
export const useToast = () => useContext(Ctx);

/** Toasts, plus one toast per finished job (each job id is announced once). */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seen = useRef<Set<string>>(new Set());
  const started = useRef(Date.now() / 1000);

  const push = useCallback((t: Omit<Toast, "id"> & { id?: string }) => {
    const id = t.id ?? Math.random().toString(36).slice(2);
    setToasts((ts) => [...ts.filter((x) => x.id !== id), { ...t, id }].slice(-4));
    window.setTimeout(() => setToasts((ts) => ts.filter((x) => x.id !== id)), 6000);
  }, []);

  useEffect(() => {
    let timer: number;
    const tick = async () => {
      try {
        const { jobs } = await get<JobsResponse>("/api/jobs");
        for (const toast of jobToasts(jobs, seen.current, started.current)) push(toast);
      } catch { /* the board shows connection problems */ }
      timer = window.setTimeout(tick, 3000);
    };
    tick();
    return () => window.clearTimeout(timer);
  }, [push]);

  return (
    <Ctx.Provider value={push}>
      {children}
      <div aria-live="polite" className="pointer-events-none fixed bottom-4 left-4 z-50 flex flex-col gap-2">
        {toasts.map((t) => (
          <div key={t.id} className="toast-in pointer-events-auto flex items-center gap-3 rounded-xl bg-slate-900 px-4 py-3 text-sm text-white shadow-xl dark:bg-slate-100 dark:text-slate-900">
            <span aria-hidden="true" className={`h-2 w-2 rounded-full ${t.tone === "ok" ? "bg-emerald-400" : t.tone === "bad" ? "bg-rose-400" : "bg-sky-400"}`} />
            <span>{t.tone === "bad" ? "✗ " : t.tone === "ok" ? "✓ " : ""}{t.text}</span>
            {t.href && <Link className="underline" to={t.href}>open</Link>}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

/** Pure: which jobs deserve a toast now (finished since the page opened, not yet announced). */
export function jobToasts(jobs: Job[], seen: Set<string>, since: number): Toast[] {
  const out: Toast[] = [];
  for (const j of jobs) {
    if (j.alive || seen.has(j.id) || !j.finished_at || j.finished_at < since || j.kind === "tools") continue;  // background listing
    seen.add(j.id);
    const ok = j.exit_code === 0;
    const what = j.kind === "evaluate" ? "Scoring" : j.kind === "batch" ? "Batch" : "Run";
    out.push({
      id: j.id, tone: ok ? "ok" : j.failed ? "bad" : "info",
      text: ok ? `${what} finished: ${j.run_id}` : j.failed ? `${what} failed: ${j.message ?? j.run_id}` : `${what} ended: ${j.run_id}`,
      href: j.kind === "batch" ? `/batches/${j.run_id}` : `/runs/${j.run_id}`,
    });
  }
  return out;
}
