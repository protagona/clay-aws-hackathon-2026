// End-to-end check of the TypeScript UI (increment 9, T3) against a running `foundry ui`, driven over the Chrome
// DevTools Protocol with Node's built-in WebSocket (no npm packages). Needs a headless Chromium: by default the
// Playwright-cached chrome-headless-shell, or CHROME=/path/to/chrome.
//
//   node scripts/ui_e2e.mjs [http://127.0.0.1:8765] [screenshot-dir]
//
// Starts an offline run from the New run dialog, waits for its card in "Needs attention", clicks Evaluate, and
// waits for it to reach "Scored" with a score ring, all without a page reload. Then screenshots the board and the
// drawer in light and dark at 1440 and 375 px.
import { spawn } from "node:child_process";
import { existsSync, readdirSync, writeFileSync, mkdirSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";

const base = process.argv[2] ?? "http://127.0.0.1:8765";
const out = process.argv[3];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function findChrome() {
  if (process.env.CHROME) return process.env.CHROME;
  const cache = join(homedir(), "Library/Caches/ms-playwright");
  if (existsSync(cache)) {
    for (const d of readdirSync(cache).filter((x) => x.startsWith("chromium_headless_shell")).sort().reverse()) {
      for (const sub of readdirSync(join(cache, d))) {
        const p = join(cache, d, sub, "chrome-headless-shell");
        if (existsSync(p)) return p;
      }
    }
  }
  console.error("no headless browser found (set CHROME=/path/to/chrome)");
  process.exit(3);
}

const proc = spawn(findChrome(), ["--headless", "--disable-gpu", "--hide-scrollbars", "--remote-debugging-port=9335",
  `--user-data-dir=${join(tmpdir(), "foundry-e2e")}`, "about:blank"], { stdio: "ignore" });
let targets;
for (let i = 0; i < 50 && !targets; i++) {
  try { targets = await (await fetch("http://127.0.0.1:9335/json")).json(); } catch { await sleep(200); }
}
const ws = new WebSocket(targets.find((t) => t.type === "page").webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
let id = 0; const pending = new Map();
ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const js = async (expr) => {
  const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.error) throw new Error(`CDP: ${r.error.message}`);
  if (r.result.exceptionDetails) throw new Error(`page: ${r.result.exceptionDetails.exception?.description ?? r.result.exceptionDetails.text}`);
  return r.result.result?.value;
};
async function until(expr, what, ms = 120000) {
  const end = Date.now() + ms;
  while (Date.now() < end) { const v = await js(expr); if (v) return v; await sleep(300); }
  throw new Error(`timed out waiting for ${what}`);
}
const fail = (msg) => { console.error(`FAIL: ${msg}`); ws.close(); proc.kill(); process.exit(1); };

// Shorthand an observer can't decode (increment 12, C5). Matched case-insensitively against visible UI text, leaving out
// what agents and the judge wrote (the report, model replies, rubric text, prompt text: [data-content], .report, pre).
const BANNED = [/\bllm_call\b/, /\btool_call\b/, /\bagent_step\b/, /\bstdev\b/, /Δ/, /mean overall/, /live now/, /nothing here/, /judging…/,
  /\bW [01]\./, /prices from/, /\bvariant\b/, /leaderboard/, /missed workflow stages/, /evaluate →/, /evaluate this run/, /not evaluated/,
  /evaluation was interrupted/, /runs \+ evaluations/, /\bevaluating\b/, /\bincomplete\b/];
const BANNED_HEADERS = ["ms", "stdev", "mean", "min", "max", "variant", "#", "δ (b − a)"];
const SCAN = `(() => {
  const root = (document.querySelector('dialog[open]') ?? document.body).cloneNode(true);
  root.querySelectorAll('[data-content], .report, .prose-sm, pre, option, script, style').forEach((n) => n.remove());
  const text = root.textContent.toLowerCase();
  const heads = [...document.querySelectorAll('th')].map((t) => t.textContent.trim().toLowerCase());
  const h1 = [...document.querySelectorAll('main h1')].filter((h) => !h.closest('.report')).length;  // the judge's report has its own
  const caption = !!document.querySelector('main header [data-caption]');
  const bad = [...document.querySelectorAll('main section[aria-labelledby]')].filter((sec) => {
    const h = document.getElementById(sec.getAttribute('aria-labelledby'));
    return !h || h.tagName !== 'H2' || !sec.querySelector('[data-caption]');
  }).map((sec) => sec.getAttribute('aria-labelledby'));
  return { text, heads, h1, caption, bad, overflow: document.documentElement.scrollWidth > window.innerWidth + 1 };
})()`;

async function scan(what, { page = true } = {}) {
  const r = await js(SCAN);
  for (const re of BANNED) { const m = r.text.match(re); if (m) fail(`${what}: shows "${m[0]}"`); }
  const head = r.heads.find((h) => BANNED_HEADERS.includes(h)); if (head !== undefined) fail(`${what}: table header "${head}"`);
  if (page && r.h1 !== 1) fail(`${what}: ${r.h1} page titles (want 1)`);
  if (page && !r.caption) fail(`${what}: no caption under the page title`);
  if (r.bad.length) fail(`${what}: a section without an h2 heading and caption`);
  if (r.overflow) fail(`${what}: horizontal page scroll`);
}

async function clarity(scoredRun) {
  const api = async (path) => { try { const r = await fetch(base + path); return r.ok ? r.json() : null; } catch { return null; } };
  const batch = (await api("/api/batches"))?.batches?.[0]?.id;
  // A rubric draft to visit: upload the active rubric as JSON (no model call), discard it afterwards.
  let draft = null;
  try {
    const csrf = (await api("/api/session")).csrf;
    const active = (await api("/api/rubric/active?task=adb")).rubric;
    const form = new FormData(); form.append("task", "adb"); form.append("kind", "file"); form.append("config", "offline");
    form.append("file", new Blob([JSON.stringify(active)], { type: "application/json" }), "rubric.json");
    const r = await fetch(base + "/api/rubric/drafts", { method: "POST", body: form, headers: { "x-csrf-token": csrf, origin: base } });
    if (r.ok) draft = { id: (await r.json()).draft_id, csrf };
  } catch { /* the draft page is then skipped */ }
  const rub = await api("/api/rubric?task=adb");
  const routes = [["board", "/"], ["batches", "/batches"], ["dashboard", "/dashboard"], ["rubric", "/rubric"], ["active rubric", "/rubric/active"],
    ["run's rubric", `/rubric/runs/${encodeURIComponent(scoredRun)}`], ["import", "/import"], ["compare (empty)", "/compare"],
    ["compare", `/compare?a=${encodeURIComponent(scoredRun)}&b=${encodeURIComponent(scoredRun)}`], ["not found", "/nope"],
    ["prompts", "/prompts"], ["a prompt", "/prompts/adb-001"], ["new prompt", "/prompts/new"]];  // increment 13 (views only)
  const opt = [["batch", batch && `/batches/${batch}`], ["history version", rub?.versions?.[0]?.file && `/rubric/history/${rub.versions[0].file}`],
    ["rubric draft", rub?.drafts?.[0]?.draft_id && `/rubric/drafts/${rub.drafts[0].draft_id}`]];
  for (const [name, path] of opt) if (path) routes.push([name, path]); else console.log(`SKIP ${name}: none available`);
  const tabs = ["Overview", "Timeline", "Steps", "Report", "Rubric"];
  for (const [w, dark] of [[1440, false], [1440, true], [375, false], [375, true]]) {
    await send("Emulation.setDeviceMetricsOverride", { width: w, height: 900, deviceScaleFactor: 1, mobile: w < 600 });
    await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: dark ? "dark" : "light" }] });
    const at = `${w}px ${dark ? "dark" : "light"}`;
    for (const [name, path] of routes) {
      await send("Page.navigate", { url: base + path });
      await until("!!document.querySelector('main h1') && !document.querySelector('.animate-pulse')", `${name} to load`, 20000);
      await sleep(300);
      await scan(`${name} (${at})`);
    }
    await send("Page.navigate", { url: `${base}/runs/${encodeURIComponent(scoredRun)}` });
    await until(`!!document.querySelector('aside[aria-label="Run details"] [role=tab]')`, "the run panel", 20000);
    for (const t of tabs) {
      await js(`[...document.querySelectorAll('aside [role=tab]')].find(x => x.textContent.startsWith('${t}')).click()`);
      await sleep(500);
      await scan(`run panel ${t} tab (${at})`);
      const cap = await js(`!!document.querySelector('aside [data-caption]')`);
      if (!cap) fail(`run panel ${t} tab (${at}): no caption`);
    }
    await js("[...document.querySelectorAll('button')].find(b => b.textContent.includes('New run')).click()");
    await until("!!document.querySelector('dialog[open]')", "the New run dialog");
    await scan(`New run dialog (${at})`, { page: false });
    if (!(await js("!!document.querySelector('dialog[open] [data-caption]')"))) fail(`New run dialog (${at}): no caption`);
  }
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await send("Emulation.setEmulatedMedia", { features: [] });
  if (draft) await fetch(`${base}/api/rubric/drafts/${draft.id}?task=adb`, { method: "DELETE", headers: { "x-csrf-token": draft.csrf, origin: base } });
  console.log(`clarity: ${routes.length} pages, the run panel's ${tabs.length} tabs and the New run dialog pass at 1440 and 375 px, light and dark`);
}

try {
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: base + "/" });
  await until("!!document.querySelector('[aria-label=\"Key numbers\"]') && !document.querySelector('.animate-pulse')", "the board");
  await js("window.__marker = 'no-reload'");
  const before = await js("[...document.querySelectorAll('section[aria-label] li a')].map(a => a.getAttribute('href'))");

  // New run dialog: offline config, first prompt.
  await js("[...document.querySelectorAll('button')].find(b => b.textContent.includes('New run')).click()");
  await until("!!document.querySelector('dialog[open]')", "the New run dialog");
  await js(`(() => { const s = document.querySelectorAll('dialog[open] select')[1];
    const set = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set; set.call(s, 'offline');
    s.dispatchEvent(new Event('change', { bubbles: true })); })()`);
  await js("[...document.querySelectorAll('dialog[open] button')].find(b => b.textContent.includes('Start run')).click()");
  const runId = await until(`(() => { const m = location.pathname.match(/^\\/runs\\/(.+)$/); return m && decodeURIComponent(m[1]); })()`, "navigation to the new run");
  if (before.includes(`/runs/${encodeURIComponent(runId)}`)) fail("the run existed before");
  console.log(`started ${runId}`);
  const card = (col) => `!!document.querySelector('section[aria-label="${col}"] a[href="/runs/${encodeURIComponent(runId)}"]')`;
  await until(card("Needs attention"), "the card in Needs attention (not evaluated)");
  console.log("card is in Needs attention");
  // Increment 12: the unscored states read in plain words too (the card's button, the drawer's "Not scored yet").
  const btn = await js(`document.querySelector('section[aria-label="Needs attention"] a[href="/runs/${encodeURIComponent(runId)}"]').closest('li').querySelector('button')?.textContent`);
  if (btn !== "Score it →") fail(`the unscored card's button reads "${btn}"`);
  await until(`[...document.querySelectorAll('aside[aria-label="Run details"] button')].some(b => b.textContent === 'Score this run')`, "Score this run in the drawer", 20000);
  await scan("unscored run on the board and in the run panel");
  await js(`document.querySelector('section[aria-label="Needs attention"] a[href="/runs/${encodeURIComponent(runId)}"]').closest('li').querySelector('button').click()`);
  await until(card("Scored"), "the card in Scored");
  const ring = await js(`document.querySelector('section[aria-label="Scored"] a[href="/runs/${encodeURIComponent(runId)}"] [role=img]')?.getAttribute('aria-label')`);
  if (!ring || !/overall \.?\d/.test(ring)) fail(`no score ring (${ring})`);
  // The open drawer must switch to the scored state on its own (verify finding: it went stale).
  await until(`!!document.querySelector('aside[aria-label="Run details"] [role=img][aria-label^="overall"]')`, "the drawer's score rings", 30000);
  console.log("drawer shows the scores");
  if ((await js("window.__marker")) !== "no-reload") fail("the page reloaded");
  console.log(`scored: ${ring}; no reload`);
  // Increment 10: the drawer's Rubric tab shows the rubric that scored the run, with the judge's scores.
  await js(`[...document.querySelectorAll('aside[aria-label="Run details"] [role=tab]')].find(t => t.textContent === 'Rubric').click()`);
  const rub = await until(`(() => { const l = document.querySelectorAll('aside[aria-label="Run details"] ol[aria-label$=" levels"]');
    const s = [...document.querySelectorAll('aside[aria-label="Run details"] span')].filter(x => /^scored [0-9]/.test(x.textContent));
    return l.length && s.length ? { lists: l.length, scored: s.length } : null; })()`, "the Rubric tab's criteria and scores", 20000);
  console.log(`rubric tab: ${rub.lists} criteria, ${rub.scored} scored`);
  if (out) {
    mkdirSync(out, { recursive: true });
    const shot = await send("Page.captureScreenshot", { format: "png" });
    writeFileSync(join(out, "drawer-rubric-tab-light-1440.png"), Buffer.from(shot.result.data, "base64"));
  }

  // Increment 14: the TF logo and tab icon follow the theme (charcoal on light, cyan on dark).
  for (const dark of [false, true]) {
    await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: dark ? "dark" : "light" }] });
    await js("localStorage.removeItem('theme')");
    await send("Page.navigate", { url: base + "/" });
    await until("!!document.querySelector('main h1')", "the board");
    const seen = await js(`[...document.querySelectorAll('header img')].filter(i => getComputedStyle(i).display !== 'none' && i.complete && i.naturalWidth > 0).map(i => i.getAttribute('src'))`);
    const want = `/assets/brand/logo-${dark ? "dark" : "light"}-v1.png`;
    if (seen.length !== 1 || seen[0] !== want) fail(`header logo in ${dark ? "dark" : "light"}: ${JSON.stringify(seen)}`);
    const icons = await js(`[...document.querySelectorAll('link[rel=icon]')].map(l => l.getAttribute('href'))`);
    if (!icons.length || !icons.every((h) => h.includes(`favicon-${dark ? "dark" : "light"}-`))) fail(`tab icon in ${dark ? "dark" : "light"}: ${icons}`);
  }
  await js(`document.querySelector('header button[aria-label^="Switch to"]').click()`);  // the app's own toggle, from dark
  await sleep(200);
  const flipped = await js(`[...document.querySelectorAll('link[rel=icon]')].every(l => l.getAttribute('href').includes('favicon-light-'))`);
  if (!flipped) fail("the tab icon didn't follow the theme toggle");
  await js("localStorage.removeItem('theme')");
  await send("Emulation.setEmulatedMedia", { features: [] });
  console.log("logo and tab icon follow the theme");

  // Increment 12: every page explains itself (C1, C2, C5, C8), at phone and desktop width, light and dark.
  await clarity(runId);

  if (out) {
    mkdirSync(out, { recursive: true });
    for (const [w, dark] of [[1440, false], [1440, true], [375, false], [375, true]]) {
      await send("Emulation.setDeviceMetricsOverride", { width: w, height: w < 600 ? 1600 : 1000, deviceScaleFactor: 1, mobile: w < 600 });
      await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: dark ? "dark" : "light" }] });
      for (const [name, path] of [["board", "/"], ["drawer", `/runs/${encodeURIComponent(runId)}`], ["dashboard", "/dashboard"], ["rubric", "/rubric"]]) {
        await js("localStorage.removeItem('theme')");
        await send("Page.navigate", { url: base + path });
        await sleep(1800);
        const overflow = await js("document.documentElement.scrollWidth > window.innerWidth + 1");
        if (overflow) fail(`horizontal page scroll on ${name} at ${w}px`);
        if (name === "drawer") {  // the drawer's close button must be on top, not under the header (verify finding)
          const ok = await js(`(() => { const b = document.querySelector('aside[aria-label="Run details"] a[aria-label="Close run details"]');
            if (!b) return false; const r = b.getBoundingClientRect(); const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
            return b.contains(hit); })()`);
          if (!ok) fail(`the drawer's close button is covered at ${w}px`);
        }
        const shot = await send("Page.captureScreenshot", { format: "png" });
        writeFileSync(join(out, `${name}-${dark ? "dark" : "light"}-${w}.png`), Buffer.from(shot.result.data, "base64"));
      }
    }
    await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
    await send("Page.navigate", { url: base + "/" });
    await sleep(1500);
    const anim = await js("getComputedStyle(document.querySelector('.card-in')).animationName");
    console.log(`reduced motion: card animation = ${anim}`);
    if (anim !== "none") fail("animations still run under prefers-reduced-motion");
    console.log(`screenshots in ${out}`);
  }
  console.log("PASS");
} catch (e) {
  fail(e.message);
}
ws.close();
proc.kill();
