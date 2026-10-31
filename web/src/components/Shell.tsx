import { type FormEvent, type ReactNode, useEffect, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { ApiError, getSession, post } from "../api";
import type { Session } from "../types";
import { useToast } from "./toasts";
import { Dialog } from "./ui";

const NAV = [
  { to: "/", label: "Runs" },
  { to: "/batches", label: "Batches" },
  { to: "/dashboard", label: "Dashboard" },
  { to: "/rubric", label: "Rubric" },
  { to: "/import", label: "Import" },
];

/** Follows the system theme until the user picks one with the toggle; only an explicit choice is remembered. */
function useTheme() {
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  const [saved, setSaved] = useState<string | null>(() => { try { return localStorage.getItem("theme"); } catch { return null; } });
  const [system, setSystem] = useState(media.matches);
  useEffect(() => {
    const on = (e: MediaQueryListEvent) => setSystem(e.matches);
    media.addEventListener("change", on);
    return () => media.removeEventListener("change", on);
  }, [media]);
  const dark = saved ? saved === "dark" : system;
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    // Increment 14: the tab icon follows the app's theme, not only the system's (the media links are the fallback).
    for (const link of document.querySelectorAll<HTMLLinkElement>('link[rel="icon"]')) {
      link.href = link.href.replace(/favicon-(light|dark)-/, `favicon-${dark ? "dark" : "light"}-`);
      link.removeAttribute("media");
    }
  }, [dark]);
  const choose = (d: boolean) => {
    setSaved(d ? "dark" : "light");
    try { localStorage.setItem("theme", d ? "dark" : "light"); } catch { /* storage blocked */ }
  };
  return [dark, choose] as const;
}

export function Shell({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [dark, setDark] = useTheme();
  const [open, setOpen] = useState(false);
  const { pathname } = useLocation();
  const on = (to: string) => (to === "/" ? pathname === "/" || pathname.startsWith("/runs/") || pathname === "/compare" : pathname.startsWith(to));
  useEffect(() => { getSession().then(setSession).catch(() => setSession(null)); }, []);

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/80 backdrop-blur dark:border-slate-800 dark:bg-slate-950/80">
        <div className="flex h-16 items-center gap-4 px-4 sm:px-8">
          <NavLink to="/" className="flex items-center gap-2 font-semibold">
            <img src="/assets/brand/logo-light-v1.png" width={32} height={32} alt="" className="dark:hidden" />
            <img src="/assets/brand/logo-dark-v1.png" width={32} height={32} alt="" className="hidden dark:block" />
            <span>Foundry<span className="font-normal text-slate-400"> / eval</span></span>
          </NavLink>
          <nav aria-label="Sections" className="hidden rounded-xl bg-slate-100 p-1 text-sm md:flex dark:bg-slate-900">
            {NAV.map((n) => (
              <NavLink key={n.to} to={n.to} aria-current={on(n.to) ? "page" : undefined}
                className={`rounded-lg px-3 py-1.5 ${on(n.to) ? "bg-white font-medium shadow-sm dark:bg-slate-800" : "text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"}`}>
                {n.label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3">
            {session?.rubric && (
              <NavLink to="/rubric" className="hidden font-mono text-xs text-slate-500 lg:inline" title="Active rubric">
                Scoring with {session.rubric.id} v{session.rubric.version}
              </NavLink>
            )}
            {session?.aws_profile && <span className="hidden text-xs text-slate-500 xl:inline" title="AWS profile">AWS profile {session.aws_profile}</span>}
            <button className="btn-ghost" onClick={() => setDark(!dark)} aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}>
              {dark ? "☀" : "◐"}
            </button>
            <button className="btn-primary" onClick={() => setOpen(true)}>▶ New run</button>
          </div>
        </div>
        <nav aria-label="Sections" className="flex gap-1 overflow-x-auto px-4 pb-2 text-sm md:hidden">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} aria-current={on(n.to) ? "page" : undefined}
              className={`whitespace-nowrap rounded-lg px-3 py-1.5 ${on(n.to) ? "bg-slate-900 text-white dark:bg-white dark:text-slate-900" : "text-slate-500"}`}>
              {n.label}
            </NavLink>
          ))}
        </nav>
      </header>
      {children}
      <NewRunDialog open={open} onClose={() => setOpen(false)} session={session} />
    </div>
  );
}

function NewRunDialog({ open, onClose, session }: { open: boolean; onClose: () => void; session: Session | null }) {
  const [prompt, setPrompt] = useState("");
  const [config, setConfig] = useState("offline");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();
  const navigate = useNavigate();
  useEffect(() => { if (session && !prompt) setPrompt(session.prompts[0] ?? ""); }, [session, prompt]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { run_id } = await post<{ run_id: string }>("/api/runs", { prompt_id: prompt, config });
      toast({ tone: "info", text: `Started ${prompt} on ${config}` });
      onClose();
      navigate(`/runs/${run_id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} title="New run" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <p data-caption className="text-sm text-slate-500">Run one prompt with one set of settings.</p>
        <label className="field">Prompt <span className="font-normal text-slate-400">· the question the agent answers</span>
          <select value={prompt} onChange={(e) => setPrompt(e.target.value)} className="input">
            {session?.prompts.map((p) => <option key={p}>{p}</option>)}
          </select>
        </label>
        <label className="field">Settings (config)
          <select value={config} onChange={(e) => setConfig(e.target.value)} className="input">
            {session?.configs.map((c) => <option key={c}>{c}</option>)}
          </select>
        </label>
        <p className="text-xs text-slate-500">Bedrock configs call real models and cost money; offline ones replay scripts.</p>
        {error && <p role="alert" className="text-sm text-rose-600">✗ {error}</p>}
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={busy || !prompt}>{busy ? "Starting…" : "Start run"}</button>
        </div>
      </form>
    </Dialog>
  );
}
