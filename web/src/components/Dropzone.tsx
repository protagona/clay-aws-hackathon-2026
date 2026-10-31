import { useRef, useState } from "react";

export function Dropzone({ accept, file, onFile, hint }: { accept: string; file: File | null; onFile: (f: File | null) => void; hint: string }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); onFile(e.dataTransfer.files[0] ?? null); }}
      className={`flex flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed p-6 text-center text-sm transition ${over ? "border-sky-500 bg-sky-50 dark:bg-sky-500/10" : "border-slate-300 dark:border-slate-700"}`}>
      <span aria-hidden="true" className="text-2xl">⇪</span>
      {file ? <span className="font-medium">{file.name} <span className="text-slate-500">({(file.size / 1024).toFixed(0)} KB)</span></span>
        : <span>Drag a file here, or</span>}
      <button type="button" className="btn-ghost" onClick={() => input.current?.click()}>{file ? "Choose another file" : "Choose a file"}</button>
      <input ref={input} type="file" accept={accept} className="sr-only" aria-label="File" onChange={(e) => onFile(e.target.files?.[0] ?? null)} />
      <span className="text-xs text-slate-500">{hint}</span>
    </div>
  );
}
