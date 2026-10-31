import { type ReactNode, useEffect, useRef } from "react";
import { ringColor } from "../format";

export function Panel({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-2xl bg-white shadow-sm ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-800 ${className}`}>
      {children}
    </div>
  );
}

export function Pill({ tone = "slate", children }: { tone?: "sky" | "amber" | "emerald" | "rose" | "slate" | "violet"; children: ReactNode }) {
  const tones = {
    sky: "bg-sky-50 text-sky-700 ring-sky-200 dark:bg-sky-500/10 dark:text-sky-300 dark:ring-sky-500/30",
    amber: "bg-amber-50 text-amber-700 ring-amber-200 dark:bg-amber-500/10 dark:text-amber-300 dark:ring-amber-500/30",
    emerald: "bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-500/10 dark:text-emerald-300 dark:ring-emerald-500/30",
    rose: "bg-rose-50 text-rose-700 ring-rose-200 dark:bg-rose-500/10 dark:text-rose-300 dark:ring-rose-500/30",
    violet: "bg-violet-50 text-violet-700 ring-violet-200 dark:bg-violet-500/10 dark:text-violet-300 dark:ring-violet-500/30",
    slate: "bg-slate-100 text-slate-600 ring-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-700",
  };
  return <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ${tones[tone]}`}>{children}</span>;
}

export function ScoreRing({ value, size = 44, label }: { value: number | null | undefined; size?: number; label?: string }) {
  const pct = value === null || value === undefined ? 0 : Math.max(0, Math.min(1, value));
  const text = value === null || value === undefined ? "—" : value >= 1 ? "1.00" : value.toFixed(2).replace(/^0/, "");
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }} role="img" aria-label={`${label ?? "score"} ${text}`}>
      <svg viewBox="0 0 36 36" className="h-full w-full -rotate-90" aria-hidden="true">
        <circle cx="18" cy="18" r="15" fill="none" className="stroke-slate-200 dark:stroke-slate-700" strokeWidth="4" />
        <circle cx="18" cy="18" r="15" fill="none" className={`${ringColor(value)} ring-anim`} strokeWidth="4"
          strokeLinecap="round" strokeDasharray={`${(pct * 94.25).toFixed(1)} 94.25`} />
      </svg>
      <span className="absolute inset-0 grid place-content-center text-[11px] font-semibold tabular-nums">{text}</span>
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return <span role="status" aria-label={label} className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-sky-500 border-t-transparent" />;
}

export function ErrorBanner({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex items-center justify-between gap-3 rounded-xl bg-rose-50 px-4 py-3 text-sm text-rose-800 ring-1 ring-rose-200 dark:bg-rose-500/10 dark:text-rose-200 dark:ring-rose-500/30">
      <span>✗ {message}</span>
      {onRetry && <button className="btn-ghost" onClick={onRetry}>Retry</button>}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-2xl border border-dashed border-slate-300 p-8 text-center text-sm text-slate-500 dark:border-slate-700">
      <div className="font-medium text-slate-700 dark:text-slate-300">{title}</div>
      {children && <div className="mt-2">{children}</div>}
    </div>
  );
}

export function Skeleton({ className = "h-24" }: { className?: string }) {
  return <div className={`animate-pulse rounded-2xl bg-slate-200/70 dark:bg-slate-800 ${className}`} />;
}

export function Kpi({ label, value, sub, accent }: { label: string; value: ReactNode; sub?: ReactNode; accent?: boolean }) {
  return (
    <Panel className="p-4">
      <div className="text-xs text-slate-500">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${accent ? "text-sky-600 dark:text-sky-400" : ""}`}>{value}</div>
      {sub && <div className="text-xs text-slate-500">{sub}</div>}
    </Panel>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: ReactNode }[]; value: T; onChange: (t: T) => void }) {
  return (
    <div role="tablist" className="flex gap-5 overflow-x-auto text-sm">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={value === t.id} onClick={() => onChange(t.id)}
          className={`whitespace-nowrap border-b-2 pb-2 ${value === t.id ? "border-sky-500 font-medium" : "border-transparent text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"}`}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Dialog({ open, title, onClose, children }: { open: boolean; title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog ref={ref} onClose={onClose} aria-label={title}
      className="m-auto w-[min(480px,calc(100vw-2rem))] rounded-2xl bg-white p-0 text-slate-900 shadow-2xl ring-1 ring-slate-200 backdrop:bg-slate-900/40 backdrop:backdrop-blur-sm dark:bg-slate-900 dark:text-slate-100 dark:ring-slate-700">
      <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4 dark:border-slate-800">
        <h2 className="font-semibold">{title}</h2>
        <button className="btn-ghost" onClick={onClose} aria-label="Close">✕</button>
      </div>
      <div className="p-5">{children}</div>
    </dialog>
  );
}
