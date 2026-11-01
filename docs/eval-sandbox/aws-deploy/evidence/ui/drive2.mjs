// The deployed web app in headless Chrome over CDP, every main path: sign in, New run, its panel and tabs, scoring
// from the panel, Compare, prompts and the tool picker, batches, dashboard, and a rubric draft from the page.
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
const [out, base, token] = [process.argv[2], process.argv[3].replace(/\/$/, ""), process.env.FOUNDRY_TOKEN];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const proc = spawn(process.env.CHROME, ["--headless", "--disable-gpu", "--hide-scrollbars", "--remote-debugging-port=9338",
  `--user-data-dir=${mkdtempSync(join(tmpdir(), "cloudui-"))}`, "about:blank"], { stdio: "ignore" });
let targets;
for (let i = 0; i < 150 && !targets; i++) { try { targets = await (await fetch("http://127.0.0.1:9338/json")).json(); } catch { await sleep(200); } }
const ws = new WebSocket(targets.find((t) => t.type === "page").webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
let id = 0; const pending = new Map(); const problems = [];
ws.onmessage = (e) => { const m = JSON.parse(e.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  if (m.method === "Runtime.exceptionThrown") problems.push(m.params.exceptionDetails.text);
  if (m.method === "Log.entryAdded" && m.params.entry.level === "error") problems.push(m.params.entry.text + " " + (m.params.entry.url || "")); };
const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const js = async (e) => { const r = await send("Runtime.evaluate", { expression: e, returnByValue: true, awaitPromise: true });
  if (r.result.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description); return r.result.result?.value; };
async function until(e, what, ms = 30000) { const end = Date.now() + ms; while (Date.now() < end) { const v = await js(e); if (v) return v; await sleep(300); } throw new Error(`timed out: ${what}`); }
const shot = async (n) => writeFileSync(`${out}/${n}.png`, Buffer.from((await send("Page.captureScreenshot")).result.data, "base64"));
const go = async (path, ready, what) => { await send("Page.navigate", { url: base + path }); return until(ready, what); };
const log = []; const say = (...a) => { const l = a.join(" "); log.push(l); console.log(l); };
const settle = "!!document.querySelector('main h1') && !document.querySelector('.animate-pulse')";
const click = (label, scope = "document") => js(`[...${scope}.querySelectorAll('button')].find(b => b.textContent.trim().startsWith(${JSON.stringify(label)})).click()`);
const panel = `document.querySelector('aside[aria-label="Run details"]')`;
try {
  await send("Page.enable"); await send("Runtime.enable"); await send("Log.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await go("/", "!!document.querySelector('input[type=password]')", "the sign-in page");
  await js(`(() => { const i = document.querySelector('input[type=password]'); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(i, ${JSON.stringify(token)}); i.dispatchEvent(new Event('input', { bubbles: true })); document.querySelector('form button').click(); })()`);
  say("signed in:", await until("[...document.querySelectorAll('header span')].find((s) => s.textContent.startsWith('Signed in as'))?.textContent", "the header"));
  say("board cards:", await until("document.querySelectorAll('section li a[href^=\"/runs/\"]').length", "cards"));
  // New run from the dialog (offline config: no model cost); its panel opens and follows it live.
  await click("▶ New run");
  await until("!!document.querySelector('dialog[open]')", "the New run dialog");
  await js(`(() => { const s = [...document.querySelectorAll('dialog[open] select')].find(x => [...x.options].some(o => o.value === 'offline'));
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(s, 'offline'); s.dispatchEvent(new Event('change', { bubbles: true })); })()`);
  await click("Start run", "document.querySelector('dialog[open]')");
  const runPath = await until("location.pathname.startsWith('/runs/') && location.pathname", "the new run's panel", 60000);
  say("New run started from the dialog:", runPath);
  await until(`(${panel}?.textContent || '').includes('Score this run')`, "the run to finish in its panel", 180000);
  say("its panel followed it to the end (Score this run offered)");
  for (const tab of ["Timeline", "Steps", "Report", "Rubric", "Overview"]) {
    await js(`[...document.querySelectorAll('aside [role=tab]')].find(x => x.textContent.startsWith('${tab}')).click()`);
    await sleep(800);
  }
  say("run panel tabs opened: Timeline, Steps, Report, Rubric, Overview");
  await shot("run-panel");
  await js(`[...${panel}.querySelectorAll('button')].find(b => b.textContent === 'Score this run').click()`);
  say("scored from the panel:", await until(`(() => { const r = ${panel}?.querySelector('[role=img][aria-label^="overall"]'); return r && r.getAttribute('aria-label'); })()`, "a score ring in the panel", 240000));
  await shot("scored");
  const rid = decodeURIComponent(runPath.split("/").pop());
  await go(`/compare?a=${encodeURIComponent(rid)}&b=${encodeURIComponent(rid)}`, settle, "Compare");
  say("compare rows:", await until("document.querySelectorAll('table tbody tr').length", "compare rows"));
  await go("/prompts", settle, "Prompts");
  say("prompts listed:", await until("document.querySelectorAll('main a[href^=\"/prompts/\"]').length", "prompts"));
  await go("/prompts/adb-001", settle, "a prompt");
  await js("(() => { const c = document.querySelector('input[role=combobox]'); c.focus(); c.dispatchEvent(new FocusEvent('focus')); })()");
  say("tool picker options:", await until("document.querySelectorAll('[role=listbox] [role=option]').length", "the tool picker's options", 60000));
  await shot("prompt");
  await go("/batches", settle, "Batches");
  await go("/dashboard", settle, "Dashboard");
  await shot("dashboard");
  // A rubric draft from the page (Text, the Bedrock judge), then discarded.
  await go("/rubric", settle + " && [...document.querySelectorAll('select option')].some(o => o.value === 'bedrock')", "Rubric");
  await js(`[...document.querySelectorAll('[role=tab]')].find(x => x.textContent === 'Text').click()`);
  await sleep(300);
  await js(`(() => { const t = document.querySelector('main form textarea'); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(t, 'Score a dataset plan 1 to 5 on whether its imagery is real and openly available, and 1 to 5 on whether its resolution fits the task.'); t.dispatchEvent(new Event('input', { bubbles: true })); })()`);
  const t0 = Date.now();
  await click("Draft rubric");
  const draft = await until("location.pathname.startsWith('/rubric/drafts/') && location.pathname", "the draft", 300000);
  const criteria = await until("document.querySelectorAll('fieldset').length", "criteria");
  say("drafted from the page in", ((Date.now() - t0) / 1000).toFixed(0), "s:", draft, "·", criteria, "criteria");
  await shot("draft");
  await click("Discard draft");
  await until("location.pathname === '/rubric'", "back on Rubric");
  say("draft discarded");
  // Expected network errors only: the gate's own 401 before sign-in, and the new run's 404 until its worker starts
  // (the panel says "Starting the run…" then).
  const expected = (p) => /401.*\/api\/session$/.test(p) || p.includes(`404`) && p.endsWith(`/api/runs/${encodeURIComponent(rid)}`);
  const real = problems.filter((p) => !expected(p));
  say(real.length ? "page errors: " + real.join(" | ") : `no page errors beyond the expected ones (${problems.length - real.length}: the gate's 401, the starting run's 404s)`);
  say(real.length ? "FAIL" : "PASS");
} catch (e) { say("FAIL", e.message, problems.join(" | ")); await shot("fail"); } finally { writeFileSync(`${out}/drive2.log`, log.join("\n") + "\n"); ws.close(); proc.kill(); }
