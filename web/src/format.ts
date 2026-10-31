// Presentation only: number and label formatting.
export const score = (v: number | null | undefined, digits = 2) => (v === null || v === undefined ? "—" : v.toFixed(digits));
export const money = (v: number | null | undefined, digits = 3) => (v === null || v === undefined ? "—" : `$${v.toFixed(digits)}`);
export const shortModel = (m: string) => m.replace(/^us\.(anthropic|moonshotai|amazon)\./, "").replace(/-v\d+(:\d+)?$/, "");
export const ringColor = (v: number | null | undefined) =>
  v === null || v === undefined ? "stroke-slate-300" : v >= 0.9 ? "stroke-emerald-500" : v >= 0.75 ? "stroke-amber-500" : "stroke-rose-500";
export function clock(startMs: number | null): string {
  if (!startMs) return "";
  const s = Math.max(0, Math.floor((Date.now() - startMs) / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}
