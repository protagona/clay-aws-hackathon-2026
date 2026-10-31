# Increment 9: TypeScript UI (live board) — Handoff

**Spec:** ./spec.md   **Design:** ./design.md   **Date:** 2026-10-31   **Track:** Standard

## What shipped

- **A TypeScript SPA** (React 19.3.1, Vite 8.3.4, Tailwind 4.3.3, TypeScript 5.9.3), built in the approved B+C design. The user picked "combo between B & C"; the mock is `mockups/bc-refined.html`.
  - **Live board:** Running, Evaluating, Scored and Needs attention.
  - **Run drawer:** a slide-over with Overview, Timeline, Steps and Report tabs.
  - **Notifications and theme:** pop-ups when work finishes, and light or dark mode.
  - **Every screen:** batches, dashboard, rubric (drag-and-drop, background drafting, field-level errors), import (drag-and-drop with progress, a live receiver count) and compare.
- **A FastAPI JSON API** (`/api`, `src/foundry/ui/api.py`). Every read and write lives once in `src/foundry/ui/actions.py`, shared with the classic pages.
- **The classic UI** moved to `/classic`, unchanged and still tested.
- **The built bundle** is committed (`src/foundry/ui/web_dist/`, 752 KB including fonts), so `foundry ui` needs no Node.

## Decisions

- **The user chose:**
  - React over htmx or vanilla JS, after checking the hackathon docs, which prescribe "TypeScript / JavaScript 5.x, Node 20+, for the thin UI" with "FastAPI as the thin API"
  - the B+C design
- **Changes to the approved mock** (told to the user):
  - The header shows the AWS profile name instead of a "Bedrock connected" dot, because nothing measures reachability.
  - Below 900 px the drawer becomes a full-screen sheet.
  - With the drawer open, the board drops to two columns.
- **The design gate ran 3 subagent passes, the maximum.** The third pass's blockers were resolved with defaults (spec status line):
  - **"Spent"** covers every run plus every evaluation, matching the mock's "runs + evaluations". **This is a judgement call put to the user.**
  - The mean and scored count cover the active rubric.
  - Pipeline progress comes from the agents a live run has started, read from its trace.
  - The receiver reports a total count.
- **CSRF for the API:** an `X-CSRF-Token` header on every write. A custom header forces a CORS preflight, which is never granted. JSON bodies are enforced (415 otherwise). Upload routes take multipart or urlencoded.
- **Found and fixed along the way:**
  - The import error message leaked the server's temporary upload path. It now shows only the file name, and a test covers it.
  - Vite would have inlined small font files as `data:` URIs, which `font-src 'self'` blocks. Fonts are always files now, and a test checks it.

## Verification

**Python suite:** 317 passed; ruff and gitleaks clean. **UI unit tests (Vitest):** 6 passed. **`npm audit`:** 0 vulnerabilities, for both the shipped and the dev dependencies.

| AC | How it was proven |
|---|---|
| A1, A2 | `tests/test_api.py` has 13 tests through the real app. They cover session, runs and KPIs, run detail, compare, dashboard, batches, receiver, import, the full rubric flow (draft by JSON shortcut, update with field errors, activate, roll back, source, discard), and jobs. `test_start_run_and_evaluate_through_api` runs a real offline run plus evaluation as subprocesses. The classic routes call the same `actions` functions: the existing 67 classic tests pass through `classic_client`. |
| A3 | `test_writes_need_csrf_header_and_json` (403 without the header or with a bad one; 415 for a non-JSON body; multipart required on upload routes), `test_origin_and_host_checked_on_every_write` (POST, PUT and DELETE refused cross-origin; bad Host refused), `test_must_deny_paths` (bad run ids, traversal, unknown task, bad draft ids and history names, an over-cap import returning 413, no server paths in errors) |
| A4 | `test_errors_are_json`: the 422 rewrite `{"error": "config: Field required"}`, an unknown endpoint, 400s, and the guard's JSON 400 and 403 |
| A5 | `test_runs_kpis_and_columns` (columns: scored, running, attention, and unreadable to attention), `test_spent_counts_every_run_mean_only_active_rubric`, and jobs with `finished_at` in the run-and-evaluate test. **`GET /api/runs` takes 17 ms on the 29 real runs**, measured on 2026-10-31. |
| B1, B3, B5 | `test_spa_served_and_fallback`: the SPA routes and `index.html` fallback, `/classic` 307 to `/classic/`, assets served as immutable files. The classic tests assert the `/classic` links, redirects, `monitor.js` path and rubric link. |
| B2, B2a | `test_csp_and_bundle`: the CSP directives, no inline scripts in `index.html`, and no `data:` fonts in the CSS. 13 woff2 files were emitted. |
| B4 | `npm audit`: 0 vulnerabilities. Every pin is at least two weeks old (registry dates checked 2026-10-31; see design ADR-4). |
| U1–U6 | Vitest (`web/src/ui.test.tsx`):<br>• the board renders the API's column and offers Evaluate only where it's needed<br>• the polling cadence is 2 s, 5 s and 1 s by state<br>• each finished job gets one toast<br>• a field error is shown inside its own criterion |
| T3 | **`scripts/ui_e2e.mjs`, live against the real server** (headless Chromium over CDP):<br>1. Opened the board.<br>2. Clicked **▶ New run** and chose the offline config. The page navigated to the new run's drawer.<br>3. The card appeared in **Needs attention** ("not evaluated").<br>4. Clicked **Evaluate →**. The card reached **Scored** with "overall .83".<br>5. The page never reloaded (a `window` marker survived).<br>6. Screenshots: board, drawer, dashboard and rubric, in light and dark, at 1440 and 375 px. No horizontal page scroll anywhere. With `prefers-reduced-motion: reduce` emulated, animations are `none`. **PASS.** |
| U5 | The screenshots are in [`screenshots/`](screenshots/): board and drawer at desktop in both themes, phone-width board and drawer, the dashboard, and the rubric. |

**Compared with the approved B+C mock:** the shell, KPI row with sub-lines, filter chips, four status columns, score rings, slide-over drawer with tabs, agent bars, latest steps and toasts all match. The differences are the three listed under Decisions.

**Exercised live:** yes. The real `foundry ui` served the built bundle to headless Chromium, and an offline run was started and evaluated through the UI end to end.

## Deferred and follow-ups

- **CI:** the new `web` job (`npm ci`, test, build, bundle diff) runs once GitHub billing is fixed.
- **The classic UI at `/classic`:** sunset it in a later increment once the new UI has been used at the event.
- **Pinning:** the CI actions are still pinned by tag (F-013).
