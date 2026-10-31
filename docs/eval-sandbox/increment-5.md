# Increment 5: live run monitor

**Track:** Quick   **Status:** Built (verified 2026-10-30)   **Date:** 2026-10-30

## Problem

Someone watching a run in the UI sees a plain table that reloads the whole page every 3 seconds. They can't tell how close the run is to its limits, which tools ran in parallel, or where the time went. Once a run is evaluated, its timeline disappears from the page behind the report. In a demo, the most interesting minute (the agent working) is also the least readable one.

## Acceptance criteria

**L: live updates**
- **L1:** While a run is `running` or being evaluated, the run page updates in place about once a second without a full page reload. A clock shows the elapsed time since `started_at`, ticking every second.
- **L2:** `GET /runs/{run_id}/live?seen=N` returns the monitor section as an HTML fragment, rendered by the same Jinja template as the full page. Steps numbered above `N` carry a `new` class, so they animate in. It returns 404 for any id the run page would refuse (invalid, traversal, symlink, missing `run.json`).
- **L3:** When the status leaves `running`, or an evaluation finishes, the page reloads once to show the final state (scores and report). Polling stops on a finished, idle run.
- **L4:** With scripts disabled, the page still renders everything and falls back to a 3-second meta refresh while live (`<noscript>`).
- **L5:** Updating never puts run data into the DOM unescaped. The script inserts only server-rendered, autoescaped fragments from the same origin, and it builds nothing from JSON.

**M: monitor content (the approved design, option A "Mission control")**
- **M1, header:** prompt id, model, a status pill (pulsing dot while running; ok, failed, timeout or evaluating colours), the elapsed or total duration, the run id, and the existing "Compare with" form.
- **M2, scores:** when evaluated, three score rings (Overall, Workflow, Output), each with its number printed and filled to the score.
- **M3, gauges:** model calls against `limits.max_model_calls`, elapsed seconds against `limits.max_seconds`, and input tokens against `limits.max_input_tokens`. Each shows "n / max" and a bar that turns amber at 75% and red at 100%. A missing limit shows the count without a bar. Next to them: cost so far, output tokens, tool calls as ok/error counts, and calls per server.
- **M4, waterfall:** one row per `llm_call` and `tool_call` step, positioned by `started_at` relative to the run start and sized by `duration_ms`, with a time axis. Colours separate model calls, tool calls and errors, so parallel tool calls show as overlapping bars. While running, a striped "working…" row runs from the end of the last step to now. Each bar has a text label and a hover title (name, status, ms).
- **M5, feed:** the steps newest first. Model steps show their text, tokens and cost; tool steps show server/name, status, ms, and an expandable `<details>` with args, the result preview and the error. Errors are visually marked. Notes (limits, agent errors) appear too.
- **M6, after evaluation:** order is header → scores → gauges → waterfall → report → feed. The feed is collapsed into a `<details>` "All steps (n)". The Evaluate button and the interrupted and evaluating notes keep their current behaviour.

**S: security**
- **S1:** The CSP gains only `script-src 'self'` and `connect-src 'self'`. `default-src 'none'`, `frame-ancestors 'none'` and `form-action 'self'` stay. The script is a static file served by the app from an allowlisted path, not an inline script.
- **S2:** The live endpoint is GET-only, reads only inside `runs/`, and goes through the same Host guard.

**Q: quality**
- **Q1:** Works at 375 px width without horizontal page scroll (the waterfall scrolls inside its own box), and in light and dark themes. Animations are off under `prefers-reduced-motion`.
- **Q2:** Status is never conveyed by colour alone: the pill has text, and error bars and feed items carry an ✗ and the word "error".

## Non-goals

- No websockets or server-sent events. Polling a fragment is enough on localhost.
- No changes to the batch, compare or index pages, though they reuse the base styles.
- No new Python or JS dependencies, and no frameworks or CDN.
- No streaming of model tokens mid-call: steps appear when the trace records them, at the end of each call.

## Assumptions and open questions

- **Steps are written to `trace.jsonl` as each finishes** (the JSONL tracer), so polling the file shows progress. Imported or receiver runs arrive whole, so they show final state only. Owner: the team. Default: accepted.
- **Input-token gauge:** `max_input_tokens` is cumulative across model calls (`harness/hooks.py:76`), so the gauge sums `tokens_in`.

## Plan

**Design review:**
- **Approved by the user (2026-10-30):** option A, "Mission control", then the rendered result, live on a preview server during a real Bedrock run.
- **Deviation from the wireframe:** the status pill and clock sit on their own bar under the header, not inside it, so the per-second update doesn't reset the "Compare with" select. The user saw this before approving.
- **Alternatives not chosen:** B, "Conversation + sidebar" (chat bubbles and a sticky gauge sidebar, with no timing picture); C, "Static" (no script, CSS waterfall, 3-second full reloads).

**Slices:**
1. Template split: `_monitor.html` (gauges, waterfall, feed, scores) included by `run.html`, plus a `_monitor_ctx()` helper that computes gauges and waterfall geometry from `steps` and `run`. Unit tests with fixture traces.
2. `GET /runs/{id}/live`, `/static/monitor.js`, CSP update, `<noscript>` fallback. UI tests for the fragment, 404s, CSP header and static path allowlist.
3. Styles (tokens in `base.html`), reduced motion, and phone width.
4. Verify against a live Bedrock run in the real UI, then update the README and handoff.

## Verification (2026-10-30)

**Suite:** 212 passed (12 new), ruff clean, and gitleaks found no leaks. There are no new dependencies.

| AC | How it was proven |
|---|---|
| L1, L3 | **Real browser:** headless Chromium (Playwright's cached headless shell, driven over CDP by a Node script with no packages) watched live Bedrock run `adb-004` for 40 s. `data-seen` advanced 0→1→5→11→13→15→18→20 with the same document (a `window` marker survived every swap), and the clock ticked each second. When the run finished, the page reloaded exactly once and polling stopped (`data-live="0"`). |
| L2 | `test_live_fragment_and_script_only_while_live`: the fragment is a bare `<section id="monitor">`, and only steps above `seen` are `new`. `test_live_fragment_bad_ids_404` covers 4 bad ids. |
| L4 | The `<noscript>` meta refresh is present only while live (same test, plus the existing `test_auto_refresh_only_while_in_progress`). |
| L5 | `test_live_fragment_renders_untrusted_content_inert`: the hostile fixture's `<script>` arguments and `<b>` error text come out escaped. The script contains no `innerHTML` or `eval`. |
| M1–M6 | `tests/test_monitor.py` (5 tests: gauge levels and limits, missing limits, waterfall geometry with parallel calls, the working bar, and untrusted fields). The rings are asserted in `test_report_page_and_unevaluated_page`. Screenshots cover the evaluating state, the final state, and dark mode at 375 px. |
| S1, S2 | `test_csp_and_static_allowlist`: the CSP directives, and `/static` serving only `monitor.js` (traversal, NUL and unknown names give 404) |
| Q1, Q2 | Screenshot at 375 px in dark mode: no page-level horizontal scroll, and the waterfall scrolls inside its own box. Status pills carry text, and errors show ✗ and "error". Animations are off under `prefers-reduced-motion`. |

**Found and fixed during build:**
- The first screenshots caught every bar replaying its grow animation, which would have flickered every second while live. Only new rows animate now.
- The "Compare with" select overflowed at phone width. It's capped at 100%.
