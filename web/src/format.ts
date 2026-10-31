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

// Increment 12: plain words for the shorthand the API and traces use.
const STEP_KIND: Record<string, string> = { llm_call: "model call", tool_call: "tool call", agent_step: "agent", note: "note" };
export const stepKind = (type: string) => STEP_KIND[type] ?? type;
const STATUS_WORD: Record<string, string> = {
  "not evaluated": "not scored yet", evaluating: "being scored", incomplete: "partly scored", timeout: "timed out",
};
export const statusWord = (s: string) => STATUS_WORD[s] ?? s;
/** "1.9 s" from milliseconds. */
export const seconds = (ms: number | null | undefined) => (ms === null || ms === undefined ? "—" : `${(ms / 1000).toFixed(1)} s`);
/** A score that may arrive as a string (batch CSV rows): two decimals, or "—". */
export const score2 = (v: string | number | null | undefined) => {
  const n = typeof v === "number" ? v : v === null || v === undefined || v === "" ? NaN : Number(v);
  return Number.isFinite(n) ? n.toFixed(2) : "—";
};
/** A bare hyphenated id like "data-fit" or "completion" -> "Data fit", "Completion"; any "a|b|c" list -> "a, b or c". */
export function pretty(label: string): string {
  if (/^[a-z0-9]+(-[a-z0-9]+)*$/.test(label)) {  // hyphenated ids only: snake_case tool names stay as they are
    const spaced = label.replace(/-/g, " ");
    return spaced.charAt(0).toUpperCase() + spaced.slice(1);
  }
  return label.replace(/[^\s()|]+(\|[^\s()|]+)+/g, (m) => orList(m.split("|")));
}
const orList = (xs: string[]) => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} or ${xs[xs.length - 1]}`);
/** Compare's difference colour: green when B improved on A, red when worse, grey when equal or neither way is better. */
export function deltaTone(delta: string, better: "higher" | "lower" | null | undefined): "good" | "bad" | "none" {
  const up = delta.startsWith("+"), down = delta.startsWith("−");  // the server's minus is U+2212
  if (!better || (!up && !down)) return "none";
  return (up === (better === "higher")) ? "good" : "bad";
}
