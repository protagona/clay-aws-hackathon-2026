// End-to-end check of the Prompts pages (increment 13, P13) on a throwaway copy of the project: never the real files.
//
//   node scripts/ui_e2e_prompts.mjs
//
// Copies tasks/ and config/ into a temp dir, starts its own `foundry ui` there (FOUNDRY_HOME, port 8799, temp runs
// dir), checks the server really serves that copy, then in headless Chromium: creates a prompt, sees its card, sets
// two expected steps, tries to run it on the offline config (refused: no script), archives it and checks it left the
// New run picker. Pages are checked for a title, caption and no sideways scroll at 1440 and 375 px.
import { spawn } from "node:child_process";
import { cpSync, existsSync, mkdtempSync, readdirSync, rmSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { basename, join } from "node:path";

const repo = new URL("..", import.meta.url).pathname;
const base = "http://127.0.0.1:8799";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const home = mkdtempSync(join(tmpdir(), "foundry-prompts-e2e-"));
cpSync(join(repo, "tasks"), join(home, "tasks"), { recursive: true });
cpSync(join(repo, "config"), join(home, "config"), { recursive: true });

function findChrome() {
  if (process.env.CHROME) return process.env.CHROME;
  const cache = join(homedir(), "Library/Caches/ms-playwright");
  for (const d of existsSync(cache) ? readdirSync(cache).filter((x) => x.startsWith("chromium_headless_shell")).sort().reverse() : []) {
    for (const sub of readdirSync(join(cache, d))) { const p = join(cache, d, sub, "chrome-headless-shell"); if (existsSync(p)) return p; }
  }
  console.error("no headless browser found (set CHROME=/path/to/chrome)"); process.exit(3);
}

const server = spawn("uv", ["run", "foundry", "ui", "--port", "8799", "--runs-dir", join(home, "runs")],
  { cwd: repo, env: { ...process.env, FOUNDRY_HOME: home }, stdio: "ignore" });
let proc, ws;
const exited = (p) => (!p || p.exitCode !== null ? Promise.resolve() : Promise.race([new Promise((r) => p.once("exit", r)), sleep(5000)]));
const done = async (code) => {  // wait for the browser and server to exit before deleting the temp copy they write in
  try { ws?.close(); } catch { /* closed */ }
  proc?.kill(); server.kill();
  await Promise.all([exited(proc), exited(server)]);
  rmSync(home, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  process.exit(code);
};
const fail = (msg) => { throw new Error(msg); };  // a throw, so nothing after a failed check (the root guard) ever runs

try {
  let up = null;
  for (let i = 0; i < 120 && !up; i++) { try { up = await (await fetch(`${base}/api/prompts?task=adb`)).json(); } catch { await sleep(500); } }
  if (!up) fail("the server didn't start");
  if (up.root_name !== basename(home)) fail(`the server serves "${up.root_name}", not the temp copy; refusing to write`);
  console.log(`server on a temp copy (${basename(home)})`);

  proc = spawn(findChrome(), ["--headless", "--disable-gpu", "--hide-scrollbars", "--remote-debugging-port=9339",
    `--user-data-dir=${join(home, "chrome")}`, "about:blank"], { stdio: "ignore" });
  let targets;
  for (let i = 0; i < 50 && !targets; i++) { try { targets = await (await fetch("http://127.0.0.1:9339/json")).json(); } catch { await sleep(200); } }
  ws = new WebSocket(targets.find((t) => t.type === "page").webSocketDebuggerUrl);
  await new Promise((r) => (ws.onopen = r));
  let id = 0; const pending = new Map();
  ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
  const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
  const js = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.result.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description ?? r.result.exceptionDetails.text);
    return r.result.result?.value;
  };
  const until = async (expr, what, ms = 20000) => { const end = Date.now() + ms; while (Date.now() < end) { const v = await js(expr); if (v) return v; await sleep(250); } fail(`timed out waiting for ${what}`); };
  const type = (sel, value) => js(`(() => { const el = document.querySelector(${JSON.stringify(sel)});
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : el.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, ${JSON.stringify(value)});
    el.dispatchEvent(new Event(el.tagName === "SELECT" ? "change" : "input", { bubbles: true })); })()`);
  const click = (text, scope = "document") => js(`[...${scope}.querySelectorAll('button, a')].find(b => b.textContent.trim() === ${JSON.stringify(text)}).click()`);
  const go = async (path) => { await send("Page.navigate", { url: base + path }); await until("!!document.querySelector('main h1')", path); await sleep(300); };
  const page = async (what) => {
    const r = await js(`({ h1: document.querySelectorAll('main h1').length, cap: !!document.querySelector('main header [data-caption]'),
      over: document.documentElement.scrollWidth > window.innerWidth + 1 })`);
    if (r.h1 !== 1 || !r.cap) fail(`${what}: needs one title and a caption`);
    if (r.over) fail(`${what}: sideways scroll`);
  };
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });

  // 1. Create a prompt.
  await go("/prompts/new");
  const newId = await until(`document.querySelector('main input').value`, "the suggested id");
  if (newId !== "adb-007") fail(`suggested id ${newId}`);
  await type("main textarea", "Map mangrove loss along Indonesia's coast since 2015 from open imagery, and write a build plan.");
  await click("Create");
  await until(`location.pathname === "/prompts/adb-007"`, "the new prompt's page");
  console.log("created adb-007");

  // 2. Its card is on the Prompts page.
  await go("/prompts");
  await until(`!!document.querySelector('section a[href="/prompts/adb-007"]')`, "adb-007's card");
  await page("prompts page (1440px)");
  console.log("card shown");

  // 3. Two expected steps.
  await go("/prompts/adb-007");
  await click("+ Add step"); await click("+ Add step");
  await type('input[aria-label="Add a tool to step 1"]', "search_datasets"); await click("Add");
  await type('input[aria-label="Add a tool to step 2"]', "roda/get_dataset_details");
  await js(`[...document.querySelectorAll('button')].filter(b => b.textContent === 'Add')[1].click()`);
  await click("Save steps");
  await until(`![...document.querySelectorAll('span')].some(s => s.textContent === 'Unsaved changes')`, "steps saved");
  const saved = await (await fetch(`${base}/api/prompts/adb-007?task=adb`)).json();
  if (JSON.stringify(saved.prompt.stages) !== JSON.stringify([["search_datasets"], ["roda/get_dataset_details"]])) fail(`steps ${JSON.stringify(saved.prompt.stages)}`);
  await page("prompt page (1440px)");
  console.log("steps saved");

  // 4. The offline config can't run it: refused with a clear message in the New run dialog.
  await click("▶ New run");
  await until("!!document.querySelector('dialog[open]')", "the New run dialog");
  await type("dialog[open] select", "adb-007");
  await js(`(() => { const s = document.querySelectorAll('dialog[open] select')[1]; Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(s, 'offline'); s.dispatchEvent(new Event('change', { bubbles: true })); })()`);
  await click("Start run", "document.querySelector('dialog[open]')");
  const msg = await until(`document.querySelector('dialog[open] [role=alert]')?.textContent`, "the refusal");
  if (!msg.includes("has no script for adb-007")) fail(`refusal reads "${msg}"`);
  await click("Cancel", "document.querySelector('dialog[open]')");
  console.log("offline run refused: no script");

  // 5. Archive it: it leaves the New run picker, and shows under Archived.
  await click("Archive this prompt");
  await until(`[...document.querySelectorAll('button')].some(b => b.textContent === 'Unarchive')`, "archived");
  await click("▶ New run");
  await until("!!document.querySelector('dialog[open]')", "the New run dialog");
  const options = await js(`[...document.querySelectorAll('dialog[open] select')[0].options].map(o => o.value)`);
  if (options.includes("adb-007")) fail("the archived prompt is still offered in New run");
  await go("/prompts");
  await until(`!!document.querySelector('section[aria-labelledby] a[href="/prompts/adb-007"]') && [...document.querySelectorAll('h2')].some(h => h.textContent === 'Archived')`, "the Archived section");
  console.log("archived: gone from New run, listed under Archived");

  // Phone width.
  await send("Emulation.setDeviceMetricsOverride", { width: 375, height: 900, deviceScaleFactor: 1, mobile: true });
  for (const path of ["/prompts", "/prompts/adb-007", "/prompts/new"]) { await go(path); await page(`${path} (375px)`); }
  console.log("PASS");
  await done(0);
} catch (e) {
  console.error(`FAIL: ${e.message}`);
  await done(1);
}
