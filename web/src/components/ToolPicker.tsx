// Increment 15: pick a tool for an expected step from the servers' full menu (with descriptions and how many runs used
// each), or type one. WAI-ARIA combobox: the input owns a listbox of options grouped by server.
import { useId, useMemo, useRef, useState } from "react";
import type { ToolInfo } from "../types";

export function ToolPicker({ label, tools, onPick, refreshing, errors = [], listedAt, onRefresh, loading = false }: {
  label: string; tools: ToolInfo[]; onPick: (name: string) => void; refreshing: boolean; loading?: boolean;
  errors: { server: string | null; error: string }[]; listedAt: string | null; onRefresh: () => void;
}) {
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);  // nothing highlighted until an arrow key: Enter then adds the typed text
  const id = useId();
  const wrap = useRef<HTMLDivElement>(null);
  const [shift, setShift] = useState(0);  // px to move the panel left so it stays on screen (phone width)
  const q = text.trim().toLowerCase();
  const shown = useMemo(() => tools.filter((t) => !q || t.name.toLowerCase().includes(q) || (t.description ?? "").toLowerCase().includes(q)
    || (t.server ?? "").toLowerCase().includes(q)), [tools, q]);
  const groups = useMemo(() => {
    const by = new Map<string, ToolInfo[]>();
    for (const t of shown) by.set(t.server ?? "other", [...(by.get(t.server ?? "other") ?? []), t]);
    return [...by.entries()];
  }, [shown]);
  const pick = (name: string) => { onPick(name); setText(""); setOpen(false); setActive(-1); };
  const optId = (i: number) => `${id}-opt-${i}`;

  return (
    <div ref={wrap} className="relative inline-flex items-center gap-1"
      onBlur={(e) => { if (!wrap.current?.contains(e.relatedTarget as Node)) setOpen(false); }}>
      <input role="combobox" aria-label={label} aria-expanded={open} aria-controls={open ? `${id}-list` : undefined} aria-autocomplete="list"
        aria-activedescendant={open && active >= 0 && active < shown.length ? optId(active) : undefined} placeholder="+ tool" value={text}
        className="w-48 max-w-full rounded-full bg-white px-3 py-1 font-mono text-xs ring-1 ring-slate-300 dark:bg-slate-900 dark:ring-slate-700"
        onFocus={() => {
          const r = wrap.current?.getBoundingClientRect();
          if (r) {  // the panel's real width, then just enough shift to keep it 16px inside the screen
            const width = Math.min(448, window.innerWidth - 48);
            setShift(Math.max(0, Math.min(r.left + width - (window.innerWidth - 16), r.left - 16)));
          }
          setOpen(true);
        }}
        onChange={(e) => { setText(e.target.value); setActive(-1); setOpen(true); }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setActive((a) => Math.min(a + 1, shown.length - 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, -1)); }
          else if (e.key === "Escape") { setOpen(false); setActive(-1); }
          else if (e.key === "Enter") {
            e.preventDefault();
            if (open && active >= 0 && shown[active]) pick(shown[active].name);  // the highlighted tool
            else if (text.trim()) pick(text.trim());
          }
        }} />
      <button className="btn-ghost text-xs" onClick={() => text.trim() && pick(text.trim())} disabled={!text.trim()}>Add</button>
      {open && (
        <div style={{ left: -shift }} className={`absolute top-full z-20 mt-1 w-[min(28rem,calc(100vw-3rem))] rounded-xl bg-white p-2 shadow-xl ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700`}>
          <ul id={`${id}-list`} role="listbox" aria-label="Tools" className="max-h-72 overflow-auto">
            {groups.map(([server, list]) => (
              <li key={server} role="presentation">
                <div className="px-2 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">{server}</div>
                <ul role="group" aria-label={server}>
                  {list.map((t) => {
                    const i = shown.indexOf(t);
                    return (
                      <li key={`${t.server}/${t.name}`} id={optId(i)} role="option" aria-selected={i === active}
                        className={`cursor-pointer rounded-lg px-2 py-1.5 ${i === active ? "bg-sky-50 dark:bg-sky-500/10" : "hover:bg-slate-50 dark:hover:bg-slate-800"}`}
                        onMouseDown={(e) => e.preventDefault()} onClick={() => pick(t.name)} onMouseEnter={() => setActive(i)}>
                        <div className="flex items-baseline justify-between gap-3">
                          <span className="font-mono text-xs">{t.name}</span>
                          <span className="shrink-0 text-[11px] text-slate-500">{t.runs ? `used in ${t.runs} run${t.runs === 1 ? "" : "s"}` : "not used yet"}</span>
                        </div>
                        <div className="line-clamp-2 text-xs text-slate-500" title={t.description ?? undefined}>
                          {t.description ?? "No description: seen in a run, not in the latest tool list."}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </li>
            ))}
          </ul>
          {shown.length === 0 && (
            <p className="px-2 py-2 text-xs text-slate-500">No tool matches “{text}”.{" "}
              {text.trim() && <button className="text-sky-600 underline" onMouseDown={(e) => e.preventDefault()} onClick={() => pick(text.trim())}>+ add “{text.trim()}”</button>}</p>
          )}
          {errors.map((e) => (
            <p key={`${e.server}`} className="px-2 py-1 text-xs text-amber-700 dark:text-amber-300">
              {e.server === "*" ? e.error : `Couldn't list tools from ${e.server}: ${e.error}`}</p>
          ))}
          <div className="mt-1 flex items-center justify-between gap-2 border-t border-slate-100 px-2 pt-2 text-[11px] text-slate-500 dark:border-slate-800">
            <span>{loading ? "Loading tools…" : refreshing ? "Listing tools from the servers…" : listedAt ? `Listed ${listedAt.slice(0, 16).replace("T", " ")} UTC` : "Not listed yet"}</span>
            <button className="text-sky-600 hover:underline disabled:opacity-50" disabled={refreshing} onMouseDown={(e) => e.preventDefault()} onClick={onRefresh}>Refresh tool list</button>
          </div>
        </div>
      )}
    </div>
  );
}
