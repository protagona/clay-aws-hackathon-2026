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
  await js(`document.querySelector('section[aria-label="Needs attention"] a[href="/runs/${encodeURIComponent(runId)}"]').closest('li').querySelector('button').click()`);
  await until(card("Scored"), "the card in Scored");
  const ring = await js(`document.querySelector('section[aria-label="Scored"] a[href="/runs/${encodeURIComponent(runId)}"] [role=img]')?.getAttribute('aria-label')`);
  if (!ring || !/overall \.?\d/.test(ring)) fail(`no score ring (${ring})`);
  // The open drawer must switch to the scored state on its own (verify finding: it went stale).
  await until(`!!document.querySelector('aside[aria-label="Run details"] [role=img][aria-label^="overall"]')`, "the drawer's score rings", 30000);
  console.log("drawer shows the scores");
  if ((await js("window.__marker")) !== "no-reload") fail("the page reloaded");
  console.log(`scored: ${ring}; no reload`);

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
        if (overflow) console.log(`WARN: horizontal page scroll on ${name} at ${w}px`);
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
