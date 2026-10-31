# Increment 9: TypeScript UI (live board) — Spec

**Track:** Standard   **Status:** Built (verify review pending)   **Date:** 2026-10-31   **Product stage:** Introduction (hackathon demo)

## Problem

The UI is server-rendered HTML with almost no JavaScript. Pages reload to update, actions block on full-page form posts, and the look is dated. It doesn't use the stack the hackathon's technical document prescribes for the UI: TypeScript or JavaScript 5.x on Node 20+ as a "thin UI", with FastAPI as the "thin API behind the UI".

## Users

- **The team:** demos it live.
- **Judges and Clay's evaluators:** watch runs, compare models, and read reports.

## Goals

- A TypeScript single-page app in the approved B+C design. It's live: nothing needs a manual reload, actions are asynchronous, and you get pop-up notifications ("toasts") on completion.
- Every current capability is reachable from it: runs, the live monitor, reports, evaluate, compare, batches, import and the receiver, the dashboard, and rubric import.
- The UI stays **thin.** All logic stays in Python behind a JSON API, so the CLI and UI behave identically.

## Non-goals

- New evaluation features. This increment is presentation and API only.
- Authentication or multi-user access. The UI stays a localhost single-user tool.
- Server push (SSE or websockets). Polling is enough on localhost.
- Removing the server-rendered UI in this increment. It moves to `/classic` as a fallback and is sunset later.

## Acceptance criteria

**A: API (FastAPI, `/api`)**
- **A1:** JSON endpoints cover every action and view the current UI has:
  - session
  - runs list and run detail (monitor data, scores, report HTML, rubric info)
  - start a run, evaluate a run
  - compare
  - batches: list, detail, start
  - import a trace, receiver status/start/stop
  - dashboard (with the rubric filter)
  - rubric: active, versions, drafts, draft detail, create draft, update, activate, discard, roll back, version detail, source download
  - job log and job dismiss
  - configs, prompts and models for the forms
- **A2:** Endpoints reuse the existing Python functions. The logic isn't duplicated, and responses carry what the pages show today, as typed JSON.
- **A3, security:** the existing guards apply:
  - the Host check (DNS rebinding)
  - the Origin check, now on every write method (POST, PUT, PATCH, DELETE)
  - CSRF: every `/api` write must carry the `X-CSRF-Token` header (from `GET /api/session`). A custom header can't be sent cross-site without a CORS preflight, which we never grant. A write whose body is JSON must also send `Content-Type: application/json`; multipart is accepted only on the two upload routes. Both rules are enforced in one dependency on the write routes and tested.
  - the path and id validation already in place

  Every must-deny case from the current UI tests is re-asserted against `/api`.
- **A4, errors:** every `/api` problem returns JSON `{"error": "<message>"}` (plus `"field"` for draft validation, U4), with the same status codes the pages use today (400, 403, 404, 409, 413, 502). This includes:
  - body validation errors: 422, rewritten from FastAPI's `{"detail": …}`
  - the guard middleware's Host, Origin and 500 responses on `/api` paths
- **A5, thin aggregates:** `GET /api/runs` returns the KPIs (runs; runs started today; mean overall and scored count over the **active rubric**; **spent over every run plus every evaluation**, matching the mockup's "runs + evaluations"; live, running and evaluating counts), and each card's board column and display fields. `GET /api/jobs` returns live jobs and jobs finished in the last 2 minutes, with their outcome. The UI computes none of these.

**U: UI (TypeScript, React, Vite, Tailwind; approved design B+C)**
- **U1, shell:**
  - a sticky top nav: Runs, Batches, Dashboard, Rubric, Import
  - the active rubric and the AWS profile name in the header. There's no Bedrock "connected" dot: nothing measures reachability, and the UI shows no status it can't back up. This changes the approved mockup slightly and was told to the user.
  - a "New run" button (prompt and config picker in a dialog)
  - a light/dark toggle that follows the system setting by default and remembers your choice
  - toasts: run finished or scored, evaluation done, errors (from `GET /api/jobs` recent completions; each shown once per job id)
- **U2, Runs board:**
  - KPI row: runs, mean overall (active rubric), live now, spent
  - filters: model, prompt, search
  - four live columns: **Running**, **Evaluating**, **Scored** (score ring), **Needs attention** (failed, or not evaluated, with an inline "Evaluate →")

  Cards move between columns without a reload, and new runs appear on their own. The board polls every 2 s while anything is live and every 5 s otherwise, so runs started from the CLI or the receiver appear too. Unreadable runs and incomplete or interrupted evaluations go in **Needs attention**.
- **U3, run drawer:** clicking a card opens a slide-over drawer. It's deep-linkable as `/runs/:id`.
  - **Tabs:**
    - **Overview:** status, clock, gauges, agent bars for pipeline runs, latest steps
    - **Timeline:** the waterfall, with agent bands
    - **Steps:** the full feed with args and results
    - **Report:** the rendered report, or an "Evaluate" action
    - **Compare ↗**
  - **Updates:** about once a second while live. When a run finishes, the drawer switches to the scored state on its own.
- **U4, other screens in the same style:**
  - **Batches:** a list, a start form, and live batch detail with the per-model table and run rows
  - **Compare:** a delta table and both timelines
  - **Dashboard:** KPIs, leaderboard, cost vs score with hover, struggles, and the rubric filter
  - **Rubric:** active rubric and history; import with drag-and-drop and an upload progress bar (XHR, since `fetch` can't report upload progress); asynchronous drafting with a spinner and "drafting…" state; the form review, where the API's validation error is shown next to its field (from `field`, e.g. `criteria.0.levels`); activate and roll back
  - **Import:** trace upload by drag-and-drop, and the receiver toggle with a live count of received runs
- **U5, quality:**
  - works at 375 px with no horizontal page scroll (wide tables scroll inside their cards)
  - keyboard navigable, with visible focus, labelled controls, and status never shown by colour alone
  - honours `prefers-reduced-motion`
  - every view has loading, empty and error states
- **U6, thin:** the app keeps no evaluation logic. It formats and displays API data and calls API actions. Any calculation the UI shows (for example a waterfall geometry) comes from the API or is pure presentation.

**B: build and serve**
- **B1:**
  - **Source:** the app lives in `web/` (TypeScript 5.9). Node `^22.22.2 || >=24.15.0` is the intersection of the pinned Vite 8.3.4 (`^20.19.0 || >=22.12.0`), Vitest 5.0.5 (`^22.12.0 || ^24.0.0 || >=26.0.0`) and jsdom 30.1.3 (`^22.22.2 || ^24.15.0 || >=26.0.0`) `engines` fields; the repo pins 24.22.2 in `.tool-versions`. Versions are exact pins with a committed `package-lock.json`.
  - **Build:** `npm run build` writes to `src/foundry/ui/web_dist/`, which is committed, so `uv run foundry ui` needs no Node.
  - **Serving:** FastAPI serves the built assets and falls back to `index.html` for client routes.
- **B2a, fonts:** the approved look's Inter and JetBrains Mono are self-hosted from `@fontsource-variable/inter` 5.3.0 and `@fontsource/jetbrains-mono` 5.3.0 (both 2026-07-19), with no Google Fonts at runtime.
- **B5, classic UI:** every link, redirect, fetch (`monitor.js`) and helper-built link (the run page's rubric link) in the classic UI carries the `/classic` prefix. Tests re-point to `/classic`.
- **B2:** The CSP becomes `default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; font-src 'self'; form-action 'self'; frame-ancestors 'none'`. Fonts are self-hosted or system fonts: no CDN at runtime.
- **B3:** The old pages remain at `/classic/...`, unchanged and still tested.
- **B4, supply chain:** `npm audit` (or OSV-Scanner on the lockfile) shows no known High or Critical vulnerabilities in the shipped dependencies, and the result is recorded.

**T: tests**
- **T1:** API tests (pytest, through the real FastAPI app) cover each endpoint's success path and its must-deny cases.
- **T2:** UI unit tests (Vitest + Testing Library) cover the board rendering columns from the API's `column` field, the drawer tabs, and the rubric form's field-error display. CI (`.github/workflows/ci.yml`) gains a job: `npm ci`, `npm test`, `npm run build`, then `git diff --exit-code src/foundry/ui/web_dist` to prove the committed bundle matches its source.
- **T3:** A headless-Chromium end-to-end check against the real server:
  1. load the board
  2. start an offline run
  3. see it move Running → Needs attention ("not evaluated")
  4. click Evaluate
  5. see it reach Scored with a ring, with no manual reload

  Screenshots in light and dark, at desktop width and 375 px. T3 runs locally (it needs a headless browser) and its output is recorded in the handoff. It isn't run in CI.

## Success signals

- A demo viewer sees runs appear and move columns, and scores land, with no clicking on reload.

## Constraints

- The TypeScript 5.x and Node 20+ stack the hackathon docs prescribe. No runtime CDN.
- Dependencies are pinned and at least two weeks old (checked on 2026-10-31).

## Assumptions and open questions

1. **Committing the built bundle** keeps `foundry ui` working from a clean clone without Node (A-4 rehearsal). Owner: the team. Default: commit it, and CI rebuilds and diffs it later.
2. **Fonts:** closed. Self-hosted `@fontsource-variable/inter` and `@fontsource/jetbrains-mono` 5.3.0 (B2a).
