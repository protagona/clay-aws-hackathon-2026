# Increment 9: TypeScript UI (live board) — Design

**Spec:** ./spec.md   **Last updated:** 2026-10-31   **Status:** Current (design gate passes 1–2 applied)

## Overview

FastAPI gets a JSON API under `/api` (`foundry.ui.api`). Every read and write the UI performs lives once in `foundry.ui.actions`, and both the classic Jinja routes and `/api` call it. The low-level helpers (`_cards`, `_read_run`, `_scores`, `_spawn`, …) stay in `app.py`, and `actions` reaches them through `_core()`. **There is no `core.py`.**

A React 19 + TypeScript 5.9 SPA (Vite 8, Tailwind 4, React Router 7) in `web/` builds to `src/foundry/ui/web_dist/`. FastAPI serves it at `/`, with an SPA fallback. The old Jinja UI moves under `/classic`. Live behaviour is polling through one small hook, `usePoll(url, ms)`: the board every 2 s while anything is live and every 5 s otherwise; an open live run every 1 s; a finished run once.

## Experience

**Design review: approved by the user (2026-10-31)**
- **Chosen:** "combo between B & C", refined in `mockups/bc-refined.html` (screenshot `mockups/bc-refined.png`).
- **Alternatives shown:** A, a Console (`a-console.html`); B, Product (`b-product.html`); C, a Live board (`c-board.html`).

**Approved layout:**
- a sticky top nav (segmented tabs), with the rubric chip, the AWS profile name (instead of the mockup's Bedrock dot; see the header note), a theme toggle and "▶ New run"
- the KPI row, then filter chips and search
- the four status columns
- a run card click opens a 540 px slide-over drawer with tabs (Overview, Timeline, Steps, Report, Compare ↗). The board shrinks beside it on wide screens. Below 900 px the drawer is a full-screen sheet with a close button, so 375 px has no horizontal scroll (a refinement told to the user).
- KPI sub-lines as in the mockup: "+N today" (runs started today, from `kpis.today`), "N running · N evaluating", "runs + evaluations" (static).
- toasts at the bottom left

**Screen-to-route map:**

| Screen | Route | Data (API) |
|---|---|---|
| Runs board + drawer | `/`, `/runs/:id` | `GET /api/runs` → `{kpis: {runs, today, mean, scored, spent, live, running, evaluating}, runs: [Card], live}`, `GET /api/runs/:id` (poll), `POST /api/runs`, `POST /api/runs/:id/evaluate` |
| Compare | `/compare?a=&b=` | `GET /api/compare?a=&b=` |
| Batches / batch | `/batches`, `/batches/:id` | `GET/POST /api/batches`, `GET /api/batches/:id` (poll while running) |
| Dashboard | `/dashboard?rubric=` | `GET /api/dashboard?rubric=` |
| Rubric | `/rubric`, `/rubric/drafts/:id`, `/rubric/history/:file` | `GET /api/rubric`, `POST /api/rubric/drafts` (multipart), `GET/PUT/DELETE /api/rubric/drafts/:id`, `POST /api/rubric/drafts/:id/activate`, `POST /api/rubric/history/activate`, `GET /api/rubric/history/:file`, `GET /api/rubric/drafts/:id/source` |
| Import | `/import` | `POST /api/import` (multipart), `GET /api/receiver`, `POST /api/receiver/start` and `/stop` |
| Session and meta | — | `GET /api/session` → `{csrf, configs, prompts, models, tasks, rubric: {id, version} \| null, aws_profile: string \| null}` |
| Jobs | toasts | `GET /api/jobs` → `{jobs: [{id, kind, run_id, label, alive, exit_code, failed, message, finished_at}]}`: live jobs, jobs finished within 120 s, and failed undismissed jobs. `POST /api/jobs/:id/dismiss`, `GET /api/jobs/:id/log` |

**States:**
- every screen: skeleton cards while loading, an empty state with a next action, and an error banner with "Retry"
- the drawer: a "Run not found" panel
- the rubric draft: "Drafting…" with a spinner, because the POST takes up to a minute (the request stays open, with a timeout of 120 s)
- the board: a failed start shows as a toast with the error and a "View log" link

## Architecture

| Component | Responsibility |
|---|---|
| `src/foundry/ui/actions.py` (new; replaces the planned `core.py`) | Every read and write the UI performs, as plain functions raising `UiError(status, message)`:<br>• runs: `run_path`, `run_view`, `board_column`, `start_run`, `start_evaluate`, `compare`<br>• `dashboard_view` (including the rubric filter)<br>• batches: `start_batch`, `batch_view`<br>• import and receiver<br>• every rubric operation<br>The classic routes and `/api` both call them, so nothing is duplicated (A2). The closures that lived in `create_app` move here. |
 Pure helpers moved out of `app.py`: `UiState`, `Job`, `_spawn`, `_cards`, `_read_run`, `_scores`, `run_view` (it returns a dict; the monitor rows already come from `monitor.monitor`), `_compare_side` and `_compare_rows`, `_batch_list`/`_batch_rows`/`_variant_stats`, `_rubric_options`/`_rubric_info`, `_configs`/`_prompt_ids`/`_model_choices`/`_task_ids`. `app.py` and `api.py` import them. **No behaviour changes.** The existing tests guard this. |
| `src/foundry/ui/api.py` (new) | `APIRouter(prefix="/api")`. The write dependency `require_write(request)` checks the `X-CSRF-Token` header with `hmac.compare_digest`, and requires `application/json` when the route takes a JSON body (multipart only on the upload routes). `GET /api/runs` returns `{kpis, runs: [Card], live}`. `GET /api/jobs` returns live jobs plus jobs finished within 120 s (outcome, label, run_id). Card fields come from `_cards` plus `column` (`board_column`), `overall`, `workflow`, `output`, `cost`, `agents_mode`, and `pipeline_progress` (the agent names done so far, when live). Draft update returns `{valid, error, field}`. Each endpoint calls core helpers and returns JSON-safe dicts (Step objects become `model_dump()`). Write endpoints take JSON bodies (pydantic models) or multipart for uploads, check `X-CSRF-Token`, and return errors as `{"error"}` with status. Run start, evaluate and batch keep using `_spawn` (subprocesses), as now. |
| `src/foundry/ui/app.py` | The Origin check now covers POST, PUT, PATCH and DELETE. For `/api` paths, the guard returns JSON `{"error"}`. The CSP is as in B2. Exception handlers turn `RequestValidationError` into 422 `{"error"}` on `/api`. All classic links, redirects (`go()`), `monitor.js` and `_rubric_info`'s link use the `/classic` prefix (B5). Jinja routes move under `/classic` (an `APIRouter(prefix="/classic")`), and internal links in the templates get the prefix. It mounts `/api`, serves `/assets/*` from `web_dist/assets`, and falls back to `index.html` for client routes (anything not `/api`, `/classic` or `/assets`). If the bundle is missing, `/` says "UI bundle not built; run `npm --prefix web run build`" and links to `/classic`. |
| `web/` | `package.json` (exact versions), `vite.config.ts` (builds to `../src/foundry/ui/web_dist`, with a dev proxy from `/api` to `127.0.0.1:8765`), `src/`:<br>• `api.ts`: a typed client that fetches the session once and adds `X-CSRF-Token`<br>• `usePoll.ts`<br>• `types.ts`: the response types<br>• `components/`: Shell, KpiRow, Board, RunCard, ScoreRing, Drawer, Gauges, Waterfall, StepFeed, AgentBars, Toasts, ThemeToggle, Dialog, Dropzone, Spinner<br>• `pages/`: Runs, Compare, Batches, Batch, Dashboard, Rubric, RubricDraft, RubricVersion, Import<br>• `main.tsx` |
| Tests | `tests/test_api.py` (pytest through the real app), `web/src/**/*.test.tsx` (Vitest + Testing Library), and the end-to-end script `scripts/ui_e2e.mjs` (Node CDP driving the Playwright-cached headless shell, with no npm dependencies). |

## Data and contracts

The JSON shapes are defined once in `web/src/types.ts`, mirroring the `api.py` return dicts. The API is internal to this app and not a public contract. Two shapes as examples:
- `GET /api/runs` → `{kpis: {runs, today, mean, scored, spent, live, running, evaluating}, runs: Card[], live}`. `mean` and `scored` cover the active rubric; `spent` covers every run and evaluation (`actions.board_kpis`).
- `Card = {run_id, prompt_id, model, status, headline, column, unreadable?}` plus optional `sub, when, started_at, overall, workflow, output, cost, agents_mode, agents_started`. Unreadable runs carry only the required fields, with `unreadable: true` and `column: "attention"`. `agents_started` lists the agents a **live pipeline run** has started so far, read from its trace; it's empty otherwise.
- `GET /api/receiver` → `{running, port, endpoint, count, received}`: `count` is the total and `received` the last 20. **The column is computed server-side**, in `actions.board_column(card)`; unreadable runs and incomplete evaluations land in "attention". The UI computes nothing (U6).
- `GET /api/runs/:id` → `{run, scores, report_html, rubric_info, evaluating, interrupted, m}`, where `m` is the existing `monitor()` dict with steps serialised (key kept as `m`, matching `actions.run_view`). The feed's model text is rendered to `text_html` by `foundry.ui.render`.
- **Every rubric, import and receiver endpoint takes `task`:** as a query parameter on GETs and DELETE, a field in JSON bodies, or a form field in multipart. It defaults to `adb` and is validated against `tasks` from the session (unknown → 404).

**Header (a change from the approved mockup):** the "Bedrock" dot is replaced by the AWS profile name (`AWS_PROFILE`). Nothing measures Bedrock reachability, and project memory says availability only comes from real calls. The user was told.

## Decisions

### ADR-1: React + Vite + TypeScript SPA over a FastAPI JSON API
- **Options:** A) a React SPA; B) htmx + Alpine on the Jinja pages; C) vanilla JS.
- **Decision:** A, chosen by the user after reading the hackathon docs.
- **Why:** the docs prescribe TypeScript/JavaScript 5.x on Node 20+ for the "thin UI", with FastAPI as the thin API (`Clay-Foundry-Technical-Hackathon.md`, Programming Languages and Preferred Libraries). A also gives the most app-like feel.
- **Reversibility:** easy. The old UI stays at `/classic`.

### ADR-2: Commit the built bundle
- **Why:** a clean clone runs `foundry ui` without Node, which the A-4 deploy rehearsal requires.
- **Trade-off:** the diffs are noisy, so the bundle is rebuilt only in UI commits.

### ADR-3: Polling, not SSE
- **Why:** localhost, a single user, and the endpoints are already cheap. The 1–2 s cadence matches today's monitor.
- **Reversibility:** easy, since everything goes through `usePoll`.

### ADR-5: Fonts are self-hosted
- **What:** `@fontsource-variable/inter` 5.3.0 and `@fontsource/jetbrains-mono` 5.3.0, bundled by Vite, so B2's `font-src 'self'` holds and the approved look is kept.
- **No inlining:** Vite inlines assets under `build.assetsInlineLimit` (4 KB by default) as `data:` URIs. Some woff2 subsets are smaller than that, and `font-src 'self'` blocks `data:`. So `vite.config.ts` sets `assetsInlineLimit: (file) => !/\.(woff2?|ttf|otf)$/.test(file) && undefined`, so fonts are always emitted as files. A test checks that the built CSS has no `data:font` URLs.

### ADR-6: CI checks the bundle
- **What:** the CI job runs `npm ci && npm test && npm run build && git diff --exit-code src/foundry/ui/web_dist`, so a committed bundle that doesn't match its source fails CI. CI hasn't run yet (GitHub billing); the job is added anyway.

### ADR-4: Pinned dependencies, at least two weeks old
- **The pins (verified on the npm registry, 2026-10-31):** react/react-dom 19.3.1, react-router-dom 7.18.4, vite 8.3.4, @vitejs/plugin-react 6.1.2, typescript 5.9.3, tailwindcss and @tailwindcss/vite 4.3.3, @types/react and @types/react-dom 19.3.0, vitest 5.0.5, @testing-library/react 16.3.3, jsdom 30.1.3.
- **TypeScript stays on 5.9.3** rather than 7.x, because the docs say 5.x.

## Failure modes

| Failure | Behaviour |
|---|---|
| The API is down or polling fails | An inline "reconnecting…" pill, with backoff to 8 s; the data stays on screen |
| A write fails (4xx/5xx) | A toast showing the API's `error` message; the form keeps its values |
| The bundle is missing | The `/` fallback message links to `/classic` |
| The CSRF token has rotated (server restart) | A 403 makes the client re-fetch the session and retry once |

## Security and privacy

- **Same trust model as today:** the server binds to 127.0.0.1 and checks Host and Origin. Writes require the CSRF header and JSON or multipart (B1). The API adds no capability the pages don't already have.
- **Autoescaping:** React escapes by default. There are exactly two `dangerouslySetInnerHTML` sinks: `report_html` and the step feed's `text_html`. Both are produced only by `foundry.ui.render.render` (no raw HTML, no images, http(s) and relative links only).
- **CSP:** B2 keeps `script-src 'self'`, with no inline scripts (Vite builds external modules).
- **Supply chain:** exact pins and a lockfile, `npm audit`/OSV recorded, and no install scripts run beyond the packages' own.

## Performance

`GET /api/runs` reads every `run.json` and `scores.json`, plus each trace once for the spend KPI. It's measured in the Verify stage and recorded in the handoff. Polling at 2 s is fine. The bundle should stay under 300 KB gzipped.

## Migration and rollback

The new UI is at `/`, and the old one at `/classic`. A rollback is a revert, or simply using `/classic`. The CLI is untouched.

## Traceability

| AC | Component | Tests |
|---|---|---|
| A1, A2 | `api.py` → `actions.py` | `test_api.py`: one test per endpoint group (session, runs list and detail, start and evaluate, compare, dashboard, batches, import, receiver, rubric create, update, activate, discard, roll back and source, jobs) |
| A3 | `require_write`, guard middleware, `actions` validation | `test_api.py`:<br>• `::test_writes_need_csrf_header_and_json`<br>• `::test_origin_and_host_checked_on_every_write`<br>• `::test_must_deny_paths`: bad run ids (404), batch traversal (404), unknown compare side (404), unknown task (404 for rubric routes, 400 for import and receiver, as the classic UI does), bad draft ids and history names (404), an import over the cap (413), and no server paths in import errors |
| A4 | `install()` handlers, guard | `test_api.py::test_errors_are_json` (422 rewrite, 404, 403, 400 Host) |
| A5 | `api.runs`, `api.jobs` | `test_api.py::test_runs_kpis_and_columns`, `::test_spent_counts_every_run_mean_only_active_rubric`, the jobs assertions in `::test_start_run_and_evaluate_through_api` |
| B1, B3, B5 | `mount_spa`, `/classic` prefix | `test_api.py::test_spa_served_and_fallback`, and the existing `test_ui.py`/`test_dashboard.py` running against `/classic` through `classic_client` (redirects, `monitor.js` path, rubric link) |
| B2, B2a | middleware CSP, `vite.config.ts` | `test_api.py::test_csp_and_bundle` |
| B4 | lockfile | `npm audit` output, recorded |
| U1–U6 | `web/src` | Vitest (`web/src/ui.test.tsx`): the board (columns from the API field, the empty column), the drawer tabs, the rubric form (field error next to its field), the cadence test (board 2 s/5 s, run 1 s then 4 s) and the toast test (one per job id), all in `web/src/ui.test.tsx` |
| U5 | — | screenshots at 375 px and desktop, light and dark; `prefers-reduced-motion` checked by emulating it over CDP in `scripts/ui_e2e.mjs` |
| T2 (CI) | `.github/workflows/ci.yml` web job | runs in CI once billing is fixed |
| T3 | `scripts/ui_e2e.mjs` | local; needs the Playwright-cached `chrome-headless-shell` (found under `~/Library/Caches/ms-playwright`). Without it the script exits with "no headless browser found", and the handoff says T3 wasn't run. |

## Risks and spikes

- **Riskiest:** the size of the rebuild (25 routes). It's mitigated by moving shared logic into `actions.py` first (the existing suite guards it, now against `/classic`) and building the API before any UI.
- **Second:** npm installs in this environment. A spike installs and builds a hello-world first.

## Implementation plan

1. **Spike:** scaffold `web/` with the pinned deps, run `npm ci`, `vite build` and a hello world served by FastAPI. Record `npm audit`.
2. **`actions.py` + `/classic` + `api.py`:** the classic routes call `actions` (no behaviour change; the existing tests pass against `/classic`), the API covers every endpoint, plus SPA serving, the CSP, JSON errors and `test_api.py`. One commit, since `app.py` imports `api.py`.
4. **UI shell:** the api client, `usePoll`, Shell, toasts, theme, the Runs board, and the drawer (all tabs). Vitest. *Commit.*
5. **UI screens:** Compare, Batches, Dashboard, Rubric (dropzone, async draft, form), Import. *Commit.*
6. **Verify:** the e2e script, screenshots in both themes and widths, the full suite, the audit, the docs and the handoff. *Commit and push.*
